import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from database import Database,protect
from service import Service,BusinessError,money,write_table
from access import BUILTIN_USERNAME
from test_support import as_builtin,seed_admin

class SystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database({'engine':'sqlite','path':str(Path(self.tmp.name)/'test.db')});self.s=Service(self.db)
        seed_admin(self.db,password='Adminpass12345');self.s.login('admin','Adminpass12345')
        self.admin=self.s.user_id
        self.s.catalog('departments',{'id':'D1','name':'研发部','manager':'负责人','notes':''})
        self.s.catalog('employees',{'id':'E1','name':'张三','department_id':'D1','phone':'','email':'','active':1,'notes':''})
        self.s.catalog('employees',{'id':'E2','name':'李四','department_id':'D1','phone':'','email':'','active':1,'notes':''})
        self.asset={'id':'A1','name':'办公电脑','category':'笔记本','quantity':1,'price_cents':600000,'life_months':36,'residual_bp':500,'purchase_date':'2024-01-01','location':'研发办公室'}
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_auth_roles_and_account_review(self):
        self.assertFalse(self.s.register('viewer','只读用户','Viewerpass12345'))
        # 注册后立即可用，但只能是只读：不能写入，也进不了账号与权限。
        self.s.login('viewer','Viewerpass12345')
        with self.assertRaises(BusinessError):self.s.save_asset(self.asset)
        with self.assertRaises(BusinessError):self.s.list('users')
        self.s.change_password('Viewerpass12345','Newviewer12345')
        with self.assertRaises(BusinessError):self.s.login('viewer','Viewerpass12345')
        self.s.login('viewer','Newviewer12345')
        viewer=self.db.one("SELECT * FROM users WHERE username='viewer'")
        self.assertEqual(viewer['role'],'只读用户');self.assertEqual(viewer['active'],1)
        self.assertNotEqual(self.s.owner_id(),str(viewer['id']))
        self.s.login('admin','Adminpass12345')
        self.assertNotEqual(self.s.owner_id(),str(self.admin))
        with self.assertRaises(BusinessError):self.s.manage_user(self.admin,'只读用户',True)
        # 管理员只能由内置管理员账号授予；被授予者不能反过来动内置账号，也不能再授予别人。
        with self.assertRaises(BusinessError):self.s.manage_user(viewer['id'],'管理员',True)
        builtin=self.db.one('SELECT id FROM users WHERE username=?',(BUILTIN_USERNAME,))['id']
        as_builtin(Service(self.db)).manage_user(viewer['id'],'管理员',True)
        granted=Service(self.db);granted.login('viewer','Newviewer12345')
        self.assertFalse(granted.is_owner())
        with self.assertRaises(BusinessError):granted.manage_user(builtin,'只读用户',True)
        with self.assertRaises(BusinessError):granted.manage_user(self.admin,'只读用户',True)
    def test_lockout_persists_and_unlock(self):
        for _ in range(5):
            with self.assertRaises(BusinessError):self.s.login('admin','incorrect')
        u=self.db.one('SELECT * FROM users WHERE id=?',(self.admin,));self.assertEqual(u['failures'],5);self.assertTrue(u['locked_until'])
        with self.assertRaises(BusinessError):self.s.login('admin','Adminpass12345')
        # 管理员账号只能由内置管理员账号解锁
        with self.assertRaises(BusinessError):self.s.manage_user(self.admin,'管理员',True)
        as_builtin(Service(self.db)).manage_user(self.admin,'管理员',True)
        self.s.login('admin','Adminpass12345')

    def test_the_built_in_account_can_never_be_locked_out(self):
        for _ in range(5):
            with self.assertRaises(BusinessError):self.s.login(BUILTIN_USERNAME,'incorrect')
        # 内置账号的试错锁定会被自动清除：它必须永远进得去。
        self.s.login('admin','Adminpass12345')
        row=self.db.one('SELECT * FROM users WHERE username=?',(BUILTIN_USERNAME,));self.assertEqual(row['failures'],0);self.assertEqual(row['locked_until'],'')
    def test_asset_lifecycle_and_stale_edit(self):
        a=self.s.save_asset(self.asset)
        self.s.flow('A1',a['revision'],'领用',employee_id='E1')
        with self.assertRaises(BusinessError):self.s.save_asset(self.asset,a['revision'])
        with self.assertRaises(BusinessError):self.s.catalog('employees',{'id':'E1','name':'张三','department_id':'D1','active':0},True)
        a=self.db.one("SELECT * FROM assets WHERE id='A1'");self.s.flow('A1',a['revision'],'调拨',employee_id='E2')
        a=self.db.one("SELECT * FROM assets WHERE id='A1'");self.s.flow('A1',a['revision'],'送修',note='无法开机',vendor='服务商')
        r=self.s.list('repairs')[0];self.assertEqual(r['status'],'维修中')
        a=self.db.one("SELECT * FROM assets WHERE id='A1'");self.s.flow('A1',a['revision'],'维修完成',note='更换电源',cost='123.45')
        self.assertEqual(self.s.list('repairs')[0]['cost_cents'],12345)
        a=self.db.one("SELECT * FROM assets WHERE id='A1'");self.s.flow('A1',a['revision'],'报废',note='达到使用年限')
        self.assertEqual(self.db.one("SELECT * FROM assets WHERE id='A1'")['status'],'已报废')
        with self.assertRaises(BusinessError):self.s.save_asset(self.asset,a['revision']+1)
        self.assertEqual(len(self.s.list('events')),6)
    def test_purchase_approval_receive_and_atomic_rollback(self):
        self.s.register('operator','采购员','Operator12345');u=self.db.one("SELECT * FROM users WHERE username='operator'");self.s.manage_user(u['id'],'操作员',True);self.s.login('operator','Operator12345')
        id=self.s.new_purchase({'name':'电脑','quantity':2,'budget':'10000','reason':'新员工入职'})
        with self.assertRaises(BusinessError):self.s.approve_purchase(id,True,'')
        self.s.login('admin','Adminpass12345');self.s.approve_purchase(id,True,'通过')
        with self.assertRaises(BusinessError):self.s.receive_purchase(id,self.asset)
        self.assertEqual(len(self.s.list('assets')),0)
        self.s.receive_purchase(id,{**self.asset,'quantity':2});self.assertEqual(self.s.list('purchases')[0]['status'],'已入库')
        with self.assertRaises(BusinessError):self.s.receive_purchase(id,{**self.asset,'id':'A2','quantity':2})
    def test_self_approval_rejected(self):
        id=self.s.new_purchase({'name':'电脑','quantity':1,'budget':'1','reason':'测试'})
        with self.assertRaises(BusinessError):self.s.approve_purchase(id,True,'通过')
    def test_inventory_difference_and_finalization(self):
        self.s.save_asset(self.asset);id=self.s.new_count('年度盘点');item=self.s.list('count_items')[0]
        with self.assertRaises(BusinessError):self.s.close_count(id)
        self.s.check_item(item['id'],0,'设备未找到');self.s.close_count(id)
        with self.assertRaises(BusinessError):self.s.check_item(item['id'],1,'重新找到')
        self.assertEqual(self.s.list('count_items')[0]['result'],'差异');self.assertEqual(self.s.list('assets')[0]['quantity'],1)
    def test_excel_import_export_history_and_atomic_duplicates(self):
        path=Path(self.tmp.name)/'import.xlsx'
        write_table(path,['编号','设备名称','数量','类别','单价','残值率','使用人1'],[['A1','笔记本',1,'电脑','6000','5','2024 年曾由张三使用'],['A2','显示器',1,'显示器','500','0','']])
        self.assertEqual(self.s.import_assets(path),2);self.assertEqual(self.s.list('assets')[0]['price_cents'],600000)
        self.assertTrue(any(e['action']=='原表历史' for e in self.s.list('events')))
        output=Path(self.tmp.name)/'out.xlsx';self.s.export_assets(output);self.assertTrue(output.exists())
        other=Path(self.tmp.name)/'bad.csv';write_table(other,['编号','设备名称','数量'],[['NEW','新设备',1],['A1','重复',1]])
        with self.assertRaises(BusinessError):self.s.import_assets(other)
        self.assertIsNone(self.db.one("SELECT * FROM assets WHERE id='NEW'"))
    def test_browser_json_migration(self):
        file=Path(self.tmp.name)/'web.json';file.write_text(json.dumps({'version':1,'assets':[{'id':'OLD','name':'旧设备','quantity':1,'category':'电脑','status':'在用','user':'张三','history':[{'at':'2024','action':'领用','detail':'张三'}]}]}),'utf-8')
        self.s.import_assets(file);a=self.s.list('assets')[0];self.assertEqual(a['status'],'闲置');self.assertIn('张三',a['notes']);self.assertEqual(len(self.s.list('events')),2)
    def test_backup_restore_validation(self):
        self.s.save_asset(self.asset);file=Path(self.tmp.name)/'backup.json';self.s.backup(file)
        data=json.loads(file.read_text('utf-8'));self.s.validate_backup(data)
        self.s.save_asset({**self.asset,'id':'A2'});self.s.restore(file);self.assertIsNone(self.s.user_id)
        self.s.login('admin','Adminpass12345');self.assertEqual(len(self.s.list('assets')),1)
        data['tables']['assets'][0]['status']='在用'
        with self.assertRaises(BusinessError):self.s.validate_backup(data)
    def test_depreciation_and_money_validation(self):
        a=self.s.save_asset(self.asset);self.assertEqual(self.s.depreciation(a,date(2025,1,1)),(600000,190000,410000));self.assertEqual(self.s.depreciation(a,date(2030,1,1)),(600000,570000,30000))
        self.assertEqual(money('10.005'),1001)
        for bad in ['NaN','Infinity','-1','word']:
            with self.assertRaises(BusinessError):money(bad)
    def test_dpapi_roundtrip(self):
        if os.name=='nt':self.assertEqual(protect(protect('not-a-real-password'),True),'not-a-real-password')
    def test_second_connection_stale_revision(self):
        a=self.s.save_asset(self.asset);other=Database(self.db.config);s2=Service(other)
        try:
            s2.login('admin','Adminpass12345');s2.save_asset({**self.asset,'name':'其他客户端修改'},a['revision'])
            with self.assertRaises(BusinessError):self.s.save_asset(self.asset,a['revision'])
        finally:other.close()

if __name__=='__main__':unittest.main(verbosity=2)
