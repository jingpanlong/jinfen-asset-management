import unittest
import tempfile
from datetime import date
from pathlib import Path
from database import Database
from enhanced import Service
from analytics import Analytics,CHARTS,month_shift
from service import BusinessError
from test_support import seed_admin

class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database({'engine':'sqlite','path':str(Path(self.tmp.name)/'db')});self.s=Service(self.db);seed_admin(self.db);self.s.login('admin','Password12345')
        self.s.catalog('departments',{'id':'D1','name':'研发部'});self.s.catalog('employees',{'id':'E1','name':'张三','department_id':'D1','active':1})
        self.s.save_asset({'id':'A1','name':'电脑','category':'电脑','quantity':2,'price_cents':100000,'life_months':36,'residual_bp':500,'purchase_date':'2026-01-01','warranty_date':'2026-11-01','location':'办公室'})
        self.s.save_asset({'id':'A2','name':'显示器','category':'显示器','quantity':1,'price_cents':20000,'life_months':36,'residual_bp':500,'location':'办公室'})
        self.s.flow('A1',1,'领用',employee_id='E1');self.s.new_purchase({'name':'设备','category':'电脑','quantity':1,'budget':'123.45','reason':'测试'})
        self.model=Analytics(self.s);self.model.load();self.conf={'asof':'2026-10-12','months':12}
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_status_quantity_and_drill_totals(self):
        chart=self.model.chart('status',self.conf);self.assertEqual(sum(p['value'] for p in chart['points']),3);self.assertEqual(next(p for p in chart['points'] if p['label']=='在用')['value'],2)
        for p in chart['points']:self.assertEqual(p['value'],sum(a['quantity'] for a in p['rows']))
    def test_filters_and_undated(self):
        self.assertEqual(len(self.model.filter({'department':'D1'})),1)
        self.assertEqual(len(self.model.filter({'status':'闲置'})),1)
        conf={'date_enabled':True,'start':'2026-01-01','end':'2026-12-31','include_undated':False};self.assertEqual(len(self.model.filter(conf)),1);conf['include_undated']=True;self.assertEqual(len(self.model.filter(conf)),2)
        with self.assertRaises(BusinessError):self.model.filter({'date_enabled':True,'start':'2026-12-01','end':'2026-01-01'})
    def test_metrics_unit_and_use_rate(self):
        metrics=self.model.metrics(self.model.filter({}),date(2026,10,12));self.assertEqual(metrics['original'],2200);self.assertAlmostEqual(metrics['use_rate'],200/3)
        self.assertLess(metrics['net'],metrics['original']);self.assertEqual(self.model.metrics([])['use_rate'],0)
    def test_department_does_not_invent_idle_ownership(self):
        chart=self.model.chart('department',self.conf);self.assertEqual(len(chart['points']),1);self.assertEqual(chart['points'][0]['label'],'研发部');self.assertEqual(chart['points'][0]['value'],2)
    def test_acquisition_and_budget_units(self):
        chart=self.model.chart('acquisitions',self.conf);self.assertEqual(sum(p['value'] for p in chart['points']),2000)
        today=date.today().isoformat();chart=self.model.chart('purchase_budget',{'asof':today,'months':12,'category':'不存在'});self.assertAlmostEqual(sum(p['value'] for p in chart['points']),123.45)
    def test_future_net_value_nonincreasing(self):
        values=[p['value'] for p in self.model.chart('forecast',self.conf)['points']];self.assertEqual(len(values),13);self.assertTrue(all(b<=a for a,b in zip(values,values[1:])))
    def test_heatmap_matches_event_counts(self):
        chart=self.model.chart('heatmap',{'asof':date.today().isoformat(),'months':6});self.assertEqual(len(chart['points']),36);self.assertEqual(sum(p['value'] for p in chart['points']),1)
    def test_all_chart_datasets_and_zero_data(self):
        for key in CHARTS:
            chart=self.model.chart(key,self.conf);self.assertEqual(chart['title'],CHARTS[key]);self.assertTrue(all(p['value']>=0 for p in chart['points']))
            self.model.chart(key,dict(self.conf,category='不存在'))
    def test_presets_personal_persistence(self):
        self.model.save_preset('部门分析',dict(self.conf,department='D1'));self.assertEqual(self.model.presets()['部门分析']['department'],'D1')
        self.s.register('viewer','只读用户','Viewerpass12345');u=self.db.one("SELECT * FROM users WHERE username='viewer'");self.s.manage_user(u['id'],'只读用户',True);self.s.login('viewer','Viewerpass12345');other=Analytics(self.s);self.assertEqual(other.presets(),{});other.save_preset('个人方案',self.conf);self.assertIn('个人方案',other.presets())
        with self.assertRaises(BusinessError):self.s.save_task({'title':'无权限'})
    def test_snapshot_is_stable_until_refresh(self):
        before=self.model.metrics(self.model.filter({}))['quantity'];self.s.save_asset({'id':'A3','name':'新设备','category':'其他','quantity':1,'life_months':36,'residual_bp':500});self.assertEqual(self.model.metrics(self.model.filter({}))['quantity'],before);self.model.load();self.assertEqual(self.model.metrics(self.model.filter({}))['quantity'],before+1)

if __name__=='__main__':unittest.main(verbosity=2)
