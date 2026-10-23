"""Read-only analytics, explicit units and reproducible chart datasets."""
import json
from collections import defaultdict
from datetime import date,datetime
from decimal import Decimal
from service import BusinessError,day,stamp

STATUS=['闲置','在用','维修中','已报废']
CHARTS={
 'status':'资产状态环形图','category':'资产类别对比','department':'部门领用对比',
 'value_rank':'资产原值排行','acquisitions':'月度购入原值',
 'repairs':'维修费用趋势','purchase_budget':'采购申请预算趋势',
 'purchase_status':'采购审批分布','forecast':'未来净值测算',
 'scatter':'设备使用年限与单价','heatmap':'资产流转热力图','warranty':'保修风险分布',
}

def month_shift(d,n):
    index=d.year*12+d.month-1+n
    return date(index//12,index%12+1,1)

def month_key(value):
    if not value:return ''
    try:
        if len(value)==10:return date.fromisoformat(value).strftime('%Y-%m')
        return datetime.fromisoformat(value).astimezone().strftime('%Y-%m')
    except (ValueError,TypeError):return ''

def full_months(start,end):
    return max(0,(end.year-start.year)*12+end.month-start.month-(end.day<start.day))

def dataset(title,kind,unit,points,**kwargs):
    return dict(title=title,kind=kind,unit=unit,points=points,**kwargs)

class Analytics:
    def __init__(self,service):self.s=service;self.snapshot=None
    def load(self):
        with self.s.db.transaction():
            self.s.permit('analytics.read');self.s.permit('assets.read')
            self.snapshot={table:self.s.list(table) for table in ['assets','employees','departments','repairs','purchases','events']}
        self.snapshot['loaded_at']=stamp();return self.snapshot
    def options(self):
        self.s.permit('analytics.read')
        data=self.snapshot or self.load()
        return {'categories':sorted({a['category'] for a in data['assets']}),'locations':sorted({a['location'] for a in data['assets'] if a['location']}),'departments':[(d['id'],d['name']) for d in data['departments']]}
    def filter(self,config):
        self.s.permit('analytics.read')
        data=self.snapshot or self.load();employees={e['id']:e for e in data['employees']}
        start=day(config.get('start',''));end=day(config.get('end',''))
        if config.get('date_enabled') and start and end and start>end:raise BusinessError('开始日期不能晚于结束日期')
        result=[]
        for a in data['assets']:
            if config.get('category') and a['category']!=config['category']:continue
            if config.get('status') and a['status']!=config['status']:continue
            if config.get('location') and a['location']!=config['location']:continue
            if config.get('department'):
                dept=employees.get(a['employee_id'],{}).get('department_id','')
                if dept!=config['department']:continue
            if config.get('date_enabled'):
                purchased=a['purchase_date']
                if not purchased:
                    if not config.get('include_undated',True):continue
                elif (start and purchased<start) or (end and purchased>end):continue
            result.append(a)
        return result
    def metrics(self,assets,asof=None):
        asof=asof or date.today();total=sum(a['quantity'] for a in assets);active=sum(a['quantity'] for a in assets if a['status']!='已报废');used=sum(a['quantity'] for a in assets if a['status']=='在用')
        original=net=0
        for a in assets:
            cost,_,value=self.s.depreciation(a,asof);original+=cost;net+=value
        return dict(quantity=total,records=len(assets),use_rate=used/active*100 if active else 0,original=original/100,net=net/100,used=used,active=active)
    def chart(self,key,config):
        self.s.permit('analytics.read')
        if key not in CHARTS:raise BusinessError('图表类型无效')
        data=self.snapshot or self.load();assets=self.filter(config);ids={a['id'] for a in assets};asof=date.fromisoformat(day(config.get('asof') or date.today().isoformat(),False));period=int(config.get('months',12))
        if period not in [6,12,24]:raise BusinessError('趋势周期仅支持 6、12、24 个月')
        months=[month_shift(asof,i).strftime('%Y-%m') for i in range(1-period,1)]
        employees={e['id']:e for e in data['employees']};departments={d['id']:d['name'] for d in data['departments']}
        common_note='范围：当前资产筛选结果。数量按台账数量统计；点击数据项可查看对应明细。'
        def point(label,value,rows,**extra):return dict(label=label,value=value,rows=rows,**extra)
        def group(field,value):
            grouped=defaultdict(list)
            for a in assets:grouped[field(a)].append(a)
            return [point(label,sum(value(a) for a in rows),rows) for label,rows in grouped.items()]
        if key=='status':
            points=[point(s,sum(a['quantity'] for a in assets if a['status']==s),[a for a in assets if a['status']==s]) for s in STATUS]
            return dataset(CHARTS[key],'donut','件',points,note=common_note)
        if key=='category':
            points=sorted(group(lambda a:a['category'],lambda a:a['quantity']),key=lambda p:p['value'],reverse=True)
            if len(points)>10:
                tail=points[9:];points=points[:9]+[point('其他类别',sum(p['value'] for p in tail),[a for p in tail for a in p['rows']])]
            return dataset(CHARTS[key],'bar','件',points,note=common_note)
        if key=='department':
            groups=defaultdict(list)
            for a in assets:
                if a['status']=='在用':groups[departments.get(employees.get(a['employee_id'],{}).get('department_id'),'未登记部门')].append(a)
            points=sorted([point(name,sum(a['quantity'] for a in rows),rows) for name,rows in groups.items()],key=lambda p:p['value'],reverse=True)
            if len(points)>10:
                tail=points[9:];points=points[:9]+[point('其他部门',sum(p['value'] for p in tail),[a for p in tail for a in p['rows']])]
            return dataset(CHARTS[key],'bar','件',points,note='只统计当前在用且关联员工的资产；部门来自员工资料，未领用资产不分配虚构部门。')
        if key=='value_rank':
            points=[point(a['id']+' · '+a['name'],a['price_cents']*a['quantity']/100,[a]) for a in sorted(assets,key=lambda a:a['price_cents']*a['quantity'],reverse=True)[:10]]
            return dataset(CHARTS[key],'bar','元',points,note='当前筛选资产的总原值排行前 10 项：单价 × 数量。包含报废与否由状态筛选决定。')
        if key=='acquisitions':
            points=[point(m,sum(a['price_cents']*a['quantity'] for a in assets if month_key(a['purchase_date'])==m)/100,[a for a in assets if month_key(a['purchase_date'])==m]) for m in months]
            return dataset(CHARTS[key],'line','元',points,note='按购入日期分月汇总当前台账原值；未填写购入日期的资产不进入此趋势。不是历史库存快照。')
        if key=='repairs':
            repairs=[r for r in data['repairs'] if r['asset_id'] in ids and r['status']!='维修中']
            points=[point(m,sum(r['cost_cents'] for r in repairs if month_key(r['closed_at'])==m)/100,[r for r in repairs if month_key(r['closed_at'])==m]) for m in months]
            return dataset(CHARTS[key],'line','元',points,detail_type='repairs',note='仅统计筛选资产的已结案维修工单；按结束日期汇总实际登记费用，含报废结案。')
        if key=='purchase_budget':
            purchases=data['purchases']
            points=[point(m,sum(r['budget_cents'] for r in purchases if month_key(r['created_at'])==m)/100,[r for r in purchases if month_key(r['created_at'])==m]) for m in months]
            return dataset(CHARTS[key],'line','元',points,detail_type='purchases',note='授权范围内采购申请总预算，按申请日期分月；包含所有审批状态，不代表实际付款。资产筛选不影响此图。')
        if key=='purchase_status':
            states=['待审批','已批准','已拒绝','已入库'];purchases=[r for r in data['purchases'] if month_key(r['created_at']) in months]
            return dataset(CHARTS[key],'donut','项',[point(s,sum(r['status']==s for r in purchases),[r for r in purchases if r['status']==s]) for s in states],detail_type='purchases',note='授权范围内所选趋势周期的采购申请，按当前审批状态统计。资产筛选不影响此图。')
        if key=='forecast':
            points=[]
            for n in range(13):
                target=month_shift(asof,n)
                if n==0:target=asof
                values=[self.s.depreciation(a,target)[2] for a in assets]
                points.append(point(target.isoformat(),sum(values)/100,assets))
            return dataset(CHARTS[key],'line','元',points,note='按当前台账、购入日期、使用月数及残值率进行直线法净值测算。未来点取月初；不预测新增采购、处置或使用状态变化。',forecast=True)
        if key=='scatter':
            dated=[a for a in sorted(assets,key=lambda a:a['id']) if a['purchase_date']]
            points=[point(a['id']+' · '+a['name'],a['price_cents']/100,[a],x=full_months(date.fromisoformat(a['purchase_date']),asof),size=a['quantity']) for a in dated[:2000]]
            return dataset(CHARTS[key],'scatter','元/件',points,x_unit='使用月数',note='横轴：购入后完整月数；纵轴：每件购入单价；圆点大小表示数量。未填写日期的资产不显示。'+(' 显示按编号排序前 2000 条，建议进一步筛选。' if len(dated)>2000 else ''))
        if key=='heatmap':
            actions=['领用','归还','调拨','送修','维修完成','报废'];events=[e for e in data['events'] if e['asset_id'] in ids]
            grouped=defaultdict(list)
            for e in events:grouped[(e['action'],month_key(e['created_at']))].append(e)
            points=[]
            for row,action in enumerate(actions):
                for col,m in enumerate(months):
                    hits=grouped[(action,m)]
                    points.append(point(action+' / '+m,len(hits),hits,x=col,y=row))
            return dataset(CHARTS[key],'heatmap','次',points,x_labels=months,y_labels=actions,detail_type='events',note='筛选资产的流转事件次数，不是资产数量。颜色越深表示该月该操作次数越多。原表自由文本历史不猜测操作类型。')
        if key=='warranty':
            def bucket(a):
                if not a['warranty_date']:return '未填写保修日期'
                remaining=(date.fromisoformat(a['warranty_date'])-asof).days
                return '已到期' if remaining<0 else '30天内到期' if remaining<=30 else '31至90天到期' if remaining<=90 else '90天后到期'
            return dataset(CHARTS[key],'donut','件',group(bucket,lambda a:a['quantity']),note='保修风险按核算日期与保修到期日比较；未填写保修日期单独展示，报废资产由状态筛选决定。')

    def presets(self):
        self.s.require();row=self.s.db.one('SELECT value FROM meta WHERE key=?',('analytics_presets_'+self.s.user_id,))
        if not row:return {}
        try:return json.loads(row['value'])
        except (ValueError,TypeError):return {}
    def save_preset(self,name,config,delete=False):
        name=str(name).strip()
        if not 1<=len(name)<=40:raise BusinessError('筛选方案名称需 1～40 字')
        with self.s.db.transaction():
            self.s.require();presets=self.presets()
            if delete:presets.pop(name,None)
            else:presets[name]=config
            encoded=json.dumps(presets,ensure_ascii=False)
            if len(presets)>20 or len(encoded)>16000:raise BusinessError('最多保存 20 个筛选方案')
            self.s.db.execute('INSERT INTO meta (key,value) VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=excluded.value',('analytics_presets_'+self.s.user_id,encoded))
