"""The client side of a LAN share: talk to the host instead of opening a file.

``RemoteDatabase`` mirrors the surface of :class:`database.Database` — the same
``execute`` / ``all`` / ``one`` / ``rows`` / ``insert`` / ``update`` /
``transaction`` members — so every caller in the program keeps working unchanged.
``rows``, ``insert`` and ``update`` reuse the base class logic and only ``execute``
travels over the network, which is why the two remain in step.
"""
from __future__ import annotations
import contextlib
import secrets
import socket
import threading

from database import COLUMNS, SCHEMA
from service import BusinessError
from share_protocol import (
    CONNECT_TIMEOUT, DEFAULT_PORT, PROTOCOL, REQUEST_TIMEOUT, ShareError,
    batches, proof, recv_frame, send_frame,
)


class LanShareError(BusinessError):
    """A LAN share problem whose message should be shown to the user as-is."""


def _offline(host, port, detail=''):
    text = '无法访问主机电脑 %s:%d 上的共享数据。请确认：\n① 主机电脑已开机，且锦纷资产管理系统正在运行；\n② 主机已开启局域网共享，端口一致，且本机填写的地址正确（局域网 IP 或公网 IP）；\n③ 两台电脑网络互通：同一局域网，或主机已开通公网访问并在路由器上把端口映射到主机；\n④ 主机防火墙已放行该端口。' % (host, port)
    if detail:
        text += '\n\n详细信息：%s' % detail
    return text


def _connect(host, port, timeout):
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
    except OSError as exc:
        raise LanShareError(_offline(host, port, exc))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def _handshake(sock, password, client_name, timeout):
    """Prove we know the access password without ever putting it on the wire."""
    sock.settimeout(timeout)
    try:
        send_frame(sock, {'op': 'hello', 'protocol': PROTOCOL, 'client': client_name})
        reply = recv_frame(sock)
    except (OSError, ShareError) as exc:
        raise LanShareError('与主机电脑握手失败：%s' % exc)
    if not isinstance(reply, dict) or not reply.get('ok'):
        message = reply.get('error') if isinstance(reply, dict) else None
        raise LanShareError(str(message or '主机电脑拒绝连接'))
    if int(reply.get('protocol', 0) or 0) != PROTOCOL:
        raise LanShareError('客户端与主机的共享版本不一致，请在两台电脑上使用同一版本软件')
    if reply.get('auth'):
        nonce = secrets.token_hex(16)
        try:
            send_frame(sock, {'op': 'auth', 'nonce': nonce, 'proof': proof(password, nonce, str(reply.get('server_nonce') or ''))})
            answer = recv_frame(sock)
        except (OSError, ShareError) as exc:
            raise LanShareError('与主机电脑认证失败：%s' % exc)
        if not isinstance(answer, dict) or not answer.get('ok'):
            message = answer.get('error') if isinstance(answer, dict) else None
            raise LanShareError(str(message or '访问口令不正确'))
    return str(reply.get('name') or '')


def test_connection(config, timeout=None):
    """Handshake once and report the host's name, or raise with a readable reason."""
    host = str(config.get('host') or '').strip()
    if not host:
        raise LanShareError('请填写主机电脑的局域网地址')
    port = int(config.get('port') or DEFAULT_PORT)
    if not 1 <= port <= 65535:
        raise LanShareError('共享端口无效，请填写 1～65535 之间的端口')
    wait = float(timeout or CONNECT_TIMEOUT)
    sock = _connect(host, port, wait)
    try:
        name = _handshake(sock, str(config.get('password') or ''), socket.gethostname(), wait)
        sock.settimeout(wait)
        send_frame(sock, {'op': 'ping'})
        recv_frame(sock)
        return name or host
    finally:
        try:
            sock.close()
        except OSError:
            pass


class RemoteDatabase:
    """Database-compatible façade for a client of another computer's share."""

    def __init__(self, config=None):
        self.config = dict(config or {})
        self.pg = False
        self.shared = True
        self.host = str(self.config.get('host') or '').strip()
        self.port = int(self.config.get('port') or DEFAULT_PORT)
        self.password = str(self.config.get('password') or '')
        self.client_name = socket.gethostname()
        self.server_name = ''
        self.on_disconnect = None
        # Re-entrant so the thread that owns a transaction can keep issuing
        # statements; other threads wait for it to finish.  See ``transaction``.
        self._lock = threading.RLock()
        self._local = threading.local()
        self._socket = None
        if not self.host:
            raise LanShareError('请填写主机电脑的局域网地址')
        self.connect()

    @property
    def inside(self):
        """True while *this thread* is inside a transaction."""
        return bool(getattr(self._local, 'inside', False))

    @inside.setter
    def inside(self, value):
        self._local.inside = bool(value)

    # ------------------------------------------------------------ connection

    def connect(self):
        sock = _connect(self.host, self.port, CONNECT_TIMEOUT)
        try:
            self.server_name = _handshake(sock, self.password, self.client_name, CONNECT_TIMEOUT)
        except Exception:
            try:
                sock.close()
            except OSError:
                pass
            raise
        sock.settimeout(REQUEST_TIMEOUT)
        self._socket = sock
        return self.server_name

    def describe(self):
        if not self.host:
            return '局域网共享（未配置）'
        where = self.host + ':' + str(self.port)
        return '局域网共享 · 主机 %s%s' % (self.server_name + ' ' if self.server_name else '', where)

    def close(self):
        sock, self._socket = self._socket, None
        self.inside = False
        if sock is None:
            return
        try:
            send_frame(sock, {'op': 'bye'})
        except Exception:  # noqa: BLE001 - saying goodbye is best effort
            pass
        try:
            sock.close()
        except OSError:
            pass

    def _drop(self, detail=''):
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if callable(self.on_disconnect):
            try:
                self.on_disconnect(detail)
            except Exception:  # noqa: BLE001 - a status hook must never break a request
                pass

    # ------------------------------------------------------------ wire

    def _check(self, payload):
        if not isinstance(payload, dict):
            raise LanShareError('主机电脑返回了无法识别的数据')
        if payload.get('ok'):
            return payload
        raise LanShareError(str(payload.get('error') or '共享请求未完成'))

    def _ready(self):
        if self._socket is not None:
            return
        if self.inside:
            raise LanShareError('与主机电脑的连接已断开，当前这项操作没有完成，请重新操作。')
        try:
            self.connect()
        except LanShareError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LanShareError(_offline(self.host, self.port, exc))

    def _exchange(self, request):
        """Send one request and read every frame of its answer."""
        self._ready()
        try:
            send_frame(self._socket, request)
            payload = recv_frame(self._socket)
        except (OSError, ShareError) as exc:
            self._drop(str(exc))
            raise LanShareError(_offline(self.host, self.port, exc))
        body = self._check(payload)
        rows = body.get('rows')
        while body.get('more'):
            rows = list(rows or [])
            try:
                body = self._check(recv_frame(self._socket))
            except (OSError, ShareError) as exc:
                self._drop(str(exc))
                raise LanShareError(_offline(self.host, self.port, exc))
            rows.extend(body.get('rows') or [])
        return rows

    def _request(self, request):
        with self._lock:
            return self._exchange(request)

    # ------------------------------------------------------------ statements

    def execute(self, sql, params=()):
        self._request({'op': 'exec', 'sql': sql, 'params': list(params), 'fetch': 'none'})
        return None

    def all(self, sql, params=()):
        return self._request({'op': 'exec', 'sql': sql, 'params': list(params), 'fetch': 'all'}) or []

    def one(self, sql, params=()):
        rows = self._request({'op': 'exec', 'sql': sql, 'params': list(params), 'fetch': 'one'}) or []
        return rows[0] if rows else None

    def rows(self, table):
        if table not in SCHEMA:
            raise ValueError('未知数据表')
        return self.all('SELECT * FROM ' + table)

    def insert(self, table, record):
        self.insert_many(table, [record])

    def insert_many(self, table, records):
        """Send a whole table in a few frames; a restore costs one request each."""
        records = list(records)
        if not records:
            return
        if table not in SCHEMA or any(set(r) != set(COLUMNS[table]) for r in records):
            raise ValueError('数据字段不匹配：' + str(table))
        for chunk in batches(records):
            self._request({'op': 'bulk', 'table': table, 'records': chunk})

    def update(self, table, id, fields):
        if table not in SCHEMA or not fields or not set(fields) <= set(COLUMNS[table]) or 'id' in fields:
            raise ValueError('更新字段不匹配')
        self.execute('UPDATE ' + table + ' SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?', [*fields.values(), id])

    # ------------------------------------------------------------ transactions

    @contextlib.contextmanager
    def transaction(self, bootstrap=False):
        if self.inside:
            raise RuntimeError('不允许嵌套事务')
        self._lock.acquire()
        self.inside = True
        begun = False
        try:
            self._exchange({'op': 'begin'})
            begun = True
            yield
            self._exchange({'op': 'commit'})
            begun = False
        except BaseException:
            if begun:
                try:
                    self._exchange({'op': 'rollback'})
                except Exception:  # noqa: BLE001 - the original failure matters more
                    pass
            raise
        finally:
            self.inside = False
            self._lock.release()
