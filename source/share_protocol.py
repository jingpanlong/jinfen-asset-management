"""Wire format, authentication and statement screening for the LAN share.

The host executes statements on behalf of other computers, so the client is not
trusted to be the application.  Only the statement shapes this program issues are
let through: one parameterised ``SELECT`` / ``INSERT`` / ``UPDATE`` / ``DELETE``
against a table of the known schema.  A client that knows the access password
still cannot run DDL, attach another database, or touch anything outside the
business tables.
"""
from __future__ import annotations
import hashlib
import hmac
import ipaddress
import json
import re
import socket
import struct

from database import SCHEMA

PROTOCOL = 1
DEFAULT_PORT = 8765
MAX_FRAME = 64 * 1024 * 1024
# A reply is split into frames of about this size, so reading a big table (or a
# backup full of photos) never needs one enormous allocation on either side.
CHUNK_BYTES = 4 * 1024 * 1024
MAX_STATEMENTS = 20000
# One bulk request carries at most this many records, whatever their size.
MAX_BATCH = 20000

# Socket behaviour: how long a live session may stay silent, and how long a
# session that is holding an open transaction may stay silent before the host
# assumes the client died and rolls the transaction back for everyone's sake.
IDLE_TIMEOUT = 300.0
TRANSACTION_TIMEOUT = 40.0
CONNECT_TIMEOUT = 8.0
HELLO_TIMEOUT = 12.0
# How long a client waits for the answer to one request.  A contended write can
# queue behind another computer for a while, so this is generous.
REQUEST_TIMEOUT = 90.0
# Public exposure: after this many wrong passwords from one address, that
# address is shut out for a while, so guessing over the internet is hopeless.
AUTH_FAIL_LIMIT = 5
AUTH_BAN_SECONDS = 600.0

_FIRST_WORD = re.compile(r'^\s*([A-Za-z]+)')
_FROM = re.compile(r'\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)', re.I)
_INTO = re.compile(r'\bINTO\s+([A-Za-z_][A-Za-z0-9_]*)', re.I)
_UPDATE_HEAD = re.compile(r'^\s*UPDATE\s+([A-Za-z_][A-Za-z0-9_]*)', re.I)
_SELECT_COUNT = re.compile(r'\bSELECT\b', re.I)
_FORBIDDEN = re.compile(r'\b(ATTACH|DETACH|PRAGMA|VACUUM|DROP|ALTER|CREATE|REPLACE|TRIGGER|INDEX|UNION|EXPLAIN|GRANT|RECURSIVE)\b', re.I)
# Functions no business statement ever needs, but an attacker would reach for:
# loading a second database, or reading and writing arbitrary files as the host.
_FORBIDDEN_FUNCTIONS = re.compile(r'\b(LOAD_EXTENSION|READFILE|WRITEFILE|FSDIR|SQLITE_READ|SQLITE_WRITE)\s*\(', re.I)
_WRITES = {'INSERT', 'UPDATE', 'DELETE'}
_ALLOWED_HEADS = {'SELECT'} | _WRITES


class ShareError(Exception):
    """A failure that carries the message the other side should display."""

    def __init__(self, message, kind='business'):
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------- statement screen

def referenced_tables(sql):
    found = _FIRST_WORD.match(sql)
    head = found.group(1).upper() if found else ''
    if head == 'SELECT':
        return set(_FROM.findall(sql)), head
    if head == 'INSERT':
        return set(_INTO.findall(sql)), head
    if head == 'UPDATE':
        return set(_UPDATE_HEAD.findall(sql)) | set(_FROM.findall(sql)), head
    if head == 'DELETE':
        return set(_FROM.findall(sql)), head
    return set(), head


def screen(sql):
    """Return the statement to run, or refuse it with a reason."""
    if not isinstance(sql, str) or not sql.strip():
        raise ShareError('收到了空的语句')
    text = sql.strip()
    if ';' in text:
        raise ShareError('共享模式下每条语句必须是单条语句')
    if len(text) > 20000:
        raise ShareError('语句过长')
    if len(_SELECT_COUNT.findall(text)) > 1:
        raise ShareError('共享模式不支持子查询')
    tables, head = referenced_tables(text)
    if head not in _ALLOWED_HEADS:
        raise ShareError('共享模式只允许查询与增删改语句，收到：' + (head or '未知'))
    if head in _WRITES and not tables:
        raise ShareError('无法识别语句操作的数据表')
    unknown = sorted(t for t in tables if t not in SCHEMA)
    if unknown:
        raise ShareError('共享模式不允许访问该数据表：' + '、'.join(unknown))
    if _FORBIDDEN.search(text) or _FORBIDDEN_FUNCTIONS.search(text):
        raise ShareError('共享模式不允许该语句')
    return text


# ---------------------------------------------------------------- authentication

def _key(password):
    return hashlib.sha256(('jinfen-lan-share|' + str(password)).encode('utf-8')).digest()


def proof(password, client_nonce, server_nonce):
    """HMAC over both nonces, so the password itself never crosses the wire."""
    message = ('%s|%s' % (client_nonce, server_nonce)).encode('utf-8')
    return hmac.new(_key(password), message, hashlib.sha256).hexdigest()


def verify(password, client_nonce, server_nonce, offered):
    expected = proof(password, client_nonce, server_nonce)
    try:
        return hmac.compare_digest(expected, str(offered or ''))
    except TypeError:
        # A garbage (non-ASCII) proof is just a wrong answer, never a crash.
        return False


# ---------------------------------------------------------------- framing

def send_frame(sock, payload):
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(body) > MAX_FRAME:
        raise ShareError('数据量超过共享传输上限，请分批处理或改用本机数据库', 'business')
    sock.sendall(struct.pack('>I', len(body)) + body)


def _recv_exactly(sock, count):
    chunks = []
    remaining = count
    while remaining:
        chunk = sock.recv(min(remaining, 262144))
        if not chunk:
            raise ConnectionError('连接已断开')
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


def recv_frame(sock):
    header = _recv_exactly(sock, 4)
    length = struct.unpack('>I', header)[0]
    if length > MAX_FRAME:
        raise ShareError('对方发来的数据超过共享传输上限')
    return json.loads(_recv_exactly(sock, length).decode('utf-8'))


def row_cost(record):
    """Cheap upper-ish bound on a row's JSON size, so senders can chunk replies."""
    total = 16
    for value in record.values():
        total += len(value) if isinstance(value, str) else 8
    return total


def batches(records, budget=CHUNK_BYTES, limit=MAX_BATCH):
    """Split records into groups that fit comfortably inside one frame."""
    chunk, cost = [], 0
    for record in records:
        size = row_cost(record)
        if chunk and (cost + size >= budget or len(chunk) >= limit):
            yield chunk
            chunk, cost = [], 0
        chunk.append(record)
        cost += size
    if chunk:
        yield chunk


# ---------------------------------------------------------------- addresses

def is_lan_address(host):
    """True for loopback, link-local and the private ranges a LAN really uses."""
    try:
        address = ipaddress.ip_address(str(host).split('%')[0])
    except ValueError:
        return False
    if address.version != 4:
        return address.is_private or address.is_loopback
    return address.is_private or address.is_loopback or address.is_link_local


def local_addresses():
    """IPv4 addresses this computer answers on, best candidate first."""
    found = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(('10.255.255.255', 1))
            found.append(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    ordered = []
    for address in found:
        if not address or address.startswith('127.') or address in ordered:
            continue
        ordered.append(address)
    return ordered


# ---------------------------------------------------------------- public address

# Plain-HTTP echo services that answer with this network's internet address.
# Tried in order; none of them is contacted unless the user turns on public access.
_PUBLIC_PROBES = (
    ('api.ipify.org', '/'),
    ('ipv4.icanhazip.com', '/'),
    ('members.3322.org', '/dyndns/getip'),
)


def public_address(timeout=3.0):
    """The internet address this network goes out through, or '' when unknown.

    Uses a raw HTTP GET so the program keeps working without any extra
    dependency.  The result is a hint for the host to hand out, not a
    guarantee: a router may still need port forwarding before clients can
    actually reach this computer.
    """
    for host, path in _PUBLIC_PROBES:
        try:
            with socket.create_connection((host, 80), timeout=timeout) as conn:
                conn.settimeout(timeout)
                request = 'GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: jinfen-share\r\n\r\n' % (path, host)
                conn.sendall(request.encode('ascii'))
                body = b''
                while len(body) < 8192:
                    part = conn.recv(1024)
                    if not part:
                        break
                    body += part
            text = body.decode('utf-8', 'replace')
            marker = text.rfind('\r\n\r\n')
            payload = text[marker + 4:] if marker >= 0 else text
            found = re.search(r'(\d{1,3}(?:\.\d{1,3}){3})', payload)
            if found:
                address = ipaddress.ip_address(found.group(1))
                if address.version == 4 and not address.is_private and not address.is_loopback:
                    return str(address)
        except OSError:
            continue
    return ''
