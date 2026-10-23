"""LAN share: protocol screening, host service, remote client and the UI wiring."""
import os
import socket
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt

from database import Database, default_share, load_share, save_share
from enhanced import Service
from service import BusinessError
from share_client import LanShareError, RemoteDatabase, test_connection
from share_protocol import (
    AUTH_FAIL_LIMIT, DEFAULT_PORT, PROTOCOL, ShareError, batches, is_lan_address,
    local_addresses, proof, recv_frame, row_cost, screen, send_frame, verify,
)
from share_server import ShareServer
from access import BUILTIN_USERNAME
from test_support import seed_admin

PASSWORD = 'Lan12345678'


def asset_row(identifier, name='共享设备'):
    stamp = '2026-01-01T00:00:00+00:00'
    return {'id': identifier, 'name': name, 'category': '电脑主机', 'brand': '', 'serial': '', 'quantity': 1, 'owner': '', 'location': '', 'supplier_id': '', 'purchase_date': '', 'price_cents': 0, 'life_months': 12, 'residual_bp': 0, 'warranty_date': '', 'status': '闲置', 'employee_id': '', 'due_date': '', 'notes': '', 'revision': 1, 'created_at': stamp, 'updated_at': stamp}


def wait_for(condition, timeout=8.0, step=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(step)
    return False


class ProtocolTests(unittest.TestCase):
    """The statement screen is the host's own guard against a rogue client."""

    ALLOWED = [
        'SELECT * FROM assets',
        'SELECT * FROM assets WHERE id=? AND revision>?',
        'SELECT value FROM meta WHERE key=?',
        "SELECT id FROM users WHERE role='管理员' AND active=1",
        'SELECT COALESCE(SUM(size_bytes),0) AS n FROM attachments',
        'SELECT 1 AS connected',
        'SELECT id,display_name FROM users',
        'SELECT * FROM attachments ORDER BY created_at DESC,id DESC',
        'SELECT COUNT(*) AS n FROM count_items WHERE count_id=?',
        "SELECT * FROM assets WHERE status<>'已报废' AND location=?",
        "INSERT INTO meta (key,value) VALUES ('write_lock','1') ON CONFLICT (key) DO NOTHING",
        "INSERT INTO meta (key,value) VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=excluded.value",
        'INSERT INTO tasks (id,title,asset_id,priority,due_date,notes,status,creator_id,created_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
        'UPDATE users SET password=?,failures=?,locked_until=? WHERE id=?',
        'DELETE FROM count_items WHERE count_id=?',
        "DELETE FROM events WHERE asset_id=? AND action='资产入库'",
    ]
    REFUSED = [
        'DROP TABLE assets',
        'PRAGMA journal_mode',
        'VACUUM',
        'DELETE FROM assets; DROP TABLE users',
        'SELECT * FROM sqlite_master',
        'SELECT * FROM sqlite_temp_master',
        'SELECT * FROM main.assets',
        'ATTACH DATABASE ? AS x',
        'INSERT INTO unknown_table (a) VALUES (1)',
        'SELECT * FROM assets WHERE id IN (SELECT id FROM users)',
        'WITH t AS (SELECT * FROM users) SELECT * FROM t',
        'ALTER TABLE assets ADD COLUMN x TEXT',
        'CREATE TABLE bad (a TEXT)',
        'DROP INDEX idx_events_asset_id',
        'REPLACE INTO users (id) VALUES (?)',
        "SELECT load_extension('x')",
        "SELECT sqlite_read('x')",
        'EXPLAIN QUERY PLAN SELECT * FROM users',
        '',
        '   ',
    ]

    def test_real_statements_are_allowed(self):
        for sql in self.ALLOWED:
            with self.subTest(sql=sql):
                self.assertEqual(screen(sql), sql.strip())

    def test_dangerous_statements_are_refused(self):
        for sql in self.REFUSED:
            with self.subTest(sql=sql):
                with self.assertRaises(ShareError):
                    screen(sql)

    def test_framing_round_trip(self):
        left, right = socket.socketpair()
        try:
            send_frame(left, {'op': 'exec', 'rows': [{'name': '中文', 'n': 3}]})
            self.assertEqual(recv_frame(right), {'op': 'exec', 'rows': [{'name': '中文', 'n': 3}]})
        finally:
            left.close(); right.close()

    def test_handshake_proof_never_carries_the_password(self):
        nonce, server_nonce = 'aaa', 'bbb'
        digest = proof(PASSWORD, nonce, server_nonce)
        self.assertNotIn(PASSWORD, digest)
        self.assertTrue(verify(PASSWORD, nonce, server_nonce, digest))
        self.assertFalse(verify(PASSWORD, nonce, server_nonce, digest[:-1] + '0'))
        self.assertFalse(verify('other-password', nonce, server_nonce, digest))

    def test_garbage_proof_is_refused_without_crashing(self):
        # compare_digest refuses non-ASCII strings; the host must treat that as
        # a wrong answer instead of tearing the session down.
        self.assertFalse(verify(PASSWORD, 'aaa', 'bbb', '非ASCII摘要'))
        self.assertFalse(verify(PASSWORD, 'aaa', 'bbb', None))

    def test_lan_address_filter(self):
        for host in ['127.0.0.1', '192.168.1.20', '10.0.0.7', '172.16.4.9', '169.254.10.1']:
            self.assertTrue(is_lan_address(host), host)
        for host in ['8.8.8.8', '1.1.1.1', '2001:4860:4860::8888', 'not-an-address', '']:
            self.assertFalse(is_lan_address(host), host)

    def test_batching_splits_by_size_and_count(self):
        rows = [{'blob': 'x' * 1000} for _ in range(50)]
        groups = list(batches(rows, budget=5000, limit=1000))
        self.assertGreater(len(groups), 1)
        self.assertEqual(sum(len(g) for g in groups), 50)
        self.assertTrue(all(sum(row_cost(r) for r in g) <= 6000 for g in groups))
        self.assertEqual([len(g) for g in batches(rows, budget=10 ** 9, limit=7)], [7, 7, 7, 7, 7, 7, 7, 1])


class ShareEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.host = Database({'engine': 'sqlite', 'path': str(self.folder / 'host.db')})
        self.log = []
        self.server = ShareServer(self.host, port=0, password=PASSWORD, name='测试主机', log=self.log.append)
        self.port = self.server.start()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            try:
                client.close()
            except Exception:
                pass
        self.server.stop()
        self.host.close()
        self.temp.cleanup()

    def client(self, password=PASSWORD, host='127.0.0.1'):
        db = RemoteDatabase({'engine': 'shared', 'host': host, 'port': self.port, 'password': password})
        self.clients.append(db)
        return db

    def admin(self):
        db = self.client()
        service = Service(db)
        # Registration only ever yields read-only accounts, so the administrator
        # this fixture needs is seeded straight into the host database.
        seed_admin(self.host)
        service.login('admin', 'Password12345')
        return db, service

    # ------------------------------------------------------------------ tests

    def test_connection_test_reports_the_host(self):
        self.assertEqual(test_connection({'host': '127.0.0.1', 'port': self.port, 'password': PASSWORD}), '测试主机')
        with self.assertRaises(BusinessError):
            test_connection({'host': '127.0.0.1', 'port': self.port, 'password': 'nope'})

    def test_wrong_password_and_missing_host_are_refused(self):
        with self.assertRaises(BusinessError):
            self.client(password='definitely-wrong')
        with self.assertRaises(BusinessError):
            RemoteDatabase({'engine': 'shared', 'host': '', 'port': self.port, 'password': PASSWORD})
        self.assertGreaterEqual(self.server.totals['rejected'], 1)

    def test_offline_host_gives_a_readable_message(self):
        # A port nobody listens on: the message must name the host and explain why.
        spare = socket.socket()
        spare.bind(('127.0.0.1', 0))
        dead = spare.getsockname()[1]
        spare.close()
        with self.assertRaises(BusinessError) as caught:
            RemoteDatabase({'engine': 'shared', 'host': '127.0.0.1', 'port': dead, 'password': PASSWORD})
        message = str(caught.exception)
        self.assertIn('主机电脑', message)
        self.assertIn('局域网共享', message)

    def test_end_to_end_write_is_visible_to_both_sides(self):
        db, service = self.admin()
        service.save_asset({'id': 'A1', 'name': '共享电脑', 'category': '电脑主机', 'quantity': 1, 'price_cents': 10000, 'life_months': 36, 'residual_bp': 500})
        self.assertEqual([r['id'] for r in service.list('assets')], ['A1'])
        self.assertEqual(self.host.one('SELECT COUNT(*) AS n FROM assets')['n'], 1)
        self.assertEqual(self.host.one('SELECT name FROM assets WHERE id=?', ('A1',))['name'], '共享电脑')
        # The host holds the seeded administrator plus the factory account, and
        # both were written through the same screened path.
        self.assertEqual(sorted(r['username'] for r in self.host.all('SELECT username FROM users')), sorted(['admin', BUILTIN_USERNAME]))
        self.assertEqual(len(self.server.clients()), 1)

    def test_second_client_sees_the_first_ones_rows(self):
        _db, first = self.admin()
        first.catalog('departments', {'id': 'D1', 'name': '行政部'})
        second_db = self.client()
        second = Service(second_db)
        second.login('admin', 'Password12345')
        self.assertEqual([r['name'] for r in second.list('departments')], ['行政部'])
        self.assertEqual(len(self.server.clients()), 2)

    def test_refused_statement_keeps_the_connection_alive(self):
        db, _service = self.admin()
        for sql in ['DROP TABLE assets', 'PRAGMA journal_mode', 'SELECT * FROM sqlite_master', 'DELETE FROM assets; DELETE FROM users']:
            with self.subTest(sql=sql):
                with self.assertRaises(BusinessError):
                    db.execute(sql)
        self.assertEqual(db.one('SELECT COUNT(*) AS n FROM assets')['n'], 0)

    def test_unknown_table_and_bad_fields_are_refused(self):
        db, _service = self.admin()
        with self.assertRaises(ValueError):
            db.rows('not_a_table')
        with self.assertRaises(ValueError):
            db.insert('assets', {'id': 'X1'})
        with self.assertRaises(BusinessError):
            db._request({'op': 'bulk', 'table': 'not_a_table', 'records': [asset_row('X1')]})
        with self.assertRaises(ValueError):
            db.update('assets', 'A1', {'id': 'A2'})

    def test_remote_transaction_commits_and_rolls_back(self):
        db, service = self.admin()
        with db.transaction():
            db.insert('departments', {'id': 'D1', 'name': '临时部门', 'manager': '', 'notes': ''})
        self.assertEqual(db.one('SELECT COUNT(*) AS n FROM departments')['n'], 1)
        with self.assertRaises(RuntimeError):
            with db.transaction():
                db.insert('departments', {'id': 'D2', 'name': '不应存在', 'manager': '', 'notes': ''})
                raise RuntimeError('业务主动失败')
        self.assertIsNone(db.one("SELECT id FROM departments WHERE id='D2'"))
        self.assertEqual(db.one('SELECT COUNT(*) AS n FROM departments')['n'], 1)
        self.assertFalse(db.inside)
        with self.assertRaises(RuntimeError):
            with db.transaction():
                with db.transaction():
                    pass
        self.assertIsInstance(service, Service)

    def test_failed_statement_inside_transaction_rolls_it_back(self):
        db, _service = self.admin()
        db.insert('departments', {'id': 'D1', 'name': '占用编号', 'manager': '', 'notes': ''})
        with self.assertRaises(BusinessError):
            with db.transaction():
                db.insert('departments', {'id': 'D9', 'name': '先写入的', 'manager': '', 'notes': ''})
                db.insert('departments', {'id': 'D1', 'name': '重复编号', 'manager': '', 'notes': ''})
        self.assertIsNone(db.one("SELECT id FROM departments WHERE id='D9'"))
        # The session survives, so the next request still works.
        self.assertEqual(db.one('SELECT COUNT(*) AS n FROM departments')['n'], 1)

    def test_client_disconnect_releases_the_host_lock(self):
        db, _service = self.admin()
        stuck = self.client()
        manager = stuck.transaction()
        manager.__enter__()
        online = {session['id'] for session in self.server.status()['clients']}
        self.assertEqual(len(online), 2)
        abandoned = max(online)
        stuck._drop('模拟掉线')
        self.assertTrue(wait_for(lambda: abandoned not in {s['id'] for s in self.server.status()['clients']}), '主机应清理掉线的会话')
        self.assertEqual(len(self.server.clients()), 1)
        finished = threading.Event()

        def write():
            with self.host.transaction():
                self.host.insert('departments', {'id': 'D7', 'name': '掉线后写入', 'manager': '', 'notes': ''})
            finished.set()

        worker = threading.Thread(target=write)
        worker.start()
        worker.join(6.0)
        self.assertTrue(finished.is_set(), '事务应随客户端掉线自动回滚并释放锁')
        self.assertEqual(db.one("SELECT name FROM departments WHERE id='D7'")['name'], '掉线后写入')

    def test_two_clients_write_concurrently(self):
        _db, first = self.admin()
        second_db = self.client()
        second = Service(second_db)
        second.login('admin', 'Password12345')
        problems = []

        def work(service, tag):
            for index in range(12):
                try:
                    service.save_asset({'id': '%s-%02d' % (tag, index), 'name': '并发设备', 'category': '电脑主机', 'quantity': 1, 'price_cents': 100, 'life_months': 12, 'residual_bp': 0})
                except Exception as exc:  # noqa: BLE001 - collected for the assertion
                    problems.append('%s #%d %r' % (tag, index, exc))

        threads = [threading.Thread(target=work, args=(first, 'T1')), threading.Thread(target=work, args=(second, 'T2'))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60.0)
        self.assertEqual(problems, [])
        self.assertEqual(self.host.one('SELECT COUNT(*) AS n FROM assets')['n'], 24)

    def test_bulk_restore_round_trip(self):
        db, service = self.admin()
        rows = [asset_row('B%04d' % index, '批量设备') for index in range(4000)]
        started = time.time()
        db.insert_many('assets', rows)
        self.assertLess(time.time() - started, 60.0)
        self.assertEqual(db.one('SELECT COUNT(*) AS n FROM assets')['n'], 4000)
        # Chunked reads reassemble a table larger than one frame.
        fetched = db.rows('assets')
        self.assertEqual(len(fetched), 4000)
        self.assertEqual({r['id'] for r in fetched}, {r['id'] for r in rows})
        self.assertIsNone(service.db.one('SELECT id FROM assets WHERE id=?', ('B9999',)))

    def test_backup_and_restore_over_the_share(self):
        db, service = self.admin()
        service.save_asset({'id': 'A1', 'name': '备份设备', 'category': '电脑主机', 'quantity': 2, 'price_cents': 5000, 'life_months': 24, 'residual_bp': 1000})
        service.catalog('departments', {'id': 'D1', 'name': '行政部'})
        target = self.folder / 'backup.json'
        service.backup(target)
        self.assertTrue(target.exists())
        service.delete_asset('A1')
        service.restore(target)
        service.login('admin', 'Password12345')
        rows = service.list('assets')
        self.assertEqual([(r['id'], r['name'], r['quantity']) for r in rows], [('A1', '备份设备', 2)])
        self.assertEqual(self.host.one('SELECT COUNT(*) AS n FROM departments')['n'], 1)

    def test_host_keeps_working_while_a_share_is_running(self):
        db, service = self.admin()
        service.save_asset({'id': 'A1', 'name': '本机设备', 'category': '电脑主机', 'quantity': 1, 'price_cents': 100, 'life_months': 12, 'residual_bp': 0})
        host_service = Service(self.host)
        host_service.login('admin', 'Password12345')
        host_service.save_asset({'id': 'A2', 'name': '主机自己加的', 'category': '电脑主机', 'quantity': 1, 'price_cents': 100, 'life_months': 12, 'residual_bp': 0})
        self.assertEqual({r['id'] for r in db.rows('assets')}, {'A1', 'A2'})

    def test_service_stop_drops_every_client(self):
        db, _service = self.admin()
        self.assertIsNotNone(db.one('SELECT 1 AS connected'))
        self.server.stop()
        self.assertFalse(self.server.running)
        self.assertTrue(wait_for(lambda: not self.server.clients(), 12.0), '关闭共享后应清理所有会话')
        with self.assertRaises(BusinessError):
            db.one('SELECT 1 AS connected')
        # Restarting on the same port brings the client back automatically.
        self.server.start(self.port)
        self.assertEqual(db.one('SELECT 1 AS connected')['connected'], 1)

    def test_status_card_reports_addresses_and_totals(self):
        db, _service = self.admin()
        status = self.server.status()
        self.assertTrue(status['running'])
        self.assertEqual(status['port'], self.port)
        self.assertTrue(status['password'])
        self.assertEqual(len(status['clients']), 1)
        self.assertGreaterEqual(status['totals']['statements'], 2)
        self.assertTrue(all(is_lan_address(address) for address in status['addresses']))
        self.assertEqual(status['addresses'], local_addresses())

    def test_handshake_rejects_a_foreign_client(self):
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=8)
        try:
            sock.settimeout(8)
            send_frame(sock, {'op': 'hello', 'protocol': PROTOCOL + 1, 'client': '越权测试'})
            answer = recv_frame(sock)
            self.assertFalse(answer['ok'])
            self.assertEqual(answer['kind'], 'protocol')
        finally:
            sock.close()

    def test_repeated_auth_failures_shut_the_address_out(self):
        for _ in range(AUTH_FAIL_LIMIT):
            sock = socket.create_connection(('127.0.0.1', self.port), timeout=8)
            try:
                sock.settimeout(8)
                send_frame(sock, {'op': 'hello', 'protocol': PROTOCOL, 'client': '口令猜测测试'})
                recv_frame(sock)
                send_frame(sock, {'op': 'auth', 'nonce': 'x', 'proof': 'bad-hex-digest'})
                answer = recv_frame(sock)
                self.assertFalse(answer['ok'])
            finally:
                sock.close()
        # Even the right password no longer lets this address in, and the
        # refusal says so in plain words.
        with self.assertRaises(LanShareError) as caught:
            self.client()
        self.assertIn('暂停', str(caught.exception))

    def test_data_survives_the_share_being_switched_off(self):
        db, service = self.admin()
        service.save_asset({'id': 'A1', 'name': '落盘设备', 'category': '电脑主机', 'quantity': 1, 'price_cents': 100, 'life_months': 12, 'residual_bp': 0})
        db.close()
        self.server.stop()
        self.assertEqual(self.host.one('SELECT COUNT(*) AS n FROM assets')['n'], 1)
        reopened = Database({'engine': 'sqlite', 'path': str(self.folder / 'host.db')})
        try:
            self.assertEqual(reopened.one('SELECT name FROM assets WHERE id=?', ('A1',))['name'], '落盘设备')
        finally:
            reopened.close()


class ShareServerOptionTests(unittest.TestCase):
    def test_start_and_stop_are_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database({'engine': 'sqlite', 'path': str(Path(folder) / 'host.db')})
            server = ShareServer(db, port=0, password=PASSWORD)
            try:
                port = server.start()
                self.assertEqual(server.start(), port)
                self.assertTrue(server.running)
                server.stop()
                self.assertFalse(server.running)
                server.stop()
                self.assertFalse(server.running)
            finally:
                db.close()

    def test_impossible_port_is_reported_clearly(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database({'engine': 'sqlite', 'path': str(Path(folder) / 'host.db')})
            server = ShareServer(db, port=70000, password=PASSWORD)
            try:
                with self.assertRaises(ShareError) as caught:
                    server.start()
                self.assertIn('端口', str(caught.exception))
                self.assertFalse(server.running)
            finally:
                db.close()

    def test_describe_share_summarises_state(self):
        from share_server import describe_share
        self.assertEqual(describe_share(None), '局域网共享未开启')
        with tempfile.TemporaryDirectory() as folder:
            db = Database({'engine': 'sqlite', 'path': str(Path(folder) / 'host.db')})
            server = ShareServer(db, port=0, password=PASSWORD)
            self.assertEqual(describe_share(server), '局域网共享未开启')
            server.start()
            try:
                self.assertIn('共享已开启', describe_share(server))
            finally:
                server.stop()
                db.close()

    def test_default_port_is_the_documented_one(self):
        self.assertEqual(DEFAULT_PORT, 8765)


class ShareSettingsTests(unittest.TestCase):
    """The host's share settings live in their own file, with a protected password."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'JINFEN_DATA_DIR': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_defaults_are_off_and_on_the_documented_port(self):
        settings = default_share()
        self.assertEqual((settings['enabled'], settings['port'], settings['password'], settings['allow_remote']), (False, DEFAULT_PORT, '', False))
        self.assertEqual(load_share(), settings)

    def test_round_trip_keeps_the_password_off_disk(self):
        if os.name != 'nt':
            self.skipTest('密码保护依赖 Windows DPAPI')
        save_share({'enabled': True, 'port': 9123, 'password': 'RoundTrip1234', 'allow_remote': True, 'identity': 'D:/data/assets.db'})
        settings = load_share()
        self.assertEqual((settings['enabled'], settings['port'], settings['password'], settings['allow_remote'], settings['identity']), (True, 9123, 'RoundTrip1234', True, 'D:/data/assets.db'))
        raw = (Path(self.temp.name) / 'share.json').read_text('utf-8')
        self.assertNotIn('RoundTrip1234', raw)
        self.assertIn('password_protected', raw)

    def test_corrupt_file_falls_back_to_defaults(self):
        (Path(self.temp.name) / 'share.json').write_text('{not json', 'utf-8')
        self.assertFalse(load_share()['enabled'])
        (Path(self.temp.name) / 'share.json').write_text('[1,2,3]', 'utf-8')
        self.assertEqual(load_share()['port'], DEFAULT_PORT)

    def test_out_of_range_port_is_ignored(self):
        (Path(self.temp.name) / 'share.json').write_text('{"enabled": true, "port": 99999}', 'utf-8')
        self.assertEqual(load_share()['port'], DEFAULT_PORT)


class ShareUiTests(unittest.TestCase):
    """The window must be able to turn the share on and off without a rebuild."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'JINFEN_DATA_DIR': self.temp.name})
        self.env.start()
        self.db = Database({'engine': 'sqlite', 'path': str(self.folder / 'db.sqlite')})
        self.s = Service(self.db)
        seed_admin(self.db)
        self.s.login('admin', 'Password12345')
        self.window = None

    def tearDown(self):
        if self.window:
            self.window.retire()
            self.window.close()
        self.db.close()
        self.env.stop()
        self.temp.cleanup()

    def build(self):
        from app import MainWindow
        self.window = MainWindow(self.s)
        self.window.retire()
        return self.window

    def start_share(self, window, password='WindowShare1234'):
        problems = []
        for port in [18765, 28765, 38765, 48765, 58765, 8765]:
            window.share_port.setValue(port)
            window.share_password.setText(password)
            try:
                window.start_share(quiet=True)
            except BusinessError as exc:
                problems.append(str(exc)); continue
            return port
        self.fail('没有可用的共享端口：' + ' / '.join(problems))

    def test_mode_text_names_the_live_store(self):
        from app import db_identity, mode_text
        window = self.build()
        self.assertIn('SQLite', mode_text(self.db))
        self.assertIn('SQLite', window.db_mode.text())
        self.assertEqual(window.share_badge.text(), '')
        self.assertEqual(db_identity(self.db), str(self.folder / 'db.sqlite'))

    def test_data_page_offers_the_share_card(self):
        window = self.build()
        window.navigate('data')
        self.assertIsNotNone(window.share_status)
        self.assertIn('共享未开启', window.share_status.text())
        self.assertTrue(window.share_on.isEnabled())
        self.assertEqual(window.share_clients.rowCount(), 0)
        self.assertEqual(window.share_clients.horizontalHeader().defaultAlignment(), Qt.AlignCenter)

    def test_starting_and_stopping_from_the_window(self):
        from app import db_identity
        window = self.build()
        port = self.start_share(window)
        self.assertTrue(window.share.running)
        self.assertEqual(window.share.bound_port, port)
        self.assertIn('共享已开启', window.share_status.text())
        self.assertEqual(window.share_badge.text(), '● 已共享给局域网')
        settings = load_share()
        self.assertTrue(settings['enabled'])
        self.assertEqual(settings['password'], 'WindowShare1234')
        self.assertEqual(settings['identity'], db_identity(self.db))
        client = RemoteDatabase({'engine': 'shared', 'host': '127.0.0.1', 'port': port, 'password': 'WindowShare1234'})
        try:
            client.one('SELECT 1 AS connected')
            window.refresh_share()
            self.assertEqual(window.share_clients.rowCount(), 1)
            self.assertEqual(window.share_clients.item(0, 0).text(), socket.gethostname())
            self.assertGreaterEqual(int(window.share_clients.item(0, 3).text()), 1)
        finally:
            client.close()
        window.stop_share(quiet=True)
        self.assertIsNone(window.share)
        self.assertFalse(load_share()['enabled'])
        self.assertIn('共享未开启', window.share_status.text())

    def test_short_password_is_refused_before_listening(self):
        window = self.build()
        window.share_port.setValue(18765)
        window.share_password.setText('short')
        with self.assertRaises(BusinessError):
            window.start_share(quiet=True)
        self.assertIsNone(window.share)
        self.assertFalse(load_share()['enabled'])

    def test_public_access_is_opt_in_and_needs_a_strong_password(self):
        window = self.build()
        self.assertFalse(window.share_public.isChecked())
        window.share_port.setValue(18765)
        window.share_public.setChecked(True)
        window.share_password.setText('Public12345')
        with self.assertRaises(BusinessError) as caught:
            window.start_share(quiet=True)
        self.assertIn('12 位', str(caught.exception))
        self.assertIsNone(window.share)
        self.assertFalse(load_share()['enabled'])
        window.share_password.setText('PublicShare12345')
        try:
            with patch('app.public_address', return_value='203.0.113.7'):
                window.start_share(quiet=True)
                window.detect_public()
            window.refresh_share()
            self.assertTrue(window.share.running)
            self.assertTrue(window.share.allow_remote)
            self.assertIn('公网', window.share_status.text())
            self.assertIn('203.0.113.7', window.share_status.text())
            self.assertEqual(window.share_badge.text(), '● 已共享（局域网+公网）')
        finally:
            window.stop_share(quiet=True)
        self.assertFalse(window.share_public_note.isVisible())

    def test_restore_share_skips_another_databases_settings(self):
        save_share({'enabled': True, 'port': 18765, 'password': 'OtherShare1234', 'allow_remote': False, 'identity': 'D:/elsewhere/assets.db'})
        window = self.build()
        window.restore_share()
        self.assertIsNone(window.share)
        self.assertTrue(any('另一个数据库' in line for line in window.share_lines))

    def test_restore_share_does_not_start_for_a_client_window(self):
        save_share({'enabled': True, 'port': 18765, 'password': 'OtherShare1234', 'allow_remote': False, 'identity': ''})
        window = self.build()
        window.db = type('Fake', (), {'shared': True, 'host': '10.0.0.9'})()
        window.restore_share()
        self.assertIsNone(window.share)
        self.assertTrue(any('客户端' in line for line in window.share_lines))

    def test_operators_cannot_change_the_share(self):
        self.s.register('worker', '普通员工', 'Password12345')
        uid = self.db.one("SELECT id FROM users WHERE username='worker'")['id']
        self.s.manage_user(uid, '操作员', True)
        other = Service(self.db)
        other.login('worker', 'Password12345')
        from app import MainWindow
        self.window = MainWindow(other)
        window = self.window
        self.assertFalse(window.share_on.isEnabled())
        self.assertFalse(window.share_off.isEnabled())
        window.share_password.setText('OperatorShare1234')
        with self.assertRaises(BusinessError):
            window.start_share(quiet=True)
        window.retire()

    def test_connection_dialog_understands_shared_mode(self):
        from app import ConnectionDialog
        from database import default_config
        dialog = ConnectionDialog(default_config())
        try:
            self.assertEqual([dialog.engine.itemData(i) for i in range(dialog.engine.count())], ['sqlite', 'shared', 'postgresql'])
            dialog.engine.setCurrentIndex(1)
            self.assertEqual(dialog.engine.currentData(), 'shared')
            self.assertEqual(dialog.labels['host'].text(), '主机电脑地址')
            self.assertEqual(dialog.labels['password'].text(), '访问口令（≥8 位）')
            self.assertEqual(dialog.fields['port'].text(), str(DEFAULT_PORT))
            # isHidden() is the right question: the dialog itself is not shown yet.
            self.assertTrue(dialog.fields['database'].isHidden())
            self.assertTrue(dialog.fields['user'].isHidden())
            self.assertTrue(dialog.fields['sslmode'].isHidden())
            self.assertFalse(dialog.fields['host'].isHidden())
            self.assertTrue(dialog.fields['path'].isHidden())
            dialog.fields['host'].setText('192.168.1.9')
            dialog.fields['password'].setText('Lan12345678')
            self.assertEqual(dialog.config(), {'engine': 'shared', 'host': '192.168.1.9', 'port': DEFAULT_PORT, 'password': 'Lan12345678'})
            dialog.fields['password'].setText('1234567')
            with self.assertRaises(BusinessError):
                dialog.config()
            dialog.fields['password'].setText('Lan12345678')
            dialog.fields['port'].setText('70000')
            with self.assertRaises(BusinessError):
                dialog.config()
            dialog.fields['port'].setText(str(DEFAULT_PORT))
            dialog.engine.setCurrentIndex(0)
            self.assertEqual(dialog.engine.currentData(), 'sqlite')
            self.assertFalse(dialog.fields['path'].isHidden())
            self.assertTrue(dialog.fields['host'].isHidden())
        finally:
            dialog.close()

    def test_connection_dialog_shared_config_connects_for_real(self):
        from app import ConnectionDialog
        from database import default_config
        host = Database({'engine': 'sqlite', 'path': str(self.folder / 'host2.db')})
        server = ShareServer(host, port=0, password='DialogShare1234', name='对话框主机')
        port = server.start()
        dialog = ConnectionDialog(default_config())
        try:
            dialog.engine.setCurrentIndex(1)
            dialog.fields['host'].setText('127.0.0.1')
            dialog.fields['port'].setText(str(port))
            dialog.fields['password'].setText('DialogShare1234')
            self.assertEqual(test_connection(dialog.config()), '对话框主机')
            dialog.fields['password'].setText('WrongDialog1234')
            with self.assertRaises(BusinessError):
                test_connection(dialog.config())
        finally:
            dialog.close()
            server.stop()
            host.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
