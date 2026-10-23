import os,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication,QMessageBox,QSystemTrayIcon
from PySide6.QtTest import QTest
from database import Database,data_dir
from service import BusinessError
from desktop_runtime import DesktopController,SingleInstance,StartupRegistry,startup_command,load_preferences,save_preferences
from app import LoginWindow,MainWindow
from test_support import seed_admin

class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);cls.app.setQuitOnLastWindowClosed(False);cls.app.setWindowIcon(QIcon(str(Path(__file__).with_name('app.ico'))))
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'JINFEN_DATA_DIR':self.temp.name});self.env.start();self.db=Database({'engine':'sqlite','path':str(Path(self.temp.name)/'db.sqlite')});self.controller=None
        self.info=patch.object(QMessageBox,'information',return_value=None);self.info.start();self.icons=patch.object(QSystemTrayIcon,'show',return_value=None);self.icons.start();self.tips=patch.object(QSystemTrayIcon,'showMessage',return_value=None);self.tips.start()
        self.app_quit=patch.object(self.app,'quit',return_value=None);self.app_quit.start()
    def tearDown(self):
        if self.controller:
            self.controller.quit();w=self.controller.current
            if w:w.desktop=None;w.close();w.deleteLater()
            self.controller.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents();self.db.close();self.env.stop();self.icons.stop();self.tips.stop();self.info.stop();self.app_quit.stop();self.temp.cleanup()
    def create(self,startup=False):
        self.controller=DesktopController(self.app,self.db,LoginWindow,MainWindow);self.controller.start(startup);return self.controller
    def login(self,c):
        seed_admin(self.db)
        w=c.current;fields=w.inputs['login'];fields['username'].setText('admin');fields['password'].setText('Password12345');w.login();return c.current
    def test_preferences_roundtrip_and_invalid_json(self):
        save_preferences({'close_to_tray':False,'minimize_to_tray':True,'startup_to_tray':False});self.assertFalse(load_preferences()['close_to_tray']);self.assertTrue(load_preferences()['minimize_to_tray']);(data_dir()/'desktop.json').write_text('broken','utf-8');self.assertTrue(load_preferences()['close_to_tray'])
    def test_legacy_directory_and_modern_precedence(self):
        base=Path(self.temp.name)/'appdata';legacy=base/'QiyunAssets';modern=base/'JinfenAssets';legacy.mkdir(parents=True);(legacy/'assets.db').touch()
        with patch.dict(os.environ,{'JINFEN_DATA_DIR':'','QIYUN_DATA_DIR':'','LOCALAPPDATA':str(base)}):
            self.assertEqual(data_dir(),legacy);modern.mkdir();(modern/'connection.json').write_text('{}');self.assertEqual(data_dir(),modern)
    def test_startup_command_is_quoted_and_requires_exe(self):
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',r'C:\Program Files\JinfenAssets\锦纷资产管理系统.exe'):
            command=startup_command();self.assertTrue(command.startswith('"'));self.assertIn(' --startup',command)
        with patch.object(sys,'frozen',False,create=True):
            with self.assertRaises(RuntimeError):startup_command()
    def test_registry_enable_disable_isolated_key(self):
        if os.name!='nt':self.skipTest('Windows registry only')
        import winreg
        key=os.path.join('Software','JinfenAssetsTests',uuid.uuid4().hex);registry=StartupRegistry(key,'TestOnly')
        try:
            registry.write(True,'"C:\\Test Only\\not-a-real-program.exe" --startup');self.assertIn('--startup',registry.read());registry.write(False);self.assertEqual(registry.read(),'')
        finally:winreg.DeleteKey(winreg.HKEY_CURRENT_USER,key)
    def test_hidden_startup_does_not_log_in(self):
        with patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=True):
            c=self.create(True);self.assertFalse(c.current.isVisible());self.assertIsNone(c.current.service.user_id);c.show_current();self.assertTrue(c.current.isVisible())
    def test_no_tray_fallback_keeps_login_visible(self):
        with patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=False):
            c=self.create(True);self.assertTrue(c.current.isVisible());self.assertFalse(c.hide_to_tray());self.assertTrue(c.current.isVisible())
    def test_closing_main_hides_without_ending_session(self):
        with patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=True):
            c=self.create();main=self.login(c);main.close();self.assertFalse(main.isVisible());self.assertIs(c.current,main);self.assertFalse(c.exiting);self.assertIsNotNone(main.s.user_id);c.show_current();self.assertTrue(main.isVisible())
    def test_logout_opens_fresh_login(self):
        with patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=True):
            c=self.create();main=self.login(c);main.logout();QTest.qWait(50);self.assertIsInstance(c.current,LoginWindow);self.assertIsNone(c.current.service.user_id);self.assertTrue(c.current.isVisible())
    def test_minimize_can_hide_to_tray(self):
        with patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=True):
            c=self.create();main=self.login(c);c.preferences['minimize_to_tray']=True;main.showMinimized();QTest.qWait(50);self.assertFalse(main.isVisible());self.assertFalse(c.exiting)
    def test_disabled_close_to_tray_exits(self):
        with patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=True):
            c=self.create();c.preferences['close_to_tray']=False;c.current.close();QTest.qWait(50);self.assertTrue(c.exiting)
    def test_single_instance_signals_primary(self):
        messages=[];first=SingleInstance(lambda:messages.append('activate'));second=SingleInstance(lambda:None)
        try:
            self.assertTrue(first.acquire());self.assertFalse(second.acquire());QTest.qWait(100);self.assertEqual(messages,['activate'])
        finally:second.close();first.close()

if __name__=='__main__':unittest.main(verbosity=2)
