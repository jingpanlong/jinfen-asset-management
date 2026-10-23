"""出厂内置管理员（owner）账号的行为验证。

软件「发给谁，内置管理员账号都是一样的」：
 * 每个数据库都带同一个用户名 ``owner`` 的管理员账号；
 * 它的口令哈希在编译期写死在 access.py 里，任何一份拷贝都相同；
 * 口令在软件内不可修改，角色与启用状态也改不动（被改动后自动恢复）；
 * 其他人自行注册一律是只读账号，管理员只能由内置账号授予；
 * 拿到管理员的其他账号依然不能动内置账号，也不能再授予别人管理员。
"""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from access import (BUILTIN_DISPLAY, BUILTIN_LABEL, BUILTIN_PASSWORD_HASH,
                    BUILTIN_USERNAME)
from database import Database
from enhanced import Service
from service import BusinessError, pw_hash
from test_support import as_builtin, seed_admin


class BuiltInAccountTests(unittest.TestCase):
    """服务层规则，不涉及界面。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database({'engine': 'sqlite', 'path': str(Path(self.tmp.name) / 'owner.db')})
        self.s = Service(self.db)
        self.s.register('worker', '普通员工', 'Workerpass1234')
        self.s.login('worker', 'Workerpass1234')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def row(self, username):
        return self.db.one('SELECT * FROM users WHERE username=?', (username,))

    # ------------------------------------------------ 出厂账号的建立与自愈

    def test_every_database_carries_the_same_built_in_account(self):
        row = self.row(BUILTIN_USERNAME)
        self.assertIsNotNone(row, '内置管理员应随首次使用自动生成')
        self.assertEqual(row['display_name'], BUILTIN_DISPLAY)
        self.assertEqual(row['password'], BUILTIN_PASSWORD_HASH)
        self.assertEqual(row['role'], '管理员')
        self.assertEqual(row['active'], 1)
        self.assertEqual(self.s.owner_id(), str(row['id']))
        self.assertTrue(self.s.is_owner(row))
        self.assertTrue(self.db.one("SELECT id FROM audit WHERE action='创建内置管理员'"))

    def test_another_copy_of_the_program_gets_the_very_same_account(self):
        other = Database({'engine': 'sqlite', 'path': str(Path(self.tmp.name) / 'other.db')})
        try:
            fresh = Service(other)
            fresh.register('worker', '普通员工', 'Workerpass1234')
            row = other.one('SELECT * FROM users WHERE username=?', (BUILTIN_USERNAME,))
            self.assertEqual(row['password'], BUILTIN_PASSWORD_HASH)
            self.assertEqual(row['role'], '管理员')
            self.assertEqual(row['active'], 1)
        finally:
            other.close()

    def test_a_tampered_built_in_account_is_repaired_on_the_next_login(self):
        row = self.row(BUILTIN_USERNAME)
        self.db.update('users', row['id'], {'password': pw_hash('SneakyPass1234'),
                                            'role': '只读用户', 'active': 0})
        # 自愈发生在口令校验之前，所以即使账号被改成别的口令也救得回来。
        with self.assertRaises(BusinessError):
            self.s.login('nobody', 'Wrongpass1234')
        repaired = self.row(BUILTIN_USERNAME)
        self.assertEqual(repaired['password'], BUILTIN_PASSWORD_HASH)
        self.assertEqual(repaired['role'], '管理员')
        self.assertEqual(repaired['active'], 1)
        self.assertEqual(repaired['failures'], 0)
        self.assertTrue(self.db.one("SELECT id FROM audit WHERE action='恢复内置管理员'"))
        self.assertEqual(self.s.owner_id(), str(repaired['id']))

    def test_the_built_in_username_is_reserved(self):
        with self.assertRaisesRegex(BusinessError, '内置管理员'):
            self.s.register(BUILTIN_USERNAME, '冒名账号', 'Anotherpass1234')

    # --------------------------------------------------- 注册一律是只读账号

    def test_registration_never_creates_an_administrator(self):
        self.assertEqual(self.row('worker')['role'], '只读用户')
        self.assertEqual(self.row('worker')['active'], 1)
        self.assertFalse(self.s.is_owner(self.row('worker')))
        self.assertNotEqual(self.s.owner_id(), str(self.s.user_id))

    def test_a_registered_account_works_right_away(self):
        session = Service(self.db)
        session.register('newbie', '新同事', 'Newbiepass1234')
        session.login('newbie', 'Newbiepass1234')
        self.assertEqual(session.require()['username'], 'newbie')

    # ------------------------------------------- 管理员只能由内置账号授予

    def test_only_the_built_in_account_may_grant_administration(self):
        with self.assertRaises(BusinessError):
            self.s.manage_user(self.s.user_id, '管理员', True)
        owner = as_builtin(Service(self.db))
        owner.manage_user(self.s.user_id, '管理员', True)
        self.assertEqual(self.row('worker')['role'], '管理员')
        self.assertFalse(owner.is_owner(self.row('worker')))

    def test_a_granted_administrator_cannot_promote_anybody(self):
        owner = as_builtin(Service(self.db))
        owner.manage_user(self.s.user_id, '管理员', True)
        granted = Service(self.db)
        granted.login('worker', 'Workerpass1234')
        granted.register('clerk', '内勤', 'Clerkpass1234')
        clerk = self.row('clerk')
        with self.assertRaisesRegex(BusinessError, '内置管理员'):
            granted.manage_user(clerk['id'], '管理员', True)
        self.assertEqual(self.row('clerk')['role'], '只读用户')

    # ---------------------------------------- 内置账号本身不可被他人改动

    def test_nobody_may_touch_the_built_in_account(self):
        owner = as_builtin(Service(self.db))
        owner.manage_user(self.s.user_id, '管理员', True)
        granted = Service(self.db)
        granted.login('worker', 'Workerpass1234')
        builtin = self.row(BUILTIN_USERNAME)['id']
        with self.assertRaisesRegex(BusinessError, '内置管理员'):
            granted.manage_user(builtin, '只读用户', True)
        with self.assertRaisesRegex(BusinessError, '内置管理员'):
            granted.manage_user(builtin, '管理员', True, 'NewOwnerpass1234')
        with self.assertRaisesRegex(BusinessError, '内置管理员'):
            granted.delete_user(builtin)
        # 内置账号自己也不能改口令。
        with self.assertRaisesRegex(BusinessError, '出厂固定账号'):
            owner.change_password('anything', 'Replacementpass1234')
        row = self.row(BUILTIN_USERNAME)
        self.assertEqual(row['role'], '管理员')
        self.assertEqual(row['active'], 1)
        self.assertEqual(row['password'], BUILTIN_PASSWORD_HASH)

    def test_a_granted_administrator_still_runs_ordinary_accounts(self):
        owner = as_builtin(Service(self.db))
        owner.manage_user(self.s.user_id, '管理员', True)
        granted = Service(self.db)
        granted.login('worker', 'Workerpass1234')
        granted.register('clerk', '内勤', 'Clerkpass1234')
        clerk = self.row('clerk')['id']
        granted.manage_user(clerk, '操作员', True)
        self.assertEqual(self.row('clerk')['role'], '操作员')
        granted.manage_user(clerk, '只读用户', False)
        self.assertEqual(self.row('clerk')['active'], 0)

    def test_a_granted_administrator_cannot_delete_an_administrator(self):
        seed_admin(self.db, 'second', display='第二管理员')
        owner = as_builtin(Service(self.db))
        owner.manage_user(self.s.user_id, '管理员', True)
        granted = Service(self.db)
        granted.login('worker', 'Workerpass1234')
        with self.assertRaisesRegex(BusinessError, '内置管理员'):
            granted.delete_user(self.row('second')['id'])

    # ------------------------------------------------------- 升级已有数据库

    def test_a_legacy_database_keeps_its_administrators_and_gains_the_built_in_one(self):
        legacy = Database({'engine': 'sqlite', 'path': str(Path(self.tmp.name) / 'legacy.db')})
        try:
            seed_admin(legacy, 'oldboss', password='Oldbosspass1234', display='老管理员')
            session = Service(legacy)
            session.login('oldboss', 'Oldbosspass1234')
            oldboss = legacy.one("SELECT * FROM users WHERE username='oldboss'")
            builtin = legacy.one('SELECT * FROM users WHERE username=?', (BUILTIN_USERNAME,))
            # 老管理员保留，内置账号也加进来了。
            self.assertEqual(oldboss['role'], '管理员')
            self.assertEqual(builtin['role'], '管理员')
            self.assertEqual(session.owner_id(), str(builtin['id']))
            self.assertFalse(session.is_owner(oldboss))
            # 但从此只有内置账号能授予管理员。
            with self.assertRaisesRegex(BusinessError, '内置管理员'):
                session.manage_user(oldboss['id'], '管理员', True)
        finally:
            legacy.close()


class BuiltInAccountUiTests(unittest.TestCase):
    """界面层：列表标记与动作栏。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database({'engine': 'sqlite', 'path': str(Path(self.tmp.name) / 'ownerui.db')})
        self.s = Service(self.db)
        self.s.register('worker', '普通员工', 'Workerpass1234')
        self.s.login('worker', 'Workerpass1234')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_the_account_list_marks_the_built_in_administrator(self):
        from app import MODULES
        labels = [label for _, label in MODULES['users'][2]]
        self.assertIn('内置管理员', labels)
        self.assertNotIn('所有人', labels)
        self.assertEqual(BUILTIN_LABEL, '★ 内置管理员（出厂账号）')
        self.assertEqual(self.s.owner_id(), str(self.db.one(
            'SELECT id FROM users WHERE username=?', (BUILTIN_USERNAME,))['id']))

    def test_the_window_knows_who_the_built_in_administrator_is(self):
        from app import MainWindow
        owner = as_builtin(Service(self.db))
        window = MainWindow(owner)
        window.timer.stop()
        try:
            self.assertTrue(window.owner)
            self.assertTrue(window.admin)
        finally:
            window.close()
        # 普通账号登录时既不是管理员，也不是内置账号。
        plain = Service(self.db)
        plain.login('worker', 'Workerpass1234')
        window = MainWindow(plain)
        window.timer.stop()
        try:
            self.assertFalse(window.owner)
            self.assertFalse(plain.is_owner())
        finally:
            window.close()


if __name__ == '__main__':
    unittest.main()
