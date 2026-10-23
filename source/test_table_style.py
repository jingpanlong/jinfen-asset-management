"""Every table renders its header titles and data cells centred.

The alignment is enforced by a paint-time delegate, so a column added later - or
a table rebuilt on every refresh - cannot drift out of line.  Check boxes and
combo boxes are widgets rather than items, so they get their own wrapper check.
"""
import os,tempfile,unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QCheckBox,QComboBox,QDialog,QHBoxLayout,QStyleOptionViewItem,QTableWidget,QTableWidgetItem,QWidget
from database import Database
from enhanced import Service
from app import MainWindow,MODULES,handle
from access import PERMISSIONS
from access_ui import RolesDialog,UserAccessDialog
from ui_extras import AttachmentDialog,ImportWizard
from table_style import CenterDelegate,center_cells,center_header,center_table,cell_box
from test_support import seed_admin

class TableCenteringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([]);cls.app.setQuitOnLastWindowClosed(False)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name);self.env=patch.dict(os.environ,{'JINFEN_DATA_DIR':self.temp.name});self.env.start()
        self.db=Database({'engine':'sqlite','path':str(self.folder/'db.sqlite')});self.s=Service(self.db);seed_admin(self.db);self.s.login('admin','Password12345');self.window=None
    def tearDown(self):
        if self.window:self.window.timer.stop();self.window.auto_backup_timer.stop();self.window.close()
        self.db.close();self.env.stop();self.temp.cleanup()
    def revision(self,aid):return self.s.check_asset(aid)['revision']
    def seed(self):
        """One record per module so every table really has rows to align."""
        self.s.catalog('departments',{'id':'D1','name':'行政部'});self.s.catalog('employees',{'id':'E1','name':'张三','department_id':'D1','active':1})
        self.s.catalog('suppliers',{'id':'S1','name':'某供应商'})
        self.s.save_asset({'id':'A-1','name':'居中验证设备','category':'电脑主机','supplier_id':'S1','quantity':1,'price_cents':100000,'life_months':36,'residual_bp':500})
        self.s.flow('A-1',self.revision('A-1'),'领用','E1')
        self.s.flow('A-1',self.revision('A-1'),'送修','','','','风扇异响','维修店')
        self.s.save_asset({'id':'A-2','name':'保修到期设备','category':'电脑主机','quantity':1,'price_cents':100000,'life_months':36,'residual_bp':500,'warranty_date':date.today().isoformat()})
        self.s.new_purchase({'name':'显示器','category':'显示器','quantity':2,'budget':'3000','reason':'扩充工位'})
        self.s.new_count('居中盘点');self.s.save_task({'title':'保养','asset_id':'A-1'})
        template=self.folder/'assets.csv';self.s.template(template);records=self.s.read_import(template)
        self.s.apply_import(records,'assets','仅新增',template,self.s.plan_import(records,'assets','仅新增'))
    def shown(self,table,row=0):
        """The alignment the delegate actually paints for one cell."""
        option=QStyleOptionViewItem();table.itemDelegate().initStyleOption(option,table.model().index(row,0));return option.displayAlignment
    def assert_centred(self,table,label):
        self.assertIsInstance(table.itemDelegate(),CenterDelegate,label+' 应使用居中绘制代理')
        self.assertEqual(table.horizontalHeader().defaultAlignment(),Qt.AlignCenter,label+' 表头默认对齐应为居中')
        for column in range(table.columnCount()):
            item=table.horizontalHeaderItem(column)
            if item is not None:self.assertEqual(item.textAlignment(),Qt.AlignCenter,label+' 第 '+str(column)+' 列表头应为居中')
        self.assertGreater(table.rowCount(),0,label+' 应有数据行可供检查')
        self.assertEqual(self.shown(table),Qt.AlignCenter,label+' 数据单元格应为居中')
    def test_helpers_centre_header_and_cells(self):
        table=QTableWidget(1,2);table.setHorizontalHeaderLabels(['编号','名称']);center_table(table)
        table.setItem(0,0,QTableWidgetItem('A-1'));table.setItem(0,1,QTableWidgetItem('设备'))
        self.assertEqual(self.shown(table),Qt.AlignCenter)
        self.assertEqual(table.horizontalHeaderItem(1).textAlignment(),Qt.AlignCenter)
    def test_centring_survives_a_column_change(self):
        table=QTableWidget(1,1);center_cells(table)
        table.setColumnCount(3);table.setHorizontalHeaderLabels(['甲','乙','丙']);center_header(table);table.setItem(0,2,QTableWidgetItem('丙值'))
        self.assertEqual(self.shown(table,0),Qt.AlignCenter)
        self.assertEqual([table.horizontalHeaderItem(i).textAlignment() for i in range(3)],[Qt.AlignCenter]*3)
    def test_cell_widgets_are_centred_in_their_cell(self):
        box=cell_box(QCheckBox(),max_width=120)
        self.assertIsInstance(box,QWidget);layout=box.layout()
        self.assertIsInstance(layout,QHBoxLayout)
        self.assertIsNotNone(layout.itemAt(0).spacerItem(),'左侧应留弹性空间')
        self.assertIsNotNone(layout.itemAt(2).spacerItem(),'右侧应留弹性空间')
        self.assertEqual(box.findChild(QCheckBox).maximumWidth(),120)
    def test_every_module_page_centres_the_ledger(self):
        self.seed();self.window=MainWindow(self.s);self.window.timer.stop();self.window.auto_backup_timer.stop();self.window.show()
        for page in MODULES:
            self.window.navigate(page);self.app.processEvents()
            self.assert_centred(self.window.table,page+' 列表')
    def test_dashboard_and_count_dialog_tables_are_centred(self):
        self.seed();self.window=MainWindow(self.s);self.window.timer.stop();self.window.auto_backup_timer.stop();self.window.show()
        self.window.navigate('dashboard');self.app.processEvents()
        # Superseded widgets are only queued for deletion, so read the live layout.
        layout=self.window.dashboard_layout
        alerts=[layout.itemAt(i).widget() for i in range(layout.count())]
        alerts=[widget for widget in alerts if isinstance(widget,QTableWidget)]
        self.assertEqual(len(alerts),1,'总览应包含一张提醒表')
        self.assert_centred(alerts[0],'总览提醒表')
        self.window.navigate('counts');self.app.processEvents();self.window.table.setCurrentCell(0,0)
        captured={}
        def probe(dialog):
            captured['table']=dialog.findChildren(QTableWidget)[0];dialog.close();return QDialog.Rejected
        with patch.object(QDialog,'exec',probe):handle(self.window,self.window.count_detail)
        self.assert_centred(captured['table'],'盘点明细表')
    def test_permission_matrices_centre_their_widgets(self):
        self.seed();first=next(iter(PERMISSIONS))
        roles=RolesDialog(self.s);self.assert_centred(roles.table,'角色权限矩阵')
        wrapper=roles.table.cellWidget(0,1)
        self.assertIsInstance(wrapper,QWidget,'复选单元应被居中容器包裹')
        self.assertIs(wrapper.findChild(QCheckBox),roles.checks[first])
        user=self.db.one("SELECT * FROM users WHERE username='admin'");user['role']='操作员'
        access=UserAccessDialog(self.s,user);self.assert_centred(access.table,'账号高级授权矩阵')
        box=access.table.cellWidget(0,1)
        self.assertIsInstance(box,QWidget,'下拉单元应被居中容器包裹')
        self.assertIs(box.findChild(QComboBox),access.overrides[first])
        roles.close();access.close()
    def test_import_preview_and_attachment_tables_are_centred(self):
        self.seed()
        template=self.folder/'assets.csv';self.s.template(template)
        wizard=ImportWizard(self.s,template)
        self.assert_centred(wizard.preview,'导入预览表');self.assert_centred(wizard.errors,'导入错误表');wizard.close()
        photo=self.folder/'warranty.txt';photo.write_text('attachment',encoding='utf-8');self.s.upload_attachments('A-1',[photo])
        attachments=AttachmentDialog(self.s,self.s.check_asset('A-1'))
        self.assert_centred(attachments.table,'附件列表');attachments.close()

if __name__=='__main__':unittest.main()
