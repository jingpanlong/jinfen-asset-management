"""The wait cursor must never cover a modal dialog.

A regression test for the bug where every form, detail window and message box
opened from a toolbar action inherited the application-wide wait cursor, so the
pointer kept spinning over the whole dialog.
"""
import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog
from database import Database
from enhanced import Service
from service import BusinessError
from app import MainWindow,FormDialog,handle
from busy_cursor import manager,push_busy,pop_busy,reset_busy
from test_support import seed_admin

class BusyCursorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([]);cls.app.setQuitOnLastWindowClosed(False)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'JINFEN_DATA_DIR':self.temp.name});self.env.start();reset_busy()
        self.db=Database({'engine':'sqlite','path':str(Path(self.temp.name)/'db.sqlite')});self.s=Service(self.db);seed_admin(self.db);self.s.login('admin','Password12345');self.window=None
    def tearDown(self):
        reset_busy()
        if self.window:self.window.timer.stop();self.window.auto_backup_timer.stop();self.window.close()
        self.db.close();self.env.stop();self.temp.cleanup()
    def main(self):
        self.window=MainWindow(self.s);self.window.timer.stop();self.window.auto_backup_timer.stop();self.window.navigate('assets');self.window.show();self.app.processEvents();return self.window
    def test_wait_cursor_hidden_while_modal_dialog_is_visible(self):
        push_busy()
        self.assertIsNotNone(QApplication.overrideCursor(),'普通后台工作期间应显示等待光标')
        dialog=QDialog();dialog.setModal(True);dialog.show();QTest.qWait(400)
        self.assertIsNone(QApplication.overrideCursor(),'模态弹窗打开时不应显示等待光标')
        dialog.close();QTest.qWait(400)
        self.assertIsNotNone(QApplication.overrideCursor(),'弹窗关闭后应恢复等待光标')
        pop_busy()
        self.assertIsNone(QApplication.overrideCursor())
    def test_handle_balances_cursor_on_success_and_failure(self):
        window=self.main()
        self.assertEqual(handle(window,lambda:'done'),'done')
        self.assertIsNone(QApplication.overrideCursor())
        with patch('app.QMessageBox.warning') as warned:
            handle(window,lambda:(_ for _ in ()).throw(BusinessError('无法完成')))
        self.assertTrue(warned.called);self.assertIsNone(QApplication.overrideCursor(),'失败路径也不能残留等待光标')
    def test_nested_handle_calls_restore_only_at_the_outermost_end(self):
        window=self.main()
        def inner():
            handle(window,lambda:None)
            return QApplication.overrideCursor() is not None
        self.assertTrue(handle(window,inner),'内层操作结束后外层仍在工作，等待光标应保持')
        self.assertIsNone(QApplication.overrideCursor())
    def test_add_asset_action_keeps_normal_pointer_over_the_form(self):
        captured={}
        def probe(dialog):
            dialog.setModal(True);dialog.show();QTest.qWait(400)
            captured['cursor']=QApplication.overrideCursor();captured['photos']=dialog.extra is not None
            dialog.close();return QDialog.Rejected
        window=self.main()
        with patch.object(FormDialog,'exec',probe):
            handle(window,window.asset_edit)
        self.assertIn('cursor',captured);self.assertIsNone(captured['cursor'],'新增资产弹窗不应继承等待光标')
        self.assertTrue(captured['photos'],'弹窗应带有资产图片选择区域')
        self.assertIsNone(QApplication.overrideCursor())

if __name__=='__main__':unittest.main()
