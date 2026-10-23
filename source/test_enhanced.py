import json
import tempfile
import unittest
from pathlib import Path
from database import Database
from enhanced import Service
from service import BusinessError,write_table
from test_support import seed_admin

class EnhancedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.db=Database({'engine':'sqlite','path':str(self.path/'db.sqlite')});self.s=Service(self.db);seed_admin(self.db);self.s.login('admin','Password12345')
        self.asset={'id':'A1','name':'电脑','category':'电脑主机','quantity':1,'price_cents':10000,'life_months':36,'residual_bp':500}
    def tearDown(self):self.db.close();self.temp.cleanup()
    def test_four_templates_import(self):
        for kind in ['departments','suppliers','employees','assets']:
            for ext in ['.csv','.xlsx']:
                target=self.path/(kind+ext);self.s.template(target,kind);records=self.s.read_import(target);plan=self.s.plan_import(records,kind,'仅新增');self.assertFalse(plan['errors']);self.assertEqual(plan['total'],1)
            self.s.apply_import(records,kind,'仅新增',target,plan)
        self.assertEqual(len(self.s.list('import_batches')),4)
    def test_preview_errors_no_partial_write(self):
        records=[{'编号':'A1','设备名称':'电脑','数量':1},{'编号':'A2','设备名称':'无效','数量':0},{'编号':'A1','设备名称':'重复','数量':1}]
        plan=self.s.plan_import(records,'assets','仅新增');self.assertEqual(len(plan['errors']),2)
        with self.assertRaises(BusinessError):self.s.apply_import(records,'assets','仅新增','bad.csv',plan)
        self.assertFalse(self.s.list('assets'))
    def test_update_preserves_assignment_and_detects_preview_race(self):
        self.s.save_asset(self.asset);self.s.catalog('employees',{'id':'E1','name':'张三','active':1});self.s.flow('A1',1,'领用',employee_id='E1')
        records=[{'编号':'A1','设备名称':'新名称','数量':1,'类别':'电脑','单价':'10'}];plan=self.s.plan_import(records,'assets','按编号新增或更新')
        self.s.apply_import(records,'assets','按编号新增或更新','update.csv',plan);a=self.s.list('assets')[0];self.assertEqual(a['employee_id'],'E1');self.assertEqual(a['status'],'在用');self.assertEqual(a['name'],'新名称')
        plan=self.s.plan_import(records,'assets','按编号新增或更新');self.s.save_asset({**a,'name':'其他操作已修改'},a['revision'])
        with self.assertRaises(BusinessError):self.s.apply_import(records,'assets','按编号新增或更新','race.csv',plan)
    def test_excel_sheet_selection(self):
        from openpyxl import Workbook
        file=self.path/'sheets.xlsx';book=Workbook();book.active.title='说明';book.active.append(['说明']);ws=book.create_sheet('资产');ws.append(['编号','设备名称','数量']);ws.append(['A1','电脑',1]);book.save(file)
        self.assertEqual(self.s.sheet_names(file),['说明','资产']);records=self.s.read_import(file,'资产');self.assertFalse(self.s.plan_import(records,'assets','仅新增')['errors'])
    def test_batch_flow_atomic(self):
        a=self.s.save_asset(self.asset);b=self.s.save_asset({**self.asset,'id':'A2'});self.s.catalog('employees',{'id':'E1','name':'张三','active':1});self.s.batch_flow([a,b],'领用',employee_id='E1');current=self.s.list('assets')
        self.assertTrue(all(r['status']=='在用' for r in current));self.s.batch_flow(current,'归还');current=self.s.list('assets');self.s.flow('A2',current[1]['revision'],'送修',note='故障')
        with self.assertRaises(BusinessError):self.s.batch_flow(current,'领用',employee_id='E1')
        self.assertEqual(self.s.db.one("SELECT * FROM assets WHERE id='A1'")['status'],'闲置')
    def test_attachment_roundtrip_backup_and_remove(self):
        self.s.save_asset(self.asset);file=self.path/'保修单.txt';file.write_text('保修凭证：原文','utf-8');self.s.upload_attachments('A1',[file]);a=self.s.attachments('A1')[0];download=self.path/'download.txt';self.s.download_attachment(a['id'],download);self.assertEqual(file.read_bytes(),download.read_bytes())
        backup=self.path/'backup.json';self.s.backup(backup);self.s.validate_backup(json.loads(backup.read_text('utf-8')));self.s.remove_attachment(a['id']);self.assertFalse(self.s.attachments('A1'));self.s.restore(backup);self.s.login('admin','Password12345');self.assertEqual(len(self.s.attachments('A1')),1)
        invalid=self.path/'bad.exe';invalid.write_bytes(b'fake')
        with self.assertRaises(BusinessError):self.s.upload_attachments('A1',[invalid])
    def test_attachment_corruption_rejected(self):
        self.s.save_asset(self.asset);file=self.path/'doc.txt';file.write_text('text');self.s.upload_attachments('A1',[file]);backup=self.path/'data.json';self.s.backup(backup);data=json.loads(backup.read_text('utf-8'));data['tables']['attachments'][0]['sha256']='0'*64
        with self.assertRaises(BusinessError):self.s.validate_backup(data)
    def test_tasks_completion_reopen(self):
        self.s.save_asset(self.asset);id=self.s.save_task({'title':'检查电脑保修','asset_id':'A1','priority':'紧急','due_date':'2026-12-01'});self.s.finish_task(id);self.assertEqual(self.s.list('tasks')[0]['status'],'已完成');self.s.finish_task(id,True);self.assertEqual(self.s.list('tasks')[0]['status'],'待处理')
    def test_legacy_backup_and_schema_migration(self):
        self.s.save_asset(self.asset);file=self.path/'backup.json';self.s.backup(file);data=json.loads(file.read_text('utf-8'));data['version']=2
        for key in ['attachments','tasks','import_batches']:data['tables'].pop(key)
        file.write_text(json.dumps(data),'utf-8');self.s.restore(file);self.s.login('admin','Password12345');self.assertEqual(len(self.s.list('assets')),1)
        self.db.execute("UPDATE meta SET value='2' WHERE key='schema_version'");other=Database(self.db.config)
        try:self.assertEqual(other.one("SELECT value FROM meta WHERE key='schema_version'")['value'],'3');self.assertEqual(len(other.rows('assets')),1)
        finally:other.close()
    def test_viewer_cannot_import_upload_batch_or_tasks(self):
        self.s.save_asset(self.asset);self.s.register('viewer','只读','Viewerpass12345');u=self.db.one("SELECT * FROM users WHERE username='viewer'");self.s.manage_user(u['id'],'只读用户',True);self.s.login('viewer','Viewerpass12345')
        for fn in [lambda:self.s.plan_import([{'编号':'A2','设备名称':'电脑'}],'assets','仅新增'),lambda:self.s.batch_flow(self.s.list('assets'),'归还'),lambda:self.s.save_task({'title':'任务'})]:
            with self.assertRaises(BusinessError):fn()

if __name__=='__main__':unittest.main(verbosity=2)
