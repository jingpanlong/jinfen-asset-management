"""SQLite / PostgreSQL storage. All application writes are serialized transactions.

One process-wide re-entrant lock guards every statement.  A single-user desktop
never contends for it, but when this computer hosts a LAN share the GUI thread
and the per-client worker threads all funnel through the same connection, so the
lock is what keeps a shared SQLite file correct.
"""
from __future__ import annotations
import contextlib
import json
import os
import sqlite3
import threading
from pathlib import Path

# A hosted share may hold the lock for the duration of a client transaction.
# The GUI must never freeze forever, so a contended acquisition gives up and
# reports a retryable message instead.
LOCK_WAIT_SECONDS = 25.0

SCHEMA = {
    'meta': 'key TEXT PRIMARY KEY, value TEXT NOT NULL',
    'users': 'id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL, failures INTEGER NOT NULL, locked_until TEXT NOT NULL, created_at TEXT NOT NULL',
    'departments': 'id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, manager TEXT NOT NULL, notes TEXT NOT NULL',
    'employees': 'id TEXT PRIMARY KEY, name TEXT NOT NULL, department_id TEXT NOT NULL, phone TEXT NOT NULL, email TEXT NOT NULL, active INTEGER NOT NULL, notes TEXT NOT NULL',
    'suppliers': 'id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, contact TEXT NOT NULL, phone TEXT NOT NULL, notes TEXT NOT NULL',
    'assets': 'id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, brand TEXT NOT NULL, serial TEXT NOT NULL, quantity INTEGER NOT NULL, owner TEXT NOT NULL, location TEXT NOT NULL, supplier_id TEXT NOT NULL, purchase_date TEXT NOT NULL, price_cents INTEGER NOT NULL, life_months INTEGER NOT NULL, residual_bp INTEGER NOT NULL, warranty_date TEXT NOT NULL, status TEXT NOT NULL, employee_id TEXT NOT NULL, due_date TEXT NOT NULL, notes TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL',
    'events': 'id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, action TEXT NOT NULL, actor_id TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL',
    'repairs': 'id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, problem TEXT NOT NULL, vendor TEXT NOT NULL, cost_cents INTEGER NOT NULL, result TEXT NOT NULL, status TEXT NOT NULL, opened_at TEXT NOT NULL, closed_at TEXT NOT NULL',
    'purchases': 'id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, quantity INTEGER NOT NULL, budget_cents INTEGER NOT NULL, reason TEXT NOT NULL, requester_id TEXT NOT NULL, status TEXT NOT NULL, approver_id TEXT NOT NULL, comment TEXT NOT NULL, asset_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL',
    'counts': 'id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL, status TEXT NOT NULL, creator_id TEXT NOT NULL, created_at TEXT NOT NULL, closed_at TEXT NOT NULL',
    'count_items': 'id TEXT PRIMARY KEY, count_id TEXT NOT NULL, asset_id TEXT NOT NULL, expected_quantity INTEGER NOT NULL, expected_status TEXT NOT NULL, actual_quantity INTEGER NOT NULL, result TEXT NOT NULL, notes TEXT NOT NULL, checked_at TEXT NOT NULL, checker_id TEXT NOT NULL',
    'audit': 'id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL',
    'attachments': 'id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, filename TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, content_b64 TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL',
    'tasks': 'id TEXT PRIMARY KEY, title TEXT NOT NULL, asset_id TEXT NOT NULL, priority TEXT NOT NULL, due_date TEXT NOT NULL, notes TEXT NOT NULL, status TEXT NOT NULL, creator_id TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT NOT NULL',
    'import_batches': 'id TEXT PRIMARY KEY, filename TEXT NOT NULL, kind TEXT NOT NULL, mode TEXT NOT NULL, added INTEGER NOT NULL, updated INTEGER NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL',
}
TABLES = list(SCHEMA)
COLUMNS = {t: [part.strip().split()[0] for part in s.split(',')] for t, s in SCHEMA.items()}
INTEGER_COLUMNS = {t: {p.strip().split()[0] for p in s.split(',') if p.strip().split()[1] == 'INTEGER'} for t, s in SCHEMA.items()}

def data_dir():
    # An explicit override supports QA without touching a real user's database.
    base=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))
    legacy=base/'QiyunAssets'
    modern=base/'JinfenAssets'
    # Retain an existing database and encrypted connection configuration in place.
    # Fresh installations use the new brand's directory.
    has_data=lambda path:any((path/name).exists() for name in ['assets.db','connection.json'])
    default=modern if has_data(modern) else legacy if has_data(legacy) else modern
    path = Path(os.environ.get('JINFEN_DATA_DIR') or os.environ.get('QIYUN_DATA_DIR') or str(default))
    path.mkdir(parents=True, exist_ok=True)
    return path

def default_config():
    return {'engine': 'sqlite', 'path': str(data_dir() / 'assets.db')}

def protect(value: str, decrypt=False):
    """Windows DPAPI, bound to the current Windows account, never plaintext on disk."""
    import base64
    import ctypes
    from ctypes import wintypes
    if os.name != 'nt':
        raise RuntimeError('保存数据库密码需要 Windows DPAPI')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    raw = base64.b64decode(value) if decrypt else value.encode('utf-8')
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, target = Blob(len(raw), buffer), Blob()
    crypt = ctypes.windll.crypt32
    fun = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    if not fun(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError('数据库密码加密 / 解密失败，请重新输入')
    try:
        out = ctypes.string_at(target.data, target.size)
        return out.decode('utf-8') if decrypt else base64.b64encode(out).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)

def load_config():
    file = data_dir() / 'connection.json'
    if not file.exists():
        return default_config()
    config = json.loads(file.read_text('utf-8'))
    if config.pop('password_protected', ''):
        # Re-read only to keep the encrypted value out of error strings.
        config['password'] = protect(json.loads(file.read_text('utf-8'))['password_protected'], True)
    return config

def save_config(config):
    saved = dict(config)
    password = saved.pop('password', '')
    if password:
        saved['password_protected'] = protect(password)
    tmp = data_dir() / 'connection.tmp'
    tmp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), 'utf-8')
    tmp.replace(data_dir() / 'connection.json')

def default_share():
    """Settings for hosting this computer's data over the local network."""
    from share_protocol import DEFAULT_PORT
    return {'enabled': False, 'port': DEFAULT_PORT, 'password': '', 'allow_remote': False, 'identity': ''}

def load_share():
    file = data_dir() / 'share.json'
    settings = default_share()
    if not file.exists():
        return settings
    try:
        saved = json.loads(file.read_text('utf-8'))
    except (OSError, ValueError):
        return settings
    if not isinstance(saved, dict):
        return settings
    settings['enabled'] = bool(saved.get('enabled'))
    settings['allow_remote'] = bool(saved.get('allow_remote'))
    settings['identity'] = str(saved.get('identity') or '')
    try:
        port = int(saved.get('port') or settings['port'])
        if 1 <= port <= 65535:
            settings['port'] = port
    except (TypeError, ValueError):
        pass
    if saved.get('password_protected'):
        try:
            settings['password'] = protect(str(saved['password_protected']), True)
        except OSError:
            settings['password'] = ''
    return settings

def save_share(settings):
    settings = dict(settings or {})
    saved = {'enabled': bool(settings.get('enabled')), 'port': int(settings.get('port') or default_share()['port']), 'allow_remote': bool(settings.get('allow_remote')), 'identity': str(settings.get('identity') or '')}
    password = str(settings.get('password') or '')
    if password:
        saved['password_protected'] = protect(password)
    tmp = data_dir() / 'share.tmp'
    tmp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), 'utf-8')
    tmp.replace(data_dir() / 'share.json')

class StorageBusy(RuntimeError):
    """Raised when a hosted share holds the storage lock longer than we can wait."""


class Database:
    def __init__(self, config=None, initialize=True):
        self.config = dict(config or default_config())
        self.pg = self.config['engine'] == 'postgresql'
        self.shared = False
        # "Am I inside a transaction?" is a question about the calling thread, not
        # about the process: when this computer hosts a share, each client has its
        # own worker thread and they must queue, not be mistaken for nesting.
        self._local = threading.local()
        # Re-entrant so a transaction can keep the lock across its statements while
        # nested helpers re-acquire it from the same thread.
        self._lock = threading.RLock()
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row
            self.conn = psycopg.connect(host=self.config['host'], port=int(self.config.get('port', 5432)), dbname=self.config['database'], user=self.config['user'], password=self.config.get('password', ''), sslmode=self.config.get('sslmode', 'require'), connect_timeout=8, autocommit=True, row_factory=dict_row)
            self.execute("SET statement_timeout = '15000'")
            self.execute("SET lock_timeout = '10000'")
        else:
            Path(self.config['path']).parent.mkdir(parents=True, exist_ok=True)
            # Hosting a share runs statements from client worker threads, so the
            # connection must accept cross-thread use; the lock keeps it serial.
            self.conn = sqlite3.connect(self.config['path'], timeout=15, isolation_level=None, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.execute('PRAGMA foreign_keys=ON')
            self.execute('PRAGMA journal_mode=WAL')
        if initialize:
            try:
                with self.transaction(bootstrap=True):
                    if self.pg:
                        self.execute('SELECT pg_advisory_xact_lock(714026)')
                    for table, fields in SCHEMA.items():
                        self.execute(f'CREATE TABLE IF NOT EXISTS {table} ({fields})')
                    self.execute("INSERT INTO meta (key,value) VALUES ('write_lock','1') ON CONFLICT (key) DO NOTHING")
                    version = self.one("SELECT value FROM meta WHERE key='schema_version'")
                    if version and version['value'] not in ['2','3']:
                        raise RuntimeError('数据库版本不兼容，请使用对应版本软件')
                    self.execute("INSERT INTO meta (key,value) VALUES ('schema_version','3') ON CONFLICT (key) DO UPDATE SET value='3'")
                    for table, col in [('events', 'asset_id'), ('count_items', 'count_id'), ('repairs', 'asset_id'), ('audit', 'created_at')]:
                        self.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON {table} ({col})')
            except Exception:
                self.close()
                raise

    @property
    def inside(self):
        """True while *this thread* is inside a transaction."""
        return bool(getattr(self._local, 'inside', False))

    @inside.setter
    def inside(self, value):
        self._local.inside = bool(value)

    def guard(self, timeout=None):
        """Take the storage lock, refusing to freeze the caller indefinitely."""
        if self._lock.acquire(timeout=LOCK_WAIT_SECONDS if timeout is None else timeout):
            return
        raise StorageBusy('另一台电脑正在写入共享数据，请稍后重试。')

    def release(self):
        try:
            self._lock.release()
        except RuntimeError:  # released by a rollback path that never acquired
            pass

    def execute(self, sql, params=()):
        # Never wait forever: a client's transaction may hold the lock, and the
        # window must stay responsive instead of freezing until that finishes.
        self.guard()
        try:
            return self.conn.execute(sql.replace('?', '%s') if self.pg else sql, tuple(params))
        finally:
            self.release()

    def all(self, sql, params=()):
        self.guard()
        try:
            return [dict(r) for r in self.conn.execute(sql.replace('?', '%s') if self.pg else sql, tuple(params)).fetchall()]
        finally:
            self.release()

    def one(self, sql, params=()):
        self.guard()
        try:
            row = self.conn.execute(sql.replace('?', '%s') if self.pg else sql, tuple(params)).fetchone()
            return dict(row) if row else None
        finally:
            self.release()

    def rows(self, table):
        if table not in SCHEMA:
            raise ValueError('未知数据表')
        return self.all(f'SELECT * FROM {table}')

    def insert(self, table, record):
        if table not in SCHEMA or set(record) != set(COLUMNS[table]):
            raise ValueError('数据字段不匹配：' + table)
        columns = COLUMNS[table]
        self.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', [record[k] for k in columns])

    def insert_many(self, table, records):
        """Bulk insert. Remotes override this so a restore costs one round trip per table."""
        for record in records:
            self.insert(table, record)

    def update(self, table, id, fields):
        if table not in SCHEMA or not fields or not set(fields) <= set(COLUMNS[table]) or 'id' in fields:
            raise ValueError('更新字段不匹配')
        self.execute(f'UPDATE {table} SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', [*fields.values(), id])

    @contextlib.contextmanager
    def transaction(self, bootstrap=False):
        if self.inside:
            raise RuntimeError('不允许嵌套事务')
        self.guard()
        self.inside = True
        try:
            self.execute('BEGIN' if self.pg else 'BEGIN IMMEDIATE')
            if not bootstrap and self.pg:
                self.execute("SELECT value FROM meta WHERE key='write_lock' FOR UPDATE")
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self.inside = False
            self._lock.release()

    def close(self):
        try:
            self.guard(timeout=5.0)
        except StorageBusy:
            # A client's transaction is still running.  Shutting down must not hang
            # on it, so the connection goes anyway and that client sees an error.
            with contextlib.suppress(Exception):
                self.conn.close()
            return
        try:
            self.conn.close()
        finally:
            self.release()


def open_database(config=None):
    """Build the storage backend the configuration asks for.

    ``shared`` means this computer is a client of another computer's LAN share,
    so every statement travels to that host instead of touching a local file.
    """
    resolved = dict(config or default_config())
    if resolved.get('engine') == 'shared':
        from share_client import RemoteDatabase
        return RemoteDatabase(resolved)
    return Database(resolved)
