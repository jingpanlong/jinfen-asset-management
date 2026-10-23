"""The host side of a LAN share: one TCP listener, one worker thread per client.

Only this process ever touches the database file.  A client asks the host to run
a parameterised statement; the host screens it (:mod:`share_protocol`), runs it on
the shared connection and sends the rows back.  ``Database`` already serialises
every statement behind one process-wide lock, so remote clients, the host's own
window and a client's multi-statement transaction all queue up instead of
corrupting each other.
"""
from __future__ import annotations
import secrets
import socket
import threading
import time
import traceback

from database import COLUMNS, SCHEMA, StorageBusy
from share_protocol import (
    AUTH_BAN_SECONDS, AUTH_FAIL_LIMIT, CHUNK_BYTES, DEFAULT_PORT, HELLO_TIMEOUT,
    IDLE_TIMEOUT, MAX_BATCH, MAX_STATEMENTS, PROTOCOL, TRANSACTION_TIMEOUT,
    ShareError, is_lan_address, local_addresses, recv_frame, row_cost, screen,
    send_frame, verify,
)

FETCH_NONE, FETCH_ONE, FETCH_ALL = 'none', 'one', 'all'
KNOWN_OPS = {'hello', 'auth', 'exec', 'bulk', 'begin', 'commit', 'rollback', 'ping', 'bye'}


class _Reported(ShareError):
    """A failure whose frame has already gone out; the loop must not send another."""


def _readable(exc):
    """Turn a storage exception into something worth putting in a dialog."""
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if 'database is locked' in lowered or 'lock timeout' in lowered or 'deadlock' in lowered or 'busy' in lowered:
        return '另一台电脑正在写入共享数据，请稍后重试。'
    return text


class _Session(threading.Thread):
    """One connected client, served on its own thread."""

    def __init__(self, server, conn, peer):
        super().__init__(name='jinfen-share-%s:%s' % (peer[0], peer[1]), daemon=True)
        self.server = server
        self.db = server.db
        self.conn = conn
        self.peer = peer
        # Re-entrant: a failing statement rolls its transaction back from inside
        # the work block, so the same thread takes this lock twice.
        self.work_lock = threading.RLock()
        self.transaction = None
        self.statements = 0
        self.client_name = ''
        self.ready = False
        self.opened_at = time.time()
        self.last_seen = time.time()
        self.identifier = 0

    # ------------------------------------------------------------ plumbing

    def log(self, message):
        self.server._log('%s：%s' % (self.peer[0], message))

    def shutdown(self):
        # shutdown() alone does not reliably wake a recv() that has a timeout on
        # Windows, so the handle is closed too: the blocked thread then fails at
        # once instead of sitting there until the idle timeout expires.
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.conn.close()
        except OSError:
            pass

    def describe(self):
        return {
            'id': self.identifier,
            'address': self.peer[0],
            'port': self.peer[1],
            'client': self.client_name or '未知电脑',
            'since': self.opened_at,
            'seen': self.last_seen,
            'statements': self.statements,
            'in_transaction': self.transaction is not None,
        }

    def _ok(self, extra=None):
        payload = {'ok': True}
        if extra:
            payload.update(extra)
        send_frame(self.conn, payload)

    def _fail(self, message, kind='business'):
        try:
            send_frame(self.conn, {'ok': False, 'error': message, 'kind': kind})
        except (OSError, ShareError):
            pass

    # ------------------------------------------------------------ lifecycle

    def run(self):
        try:
            self.conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.conn.settimeout(HELLO_TIMEOUT)
            self.handshake()
            self.conn.settimeout(IDLE_TIMEOUT)
            while not self.server.stopping:
                request = recv_frame(self.conn)
                self.last_seen = time.time()
                self.handle(request)
        except (ConnectionError, socket.timeout, TimeoutError) as exc:
            if not self.server.stopping:
                self.log('连接结束（%s）' % (exc or '对方已关闭'))
        except ShareError as exc:
            if not self.server.stopping and not isinstance(exc, _Reported):
                self._fail(str(exc), exc.kind)
            if not self.server.stopping:
                self.log('会话中止：%s' % exc)
        except OSError as exc:
            if not self.server.stopping:
                self.log('网络中断：%s' % exc)
        except Exception:
            self.log('会话异常：' + traceback.format_exc(limit=4).replace('\n', ' | '))
        finally:
            self.rollback()
            self.server._forget(self)
            try:
                self.conn.close()
            except OSError:
                pass

    def handshake(self):
        hello = recv_frame(self.conn)
        # The ban check sits after the hello: refusing instantly would reset
        # the connection before the client could read the explanation.
        if self.server._blocked(self.peer[0]):
            self._fail('该地址因多次口令错误已被暂停连接，请稍后再试', 'auth')
            raise _Reported('地址已被暂停')
        if not isinstance(hello, dict) or hello.get('op') != 'hello':
            self._fail('握手失败：对方不是本软件的客户端', 'protocol')
            raise _Reported('握手失败')
        if int(hello.get('protocol', 0) or 0) != PROTOCOL:
            self._fail('客户端与主机的共享版本不一致，请在两台电脑上使用同一版本软件', 'protocol')
            raise _Reported('协议版本不一致')
        self.client_name = str(hello.get('client') or '')[:64]
        nonce = secrets.token_hex(16)
        self._ok({'protocol': PROTOCOL, 'server_nonce': nonce, 'auth': bool(self.server.password), 'name': self.server.name})
        if self.server.password:
            self.conn.settimeout(HELLO_TIMEOUT)
            answer = recv_frame(self.conn)
            if not isinstance(answer, dict) or answer.get('op') != 'auth':
                self._fail('认证请求格式不正确', 'auth')
                raise _Reported('认证失败')
            if not verify(self.server.password, str(answer.get('nonce') or ''), nonce, answer.get('proof')):
                self.server._bump('rejected')
                self.server._fail_auth(self.peer[0])
                time.sleep(0.6)  # slow down anyone guessing
                self._fail('访问口令不正确，请向管理员确认', 'auth')
                self.log('访问口令校验失败（客户端：%s）' % (self.client_name or '未知'))
                raise _Reported('访问口令不正确')
            # The client is waiting for this confirmation before it starts asking.
            self._ok({'protocol': PROTOCOL, 'name': self.server.name, 'auth': True})
        self.ready = True
        self.opened_at = self.last_seen = time.time()
        self.log('已连接（客户端：%s）' % (self.client_name or '未知电脑'))

    # ------------------------------------------------------------ requests

    def handle(self, request):
        if not isinstance(request, dict):
            raise ShareError('请求格式不正确')
        op = request.get('op')
        if op not in KNOWN_OPS:
            raise ShareError('不支持的共享操作：%s' % op)
        if op in ('hello', 'auth'):
            raise ShareError('重复的握手请求')
        try:
            self.dispatch(request, op)
        except ShareError as exc:
            if isinstance(exc, _Reported):
                raise
            # A refused statement is the client's problem, not a reason to hang up.
            self._fail(str(exc), exc.kind)

    def dispatch(self, request, op):
        if op == 'ping':
            self._ok({'time': time.time(), 'name': self.server.name})
            return
        if op == 'bye':
            try:
                self._ok()
            except (OSError, ShareError):
                pass
            raise ConnectionError('客户端主动断开')
        if op == 'begin':
            self.begin()
            return
        if op == 'commit':
            self.commit()
            return
        if op == 'rollback':
            self.rollback(ack=True)
            return
        if op == 'bulk':
            self.bulk(request)
            return
        self.execute(request)

    def run_work(self, work):
        """Run a piece of database work under the storage lock.

        Returns ``False`` when the failure has already been reported to the
        client, so the caller must not send a second frame.
        """
        inside = self.transaction is not None
        with self.work_lock:
            self.statements += 1
            self.server._bump('statements')
            if inside and self.statements > MAX_STATEMENTS:
                raise ShareError('单个事务的语句数超过上限，请拆分后重试')
            if not inside:
                try:
                    self.db.guard()
                except StorageBusy as exc:
                    self._fail(str(exc), 'busy')
                    return False
            try:
                work()
            except Exception as exc:  # noqa: BLE001 - reported back verbatim
                if inside:
                    # The transaction is gone either way; the client is told why.
                    self.rollback()
                    raise ShareError(_readable(exc))
                self._fail(_readable(exc), 'business')
                return False
            finally:
                if not inside:
                    self.db.release()
        return True

    def execute(self, request):
        sql = screen(request.get('sql'))
        params = request.get('params') or []
        if not isinstance(params, (list, tuple)):
            raise ShareError('参数格式不正确')
        fetch = request.get('fetch') or FETCH_NONE
        if fetch not in (FETCH_NONE, FETCH_ONE, FETCH_ALL):
            raise ShareError('不支持的读取方式')
        collected = {}

        def work():
            cursor = self.db.execute(sql, list(params))
            if fetch == FETCH_NONE:
                return
            if fetch == FETCH_ONE:
                row = cursor.fetchone()
                collected['rows'] = [dict(row)] if row else []
            else:
                collected['rows'] = [dict(r) for r in cursor.fetchall()]

        if not self.run_work(work):
            return
        try:
            self.send_rows(collected.get('rows'))
        except ShareError as exc:
            self._fail(str(exc), exc.kind)

    def bulk(self, request):
        table = request.get('table')
        records = request.get('records')
        if table not in SCHEMA:
            raise ShareError('共享模式不允许访问该数据表：%s' % table)
        if not isinstance(records, list) or not records:
            raise ShareError('批量写入的内容为空')
        if len(records) > MAX_BATCH:
            raise ShareError('单次批量写入的记录过多，请分批处理')
        for record in records:
            if not isinstance(record, dict) or set(record) != set(COLUMNS[table]):
                raise ShareError('数据字段不匹配：' + str(table))
        if not self.run_work(lambda: self.db.insert_many(table, records)):
            return
        self._ok({'rows': None, 'count': len(records)})

    def send_rows(self, rows):
        if rows is None:
            send_frame(self.conn, {'ok': True, 'rows': None, 'more': False})
            return
        total = len(rows)
        if not total:
            send_frame(self.conn, {'ok': True, 'rows': [], 'more': False})
            return
        index = 0
        while index < total:
            # Keep each frame under the cap even when a single row carries a photo.
            chunk, cost = [], 0
            while index < total:
                size = row_cost(rows[index])
                if chunk and cost + size >= CHUNK_BYTES:
                    break
                cost += size
                chunk.append(rows[index])
                index += 1
            send_frame(self.conn, {'ok': True, 'rows': chunk, 'more': index < total})

    # ------------------------------------------------------------ transactions

    def begin(self):
        if self.transaction is not None:
            raise ShareError('已有未提交的事务')
        with self.work_lock:
            self.statements = 0
            manager = self.db.transaction()
            try:
                manager.__enter__()
            except StorageBusy as exc:
                self._fail(str(exc), 'busy')
                return
            except Exception as exc:  # noqa: BLE001
                self._fail(_readable(exc), 'business')
                return
            self.transaction = manager
        self.conn.settimeout(TRANSACTION_TIMEOUT)
        self._ok({'transaction': True})

    def commit(self):
        manager = self.transaction
        if manager is None:
            raise ShareError('没有待提交的事务')
        with self.work_lock:
            self.transaction = None
            try:
                manager.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                self._fail(_readable(exc), 'business')
                return
        self.conn.settimeout(IDLE_TIMEOUT)
        self._ok({'transaction': False})

    def rollback(self, ack=False):
        manager = self.transaction
        if manager is not None:
            self.transaction = None
            with self.work_lock:
                try:
                    manager.__exit__(ShareError, ShareError('事务已回滚'), None)
                except Exception:  # noqa: BLE001 - the rollback itself matters, not the error
                    pass
            if not self.server.stopping:
                try:
                    self.conn.settimeout(IDLE_TIMEOUT)
                except OSError:
                    pass
            self.log('事务已回滚')
        if ack and not self.server.stopping:
            self._ok({'transaction': False})


class ShareServer:
    """Listens for other computers and runs their statements on our database."""

    def __init__(self, database, port=DEFAULT_PORT, password='', name='', allow_remote=False, log=None):
        self.db = database
        self.password = str(password or '')
        self.name = str(name or '').strip() or socket.gethostname()
        self.allow_remote = bool(allow_remote)
        self._log = log if callable(log) else (lambda message: None)
        self._state = threading.Lock()
        self._listener = None
        self._thread = None
        self._stop = threading.Event()
        self._sessions = {}
        self._counter = 0
        self._fails = {}
        self._bans = {}
        self.public_ip = ''
        self.bound_port = int(DEFAULT_PORT if port is None else port)
        self.started_at = 0.0
        self.totals = {'connections': 0, 'statements': 0, 'rejected': 0}

    # ------------------------------------------------------------ state

    @property
    def stopping(self):
        return self._stop.is_set()

    @property
    def running(self):
        with self._state:
            return self._listener is not None

    def _bump(self, key):
        with self._state:
            self.totals[key] = self.totals.get(key, 0) + 1

    def note_public(self, address):
        """Remember the detected internet address, from any thread."""
        with self._state:
            self.public_ip = str(address or '')

    def _blocked(self, address):
        now = time.time()
        with self._state:
            return now < self._bans.get(address, 0.0)

    def _fail_auth(self, address):
        """Count a wrong password; shut the address out once guesses pile up."""
        now = time.time()
        with self._state:
            count = self._fails.get(address, 0) + 1
            if count >= AUTH_FAIL_LIMIT:
                self._bans[address] = now + AUTH_BAN_SECONDS
                self._fails.pop(address, None)
            else:
                self._fails[address] = count
        if count >= AUTH_FAIL_LIMIT:
            self._log('地址 %s 口令连续错误 %d 次，暂停其连接 %d 分钟' % (address, count, int(AUTH_BAN_SECONDS // 60)))

    def addresses(self):
        return local_addresses()

    def clients(self):
        return [session for session in list(self._sessions.values()) if session.ready]

    def status(self):
        with self._state:
            return {
                'running': self._listener is not None,
                'port': self.bound_port,
                'name': self.name,
                'password': bool(self.password),
                'allow_remote': self.allow_remote,
                'addresses': self.addresses() if self._listener is not None else [],
                'clients': [session.describe() for session in self.clients()],
                'totals': dict(self.totals),
                'started_at': self.started_at,
                'public_ip': self.public_ip,
            }

    # ------------------------------------------------------------ listener

    def start(self, port=None):
        with self._state:
            if self._listener is not None:
                return self.bound_port
            wanted = self.bound_port if port is None else int(port)
            if not 0 <= wanted <= 65535:
                raise ShareError('共享端口必须在 1～65535 之间')
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(('', wanted))
                listener.listen(32)
            except OSError as exc:
                listener.close()
                raise ShareError('无法在端口 %s 上开启共享：%s。请换一个端口，或检查是否已有其他程序占用。' % (wanted, exc))
            self.bound_port = listener.getsockname()[1]
            self._listener = listener
            self._stop.clear()
            self.started_at = time.time()
            self._thread = threading.Thread(target=self._accept_loop, name='jinfen-share-accept', daemon=True)
            self._thread.start()
        self._log('共享已开启，端口 %d' % self.bound_port)
        return self.bound_port

    def stop(self, wait=3.0):
        with self._state:
            listener = self._listener
            self._listener = None
            self._stop.set()
        if listener is None:
            return
        try:
            listener.close()
        except OSError:
            pass
        sessions = list(self._sessions.values())
        for session in sessions:
            session.shutdown()
        deadline = time.time() + max(0.0, wait)
        for session in sessions:
            session.join(max(0.05, deadline - time.time()))
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(1.0)
        self.started_at = 0.0
        self._log('共享已关闭')

    def _accept_loop(self):
        while not self.stopping:
            listener = self._listener
            if listener is None:
                break
            try:
                conn, peer = listener.accept()
            except OSError:
                if self.stopping:
                    break
                time.sleep(0.2)
                continue
            if not is_lan_address(peer[0]) and not self.allow_remote:
                self._bump('rejected')
                self._log('拒绝非局域网地址的连接：%s' % peer[0])
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            self._bump('connections')
            session = _Session(self, conn, peer)
            with self._state:
                self._counter += 1
                session.identifier = self._counter
                self._sessions[session.identifier] = session
            session.start()

    def _forget(self, session):
        with self._state:
            self._sessions.pop(session.identifier, None)
        if session.ready:
            self._log('已断开（客户端：%s）' % (session.client_name or '未知电脑'))


def describe_share(server):
    """One-line summary for the settings card."""
    if server is None or not server.running:
        return '局域网共享未开启'
    addresses = server.addresses()
    where = addresses[0] if addresses else '本机'
    scope = '局域网 + 公网' if server.allow_remote else '仅局域网'
    return '共享已开启（可达范围：%s）：%s:%d，在线客户端 %d 台' % (scope, where, server.bound_port, len(server.clients()))
