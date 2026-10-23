import os,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PIL import Image
from PySide6.QtCore import Qt,QPoint,QPointF,QMimeData,QUrl
from PySide6.QtGui import QDragEnterEvent,QDropEvent
from PySide6.QtWidgets import QApplication,QDialog,QLabel
from PySide6.QtTest import QTest
from database import Database
from enhanced import Service
from service import BusinessError
from asset_photos import upload_images,image_list,image_bytes,table_thumbnails,thumbnail
from asset_photo_ui import AssetDetailDialog,PhotoPicker
from app import MainWindow
from test_support import seed_admin

class PhotoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([]);cls.app.setQuitOnLastWindowClosed(False)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name);self.env=patch.dict(os.environ,{'JINFEN_DATA_DIR':self.temp.name});self.env.start();self.db=Database({'engine':'sqlite','path':str(self.folder/'db.sqlite')});self.s=Service(self.db);seed_admin(self.db);self.s.login('admin','Password12345')
        for aid,location in [('A1','一楼'),('A2','二楼')]:self.s.save_asset({'id':aid,'name':'电脑 '+aid,'category':'电脑','location':location})
        self.file=self.folder/'设备照片.png';Image.new('RGB',(120,80),'steelblue').save(self.file);self.window=None
    def tearDown(self):
        if self.window:self.window.timer.stop();self.window.auto_backup_timer.stop();self.window.close()
        self.db.close();self.env.stop();self.temp.cleanup()
    def test_upload_thumbnail_and_backup_roundtrip(self):
        upload_images(self.s,'A1',[self.file]);row=image_list(self.s,'A1')[0];self.assertEqual(image_bytes(self.s,row['id']),self.file.read_bytes());self.assertIn('A1',table_thumbnails(self.s,['A1','A2']))
        backup=self.folder/'backup.json';self.s.backup(backup);self.s.restore(backup);self.s.login('admin','Password12345');self.assertEqual(image_bytes(self.s,row['id']),self.file.read_bytes())
    def test_invalid_multi_upload_has_no_partial_write(self):
        bad=self.folder/'bad.jpg';bad.write_text('not an image')
        with self.assertRaises(BusinessError):upload_images(self.s,'A1',[self.file,bad])
        self.assertEqual(image_list(self.s,'A1'),[])
    def test_large_pixel_image_rejected(self):
        path=self.folder/'large.png';Image.new('1',(5000,5000)).save(path)
        with self.assertRaisesRegex(BusinessError,'像素'):upload_images(self.s,'A1',[path])
    def test_scoped_and_readonly_photo_access(self):
        upload_images(self.s,'A2',[self.file]);aid=image_list(self.s,'A2')[0]['id'];self.s.register('viewer','只读','Password12345');uid=self.db.one("SELECT id FROM users WHERE username='viewer'")['id'];self.s.manage_user(uid,'只读用户',True)
        self.s.assign_access(uid,{'roles':['只读用户'],'allow':[],'deny':[],'scope':'locations','locations':['一楼'],'employee_id':'','expires':''});viewer=Service(self.db);viewer.login('viewer','Password12345')
        with self.assertRaises(BusinessError):upload_images(viewer,'A1',[self.file])
        with self.assertRaises(BusinessError):image_bytes(viewer,aid)
        self.assertEqual(table_thumbnails(viewer,['A1','A2']),{})
    def test_corrupt_image_skipped_in_table(self):
        upload_images(self.s,'A1',[self.file]);a=image_list(self.s,'A1')[0];self.db.update('attachments',a['id'],{'sha256':'0'*64});self.assertEqual(table_thumbnails(self.s,['A1']),{})
        with self.assertRaises(BusinessError):image_bytes(self.s,a['id'])
    def main(self):
        self.window=MainWindow(self.s);self.window.timer.stop();self.window.auto_backup_timer.stop();self.window.navigate('assets');self.window.show();self.app.processEvents();return self.window
    def test_real_table_drop_targets_hovered_asset(self):
        w=self.main();row=next(i for i,r in enumerate(w.visible) if r['id']=='A2');pos=w.table.visualItemRect(w.table.item(row,0)).center();mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(self.file))])
        enter=QDragEnterEvent(pos,Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier);self.app.sendEvent(w.table.viewport(),enter);self.assertTrue(enter.isAccepted())
        drop=QDropEvent(QPointF(pos),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier);self.app.sendEvent(w.table.viewport(),drop);self.assertTrue(drop.isAccepted());self.assertEqual(len(image_list(self.s,'A2')),1);self.assertEqual(image_list(self.s,'A1'),[])
        photo=next(j for j in range(w.table.columnCount()) if w.table.horizontalHeaderItem(j).text()=='资产图片');self.assertFalse(w.table.item(row,photo).icon().isNull())
    def test_double_click_every_column_opens_correct_asset(self):
        w=self.main();opened=[]
        with patch.object(AssetDetailDialog,'exec',lambda d:opened.append(d.asset_id) or QDialog.Accepted):
            for column in range(w.table.columnCount()):
                item=w.table.item(0,column);w.table.scrollToItem(item);self.app.processEvents();pos=w.table.visualItemRect(item).center();QTest.mouseClick(w.table.viewport(),Qt.LeftButton,pos=pos);QTest.mouseDClick(w.table.viewport(),Qt.LeftButton,pos=pos)
        self.assertEqual(opened,[w.visible[0]['id']]*w.table.columnCount());self.assertFalse(w.findChild(QLabel,'brandIcon').pixmap().isNull())
    def test_detail_drop_preview_and_readonly_buttons(self):
        d=AssetDetailDialog(self.s,'A1');d.show();self.app.processEvents();pos=QPoint(40,40);mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(self.file))]);self.app.sendEvent(d.preview,QDragEnterEvent(pos,Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier));drop=QDropEvent(QPointF(pos),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier);self.app.sendEvent(d.preview,drop);self.assertEqual(len(d.photos),1);self.assertFalse(d.preview.pixmap().isNull());d.close()
    def test_picker_stages_and_validates_selected_files(self):
        picker=PhotoPicker();picker.add_paths([str(self.file)]);self.assertEqual(picker.paths,[str(self.file)]);self.assertEqual(picker.gallery.count(),1)
        picker.add_paths([str(self.file)]);self.assertEqual(len(picker.paths),1,'同一张图片不应重复加入')
        bad=self.folder/'bad.jpg';bad.write_text('not an image')
        with patch('asset_photo_ui.QMessageBox.warning') as warned:picker.add_paths([str(bad)])
        self.assertTrue(warned.called);self.assertEqual(len(picker.paths),1,'无效图片不应进入待上传列表')
        picker.gallery.setCurrentRow(0);picker.remove_selected();self.assertEqual(picker.paths,[]);self.assertEqual(picker.gallery.count(),0)
        picker.add_paths([str(self.file)]);picker.clear();self.assertEqual(picker.paths,[]);self.assertIn('留空',picker.count.text())
    def test_picker_drag_and_drop_stages_files(self):
        picker=PhotoPicker();pos=QPoint(30,30);mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(self.file))])
        enter=QDragEnterEvent(pos,Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier);self.app.sendEvent(picker.drop,enter);self.assertTrue(enter.isAccepted())
        drop=QDropEvent(QPointF(pos),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier);self.app.sendEvent(picker.drop,drop)
        self.assertEqual(picker.paths,[str(self.file)]);self.assertEqual(picker.gallery.count(),1)
    def test_add_asset_dialog_uploads_staged_photos_on_save(self):
        w=self.main();captured={}
        def fake_exec(dialog):
            dialog.widgets['id'].setText('A9');dialog.widgets['name'].setText('新设备');dialog.widgets['category'].setText('电脑')
            captured['picker']=dialog.extra;dialog.extra.add_paths([str(self.file)]);return QDialog.Accepted
        with patch('app.FormDialog.exec',fake_exec),patch('app.message'):
            w.asset_edit()
        self.assertIsInstance(captured['picker'],PhotoPicker);self.assertEqual(len(image_list(self.s,'A9')),1);self.assertEqual(image_list(self.s,'A1'),[])
        def fake_edit(dialog):
            captured['edit']=dialog.extra;dialog.extra.add_paths([str(self.file)]);return QDialog.Accepted
        with patch('app.FormDialog.exec',fake_edit),patch('app.message'):
            w.asset_edit(next(r for r in self.s.list('assets') if r['id']=='A1'))
        self.assertEqual(len(image_list(self.s,'A1')),1)

if __name__=='__main__':unittest.main()
