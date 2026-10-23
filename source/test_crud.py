"""Every data set must offer create / read / update / delete, with deletion safe.

Deletion is allowed only while nothing references the record; otherwise the
service refuses and explains why. Records marked as logs stay removable by the
administrator only.
"""
import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PIL import Image
from PySide6.QtWidgets import QApplication
from database import Database
from enhanced import Service
from service import BusinessError
from asset_photos import upload_images
from access import BUILTINS,REMOVAL,BUILTIN_USERNAME
from test_support import as_builtin,seed_admin
from app import MainWindow

class CrudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([]);cls.app.setQuitOnLastWindowClosed(False)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name);self.env=patch.dict(os.environ,{'JINFEN_DATA_DIR':self.temp.name});self.env.start()
        self.db=Database({'engine':'sqlite','path':str(self.folder/'db.sqlite')});self.s=Service(self.db);seed_admin(self.db);self.s.login('admin','Password12345')
        self.s.save_asset({'id':'A1','name':'电脑 A1','category':'电脑','location':'一楼'});self.window=None
    def tearDown(self):
        if self.window:self.window.timer.stop();self.window.auto_backup_timer.stop();self.window.close()
        self.db.close();self.env.stop();self.temp.cleanup()
    def revision(self,aid='A1'):return self.s.check_asset(aid)['revision']
    def photo(self,name='p.png'):
        path=self.folder/name;Image.new('RGB',(80,60),'steelblue').save(path);return path
    def login(self,username,role,permissions):
        self.s.save_role(username+'角色',permissions);self.s.register(username,'测试'+username,'Password12345')
        uid=self.db.one('SELECT id FROM users WHERE username=?',(username,))['id'];self.s.manage_user(uid,role,True)
        self.s.assign_access(uid,{'roles':[username+'角色'],'allow':[],'deny':[],'scope':'all','locations':[],'employee_id':'','expires':''})
        other=Service(self.db);other.login(username,'Password12345');return other

    # ---- 资产：只有未被引用时才能删除
    def test_fresh_asset_can_be_deleted_without_leaving_history(self):
        self.s.delete_asset('A1')
        self.assertEqual(self.s.list('assets'),[]);self.assertEqual([e for e in self.s.list('events') if e['asset_id']=='A1'],[])
        self.assertIn('删除资产',[a['action'] for a in self.s.list('audit')])
    def test_asset_with_flow_history_is_refused(self):
        self.s.catalog('employees',{'id':'E1','name':'张三','active':1});self.s.flow('A1',self.revision(),'领用','E1')
        with self.assertRaisesRegex(BusinessError,'流转历史'):self.s.delete_asset('A1')
        self.assertEqual(len(self.s.list('assets')),1)
    def test_asset_with_attachment_repair_task_or_count_is_refused(self):
        upload_images(self.s,'A1',[self.photo()])
        with self.assertRaisesRegex(BusinessError,'附件'):self.s.delete_asset('A1')
        aid=self.s.attachments('A1')[0]['id'];self.s.remove_attachment(aid)
        self.s.save_task({'title':'保养','asset_id':'A1'})
        with self.assertRaisesRegex(BusinessError,'待办'):self.s.delete_asset('A1')
        self.s.delete_task(self.s.list('tasks')[0]['id'])
        self.s.new_count('季度盘点')
        with self.assertRaisesRegex(BusinessError,'盘点明细'):self.s.delete_asset('A1')
        self.s.delete_count(self.s.list('counts')[0]['id'])
        self.s.flow('A1',self.revision(),'送修','','','','风扇异响','维修店')
        with self.assertRaisesRegex(BusinessError,'维修工单'):self.s.delete_asset('A1')
    # ---- 基础资料：引用检查
    def test_catalog_delete_respects_references(self):
        self.s.catalog('departments',{'id':'D1','name':'行政部'});self.s.catalog('employees',{'id':'E1','name':'张三','department_id':'D1','active':1})
        self.s.catalog('suppliers',{'id':'S1','name':'某供应商'})
        with self.assertRaisesRegex(BusinessError,'员工档案引用'):self.s.delete_catalog('departments','D1')
        self.s.catalog('employees',{'id':'E1','name':'张三','department_id':'','active':1},True)
        self.s.delete_catalog('departments','D1');self.assertEqual(self.s.list('departments'),[])
        self.s.save_asset({'id':'A2','name':'设备','category':'电脑','supplier_id':'S1'})
        with self.assertRaisesRegex(BusinessError,'资产引用'):self.s.delete_catalog('suppliers','S1')
        self.s.save_asset({'id':'A2','name':'设备','category':'电脑','supplier_id':''},self.revision('A2'))
        self.s.delete_catalog('suppliers','S1');self.assertEqual(self.s.list('suppliers'),[])
        self.s.flow('A1',self.revision(),'领用','E1')
        with self.assertRaisesRegex(BusinessError,'资产引用'):self.s.delete_catalog('employees','E1')
    def test_asset_without_flow_does_not_block_employee_delete(self):
        self.s.catalog('employees',{'id':'E1','name':'张三','active':1})
        self.s.delete_catalog('employees','E1');self.assertEqual(self.s.list('employees'),[])
    # ---- 采购：改内容与删除的状态边界
    def test_purchase_edit_and_delete_state_rules(self):
        pid=self.s.new_purchase({'name':'显示器','category':'显示器','quantity':2,'budget':'3000','reason':'扩充工位'})
        self.s.update_purchase(pid,{'name':'显示器 27寸','category':'显示器','quantity':3,'budget':'4500','reason':'扩充工位'})
        p=self.db.one('SELECT * FROM purchases WHERE id=?',(pid,));self.assertEqual((p['name'],p['quantity'],p['budget_cents']),('显示器 27寸',3,450000))
        seed_admin(self.db,'boss',display='老板')
        other=Service(self.db);other.login('boss','Password12345');other.approve_purchase(pid,True,'同意')
        with self.assertRaisesRegex(BusinessError,'待审批'):self.s.update_purchase(pid,{'name':'x','reason':'y'})
        with self.assertRaisesRegex(BusinessError,'不能删除'):self.s.delete_purchase(pid)
        self.assertEqual(len(self.s.list('purchases')),1)
    def test_rejected_purchase_can_be_deleted(self):
        pid=self.s.new_purchase({'name':'打印机','quantity':1,'budget':'1000','reason':'试用'})
        seed_admin(self.db,'boss2',display='老板2')
        other=Service(self.db);other.login('boss2','Password12345');other.approve_purchase(pid,False,'预算不足')
        self.s.delete_purchase(pid);self.assertEqual(self.s.list('purchases'),[])
    # ---- 盘点 / 待办 / 维修 / 附件
    def test_count_delete_removes_items(self):
        self.s.new_count('年度盘点');count=self.s.list('counts')[0]
        self.assertTrue([i for i in self.s.list('count_items') if i['count_id']==count['id']])
        self.s.delete_count(count['id'])
        self.assertEqual(self.s.list('counts'),[]);self.assertEqual([i for i in self.s.list('count_items') if i['count_id']==count['id']],[])
    def test_task_edit_and_delete(self):
        tid=self.s.save_task({'title':'旧标题','asset_id':'A1','priority':'普通'})
        self.s.update_task({'id':tid,'title':'新标题','asset_id':'','priority':'紧急','due_date':'2026-12-31','notes':'备注'})
        t=self.db.one('SELECT * FROM tasks WHERE id=?',(tid,));self.assertEqual((t['title'],t['priority'],t['asset_id']),('新标题','紧急',''))
        self.s.finish_task(tid)
        with self.assertRaisesRegex(BusinessError,'重新开启'):self.s.update_task({'id':tid,'title':'再改'})
        self.s.delete_task(tid);self.assertEqual(self.s.list('tasks'),[])
    def test_repair_edit_and_delete_respects_open_ticket(self):
        self.s.flow('A1',self.revision(),'送修','','','','屏幕闪烁','快修店');repair=self.s.list('repairs')[0]
        with self.assertRaisesRegex(BusinessError,'维修中'):self.s.delete_repair(repair['id'])
        self.s.update_repair(repair['id'],{'problem':'屏幕闪烁并返修','vendor':'快修店','cost':'120','result':'更换排线'})
        row=self.db.one('SELECT * FROM repairs WHERE id=?',(repair['id'],));self.assertEqual((row['problem'],row['cost_cents']),('屏幕闪烁并返修',12000))
        self.s.flow('A1',self.revision(),'维修完成','','','','已更换排线','','120')
        self.s.delete_repair(repair['id']);self.assertEqual(self.s.list('repairs'),[])
    def test_attachment_rename_keeps_extension_and_validates_name(self):
        upload_images(self.s,'A1',[self.photo('设备照片.png')]);aid=self.s.attachments('A1')[0]['id']
        self.s.rename_attachment(aid,'机房正面')
        self.assertEqual(self.s.attachments('A1')[0]['filename'],'机房正面.png')
        with self.assertRaisesRegex(BusinessError,'不能包含'):self.s.rename_attachment(aid,'非法/名称')
        with self.assertRaisesRegex(BusinessError,'没有变化'):self.s.rename_attachment(aid,'机房正面')
    # ---- 留痕记录：仅管理员可删
    def test_admin_can_delete_log_records(self):
        self.s.save_asset({'id':'A3','name':'设备','category':'电脑'})
        event=[e for e in self.s.list('events') if e['asset_id']=='A3'][0]
        self.s.delete_log('events',event['id']);self.assertEqual([e for e in self.s.list('events') if e['asset_id']=='A3'],[])
        auditor=self.login('loguser','只读用户',sorted(BUILTINS['只读用户']))
        with self.assertRaisesRegex(BusinessError,'无权执行'):auditor.delete_log('audit',self.s.list('audit')[0]['id'])
    # ---- 账号删除边界
    def test_user_delete_boundaries(self):
        self.s.register('temp','临时','Password12345');uid=self.db.one("SELECT id FROM users WHERE username='temp'")['id']
        self.s.delete_user(uid);self.assertIsNone(self.db.one('SELECT id FROM users WHERE id=?',(uid,)))
        with self.assertRaisesRegex(BusinessError,'当前正在登录'):self.s.delete_user(self.s.user_id)
        self.s.register('solo','唯一','.Password12345');self.assertIsNotNone(self.db.one("SELECT id FROM users WHERE username='solo'"))
    def test_built_in_and_administrator_deletion_boundaries(self):
        deleter=self.login('deleter','操作员',['users.delete'])
        builtin=self.db.one('SELECT id FROM users WHERE username=?',(BUILTIN_USERNAME,))['id']
        # 内置管理员是出厂固定账号，谁都不能删
        with self.assertRaisesRegex(BusinessError,'内置管理员'):deleter.delete_user(builtin)
        self.assertIsNotNone(self.db.one('SELECT id FROM users WHERE id=?',(builtin,)))
        seed_admin(self.db,'admin2',display='二号');uid=self.db.one("SELECT id FROM users WHERE username='admin2'")['id']
        # 管理员账号只能由内置管理员删除
        with self.assertRaisesRegex(BusinessError,'内置管理员'):deleter.delete_user(uid)
        with self.assertRaisesRegex(BusinessError,'内置管理员'):self.s.delete_user(uid)
        self.assertIsNotNone(self.db.one('SELECT id FROM users WHERE id=?',(uid,)))
        as_builtin(Service(self.db)).delete_user(uid)
        self.assertIsNone(self.db.one('SELECT id FROM users WHERE username=?',('admin2',)))
    # ---- 权限拦截：删除权限默认不授予操作员
    def test_operator_role_has_no_removal_permissions(self):
        self.assertFalse(set(BUILTINS['操作员'])&REMOVAL)
        operator=self.login('oper','操作员',sorted(BUILTINS['操作员']))
        with self.assertRaisesRegex(BusinessError,'无权执行'):operator.delete_asset('A1')
        self.assertEqual(len(self.s.list('assets')),1)
    # ---- 界面接线：每个模块都有删除入口
    def main(self):
        self.window=MainWindow(self.s);self.window.timer.stop();self.window.auto_backup_timer.stop();self.window.show();self.app.processEvents();return self.window
    def actions(self,window):
        found={}
        for index in range(window.action_row.count()):
            widget=window.action_row.itemAt(index).widget()
            if widget is None:continue
            menu=widget.menu() if hasattr(widget,'menu') else None
            catalog={a.text():a for a in menu.actions()} if menu else {widget.text():widget}
            found.update(catalog)
        return found
    def test_every_module_exposes_its_delete_action(self):
        window=self.main()
        expected={'assets':'删除资产','employees':'删除','departments':'删除','suppliers':'删除','purchases':'删除申请','repairs':'删除工单','counts':'删除盘点','tasks':'删除待办','users':'删除账号','events':'删除记录','audit':'删除记录','import_batches':'删除记录'}
        for page,label in expected.items():
            window.navigate(page);self.app.processEvents()
            found=self.actions(window)
            self.assertIn(label,found,f'{page} 缺少「{label}」入口')
            self.assertTrue(found[label].isEnabled(),f'{page} 的「{label}」应当可用')
        window.navigate('employees');self.app.processEvents()
        self.assertIn('删除',self.actions(window))
    def test_operator_ui_disables_removal_but_keeps_editing(self):
        operator=self.login('uiop','操作员',sorted(BUILTINS['操作员']))
        self.window=MainWindow(operator);self.window.timer.stop();self.window.auto_backup_timer.stop()
        self.window.navigate('tasks');self.app.processEvents()
        found=self.actions(self.window)
        self.assertFalse(found['删除待办'].isEnabled(),'无删除权限时按钮应禁用')
        self.assertTrue(found['编辑待办'].isEnabled(),'编辑权限应仍然可用')

if __name__=='__main__':unittest.main()
