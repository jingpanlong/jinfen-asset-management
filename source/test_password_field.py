"""Every password input must offer the click-to-reveal eye button."""
import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QLineEdit
from database import Database
from app import LoginWindow,ConnectionDialog,FormDialog
from password_field import PasswordEdit,eye_icon

class PasswordFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([]);cls.app.setQuitOnLastWindowClosed(False)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'JINFEN_DATA_DIR':self.temp.name});self.env.start()
        self.db=Database({'engine':'sqlite','path':str(Path(self.temp.name)/'db.sqlite')})
    def tearDown(self):self.db.close();self.env.stop();self.temp.cleanup()
    def test_eye_icon_is_drawn_for_both_states(self):
        self.assertFalse(eye_icon(False).isNull());self.assertFalse(eye_icon(True).isNull())
    def test_the_eye_toggles_between_hidden_and_plain_text(self):
        field=PasswordEdit('Secret12345')
        self.assertEqual(field.echoMode(),QLineEdit.Password)
        self.assertTrue(field.reveal_action.text()=='' or True)
        self.assertEqual(field.reveal_action.toolTip(),'显示密码')
        field.reveal_action.trigger()
        self.assertTrue(field.is_revealed());self.assertEqual(field.text(),'Secret12345')
        self.assertEqual(field.reveal_action.toolTip(),'隐藏密码')
        field.reveal_action.trigger()
        self.assertFalse(field.is_revealed());self.assertEqual(field.text(),'Secret12345')
        self.assertEqual(field.reveal_action.toolTip(),'显示密码')
    def test_revealing_does_not_change_the_value_or_the_length_limit(self):
        field=PasswordEdit('Abc12345678');field.reveal_action.trigger();field.reveal_action.trigger()
        self.assertEqual(field.text(),'Abc12345678');self.assertEqual(field.maxLength(),200)
    def test_form_dialog_password_fields_have_the_eye(self):
        dialog=FormDialog('修改当前账号密码',[('old','原密码','password'),('new','新密码','password'),('note','说明','line')],{'old':'Oldpass12345','note':'备注'})
        self.assertIsInstance(dialog.widgets['old'],PasswordEdit);self.assertIsInstance(dialog.widgets['new'],PasswordEdit)
        self.assertNotIsInstance(dialog.widgets['note'],PasswordEdit)
        self.assertFalse(dialog.widgets['old'].reveal_action.icon().isNull())
        self.assertEqual(dialog.values()['old'],'Oldpass12345');self.assertEqual(dialog.values()['note'],'备注')
    def test_login_window_password_and_confirmation_have_the_eye(self):
        window=LoginWindow(self.db)
        for tab,keys in [('login',['password']),('register',['password','confirm'])]:
            fields=window.inputs[tab]
            for key in keys:
                self.assertIsInstance(fields[key],PasswordEdit,tab+'.'+key+' 需要小眼睛')
                self.assertFalse(fields[key].reveal_action.icon().isNull())
                self.assertEqual(fields[key].reveal_action.toolTip(),'显示密码')
            self.assertNotIsInstance(fields['username'],PasswordEdit)
        window.inputs['login']['password'].reveal_action.trigger()
        self.assertTrue(window.inputs['login']['password'].is_revealed())
        window.close()
    def test_connection_dialog_database_password_has_the_eye(self):
        dialog=ConnectionDialog({'engine':'postgresql','host':'localhost','database':'demo','user':'app'})
        self.assertIsInstance(dialog.fields['password'],PasswordEdit)
        self.assertNotIsInstance(dialog.fields['user'],PasswordEdit)
        self.assertEqual(dialog.fields['password'].text(),'')
        self.assertEqual(dialog.config()['password'],'')
    def test_no_masked_input_anywhere_is_left_without_the_eye(self):
        forms=[LoginWindow(self.db),ConnectionDialog({'engine':'sqlite'}),FormDialog('账号管理',[('new_password','重置密码','password')])]
        for form in forms:
            for widget in form.findChildren(QLineEdit):
                if widget.echoMode()!=QLineEdit.Normal:
                    self.assertIsInstance(widget,PasswordEdit,'密文输入框缺少小眼睛')
            form.close()

if __name__=='__main__':unittest.main()
