import json,os,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from database import Database
from enhanced import Service
from service import BusinessError,LABELS
from analytics import Analytics
from access import PERMISSIONS
from test_support import seed_admin

class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name);self.db=Database({'engine':'sqlite','path':str(self.folder/'db.sqlite')})
        seed_admin(self.db,display='总管理员');self.admin=Service(self.db);self.admin.login('admin','Password12345')
        self.admin.register('worker','普通员工','Password12345');self.uid=self.db.one("SELECT id FROM users WHERE username='worker'")['id'];self.admin.manage_user(self.uid,'操作员',True)
        self.admin.catalog('departments',{'id':'D1','name':'研发部','manager':'','notes':''})
        self.admin.catalog('employees',{'id':'E1','name':'张三','department_id':'D1','phone':'','email':'','notes':'','active':1})
        for uid,location in [('A1','一楼'),('A2','二楼')]:self.admin.save_asset({'id':uid,'name':uid,'category':'电脑','location':location,'quantity':1})
        self.worker=Service(self.db)
    def tearDown(self):self.db.close();self.temp.cleanup()
    def config(self,**kwargs):
        c={'roles':['操作员'],'allow':[],'deny':[],'scope':'all','locations':[],'employee_id':'','expires':''};c.update(kwargs);return c
    def login(self,**kwargs):self.admin.assign_access(self.uid,self.config(**kwargs));self.worker.login('worker','Password12345');return self.worker
    def test_multiple_roles_and_deny_priority(self):
        self.admin.save_role('导出专员',['data.export'],'导出许可');w=self.login(roles=['资产维护员','导出专员'],deny=['assets.edit'],allow=['assets.scrap'])
        self.assertTrue(w.can('data.export'));self.assertTrue(w.can('assets.scrap'));self.assertFalse(w.can('assets.edit'))
        with self.assertRaises(BusinessError):w.save_asset(dict(self.admin.list('assets')[0],name='非法'),1)
    def test_location_read_write_export_and_import_boundary(self):
        w=self.login(scope='locations',locations=['一楼']);self.assertEqual([a['id'] for a in w.list('assets')],['A1'])
        with self.assertRaises(BusinessError):w.flow('A2',1,'移动位置',location='一楼')
        with self.assertRaises(BusinessError):w.flow('A1',1,'移动位置',location='二楼')
        with self.assertRaises(BusinessError):w.export_assets(self.folder/'bad.csv',self.admin.list('assets'))
        self.assertFalse((self.folder/'bad.csv').exists())
        rows=[{'编号':'A3','设备名称':'新设备','类别':'电脑','数量':1,'存放位置':'二楼'}]
        plan=w.plan_import(rows,'assets','仅新增');self.assertTrue(plan['errors'])
        with self.assertRaises(BusinessError):w.apply_import(rows,'assets','仅新增','bad.csv')
        self.assertIsNone(self.db.one("SELECT id FROM assets WHERE id='A3'"))
    def test_self_scope_and_related_rows(self):
        self.admin.flow('A1',1,'领用','E1');w=self.login(scope='self',employee_id='E1')
        self.assertEqual([a['id'] for a in w.list('assets')],['A1']);self.assertEqual({e['asset_id'] for e in w.list('events')},{'A1'})
        with self.assertRaises(BusinessError):w.save_asset({'id':'A3','name':'新设备','category':'电脑'})
        w.flow('A1',2,'归还');self.assertEqual(w.list('assets'),[])
    def test_attachment_direct_id_cannot_bypass_scope(self):
        file=self.folder/'contract.txt';file.write_text('confidential');self.admin.upload_attachments('A2',[file]);aid=self.admin.attachments('A2')[0]['id'];w=self.login(scope='locations',locations=['一楼'])
        for call in [lambda:w.attachments('A2'),lambda:w.download_attachment(aid,self.folder/'stolen.txt'),lambda:w.remove_attachment(aid)]:
            with self.assertRaises(BusinessError):call()
        self.assertFalse((self.folder/'stolen.txt').exists())
    def test_revocation_blocks_existing_analytics_snapshot(self):
        w=self.login();m=Analytics(w);m.load();self.admin.assign_access(self.uid,self.config(deny=['data.export','analytics.read']))
        with self.assertRaises(BusinessError):m.chart('status',{})
        with self.assertRaises(BusinessError):w.export_assets(self.folder/'revoked.csv')
        w.login('worker','Password12345');self.assertFalse(w.can('data.export'))
    def test_expired_authorization_blocks_login(self):
        self.admin.assign_access(self.uid,self.config(expires=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()))
        with self.assertRaisesRegex(BusinessError,'到期'):self.worker.login('worker','Password12345')
    def test_delegated_approval_without_admin_or_self_approval(self):
        pid=self.admin.new_purchase({'name':'服务器','budget':'1000','reason':'扩容'});w=self.login(roles=['采购审批员'])
        w.approve_purchase(pid,True,'同意');self.assertEqual(self.db.one('SELECT status FROM purchases WHERE id=?',(pid,))['status'],'已批准')
        with self.assertRaises(BusinessError):w.save_role('非法',['data.restore'])
        self.assertFalse(w.page_allowed('dashboard'));self.assertTrue(w.page_allowed('purchases'))
    def test_scoped_count_does_not_include_other_location(self):
        w=self.login(scope='locations',locations=['一楼']);cid=w.new_count('一楼盘点')
        self.assertEqual([r['asset_id'] for r in self.db.all('SELECT * FROM count_items WHERE count_id=?',(cid,))],['A1'])
    def test_limited_accounts_cannot_maintain_global_catalog_or_backup(self):
        w=self.login(scope='locations',locations=['一楼'],allow=['data.backup'])
        with self.assertRaises(BusinessError):w.catalog('departments',{'id':'D2','name':'越界'})
        with self.assertRaises(BusinessError):w.backup(self.folder/'bad.json')
    def test_backup_roundtrip_and_legacy_resets_grants(self):
        self.admin.save_role('自定义',['assets.read']);self.admin.assign_access(self.uid,self.config(roles=['自定义'],scope='locations',locations=['一楼']))
        file=self.folder/'backup.json';self.admin.backup(file);data=json.loads(file.read_text('utf-8'));self.assertIn('access_policy',data)
        self.admin.restore(file);self.admin.login('admin','Password12345');self.assertEqual(self.admin.policy()['users'][self.uid]['roles'],['自定义'])
        data.pop('access_policy');file.write_text(json.dumps(data));self.admin.restore(file);self.admin.login('admin','Password12345');self.assertEqual(self.admin.policy()['users'],{})
    def test_role_deletion_and_protected_admin(self):
        self.admin.save_role('自定义',['assets.read']);self.admin.assign_access(self.uid,self.config(roles=['自定义']))
        with self.assertRaises(BusinessError):self.admin.delete_role('自定义')
        with self.assertRaises(BusinessError):self.admin.assign_access(self.admin.user_id,self.config(roles=[]))
        self.admin.reset_access(self.uid);self.admin.delete_role('自定义')
    def test_denied_operation_audit_and_sensitive_read(self):
        w=self.login(deny=['data.export','employees.read']);self.assertEqual(w.list('employees'),[])
        with self.assertRaises(BusinessError):w.export_assets(self.folder/'bad.csv')
        self.assertTrue(any(a['action']=='权限拒绝' for a in self.admin.list('audit')))
    def test_reset_password_and_restore_invalidate_other_session(self):
        w=self.login();self.admin.manage_user(self.uid,'操作员',True,'Newpassword12345')
        with self.assertRaises(BusinessError):w.list('assets')
        w.login('worker','Newpassword12345');file=self.folder/'backup.json';self.admin.backup(file);self.admin.restore(file)
        with self.assertRaises(BusinessError):w.list('assets')
    def test_update_import_with_edit_only_grant(self):
        w=self.login(roles=[],allow=['assets.read','assets.edit','data.import'])
        rows=[{'编号':'A1','设备名称':'改名','类别':'电脑','数量':1,'存放位置':'一楼'}]
        plan=w.plan_import(rows,'assets','按编号新增或更新')
        self.assertFalse(plan['errors']);w.apply_import(rows,'assets','按编号新增或更新','update.csv',plan)
        self.assertEqual(self.db.one("SELECT name FROM assets WHERE id='A1'")['name'],'改名')

if __name__=='__main__':unittest.main()
