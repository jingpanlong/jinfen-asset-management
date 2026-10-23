import logging
from datetime import date,datetime
from pathlib import Path
from PySide6.QtCore import Qt,QDate,QPoint
from PySide6.QtGui import QPainter,QPageLayout,QImage,QColor
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QComboBox,QCheckBox,QDateEdit,QPushButton,QFrame,QFileDialog,QMessageBox,QDialog,QTableWidget,QTableWidgetItem,QAbstractItemView,QInputDialog,QApplication
from PySide6.QtPrintSupport import QPrinter
from analytics import Analytics,CHARTS,STATUS
from chart_canvas import ChartCanvas,short
from service import write_table,BusinessError
from busy_cursor import push_busy,pop_busy
from table_style import center_table

def safe(parent,callback):
    push_busy()
    try:return callback()
    except Exception as e:logging.exception('Analytics operation failed');QMessageBox.warning(parent,'分析操作未完成',str(e))
    finally:pop_busy()

def button(title,fn):
    w=QPushButton(title);w.clicked.connect(lambda _:safe(w,fn));return w

def combo(options):
    w=QComboBox();w.setMinimumContentsLength(9);w.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    for value,title in options:w.addItem(title,value)
    return w

class AnalyticsPage(QWidget):
    def __init__(self,service,mainwindow):
        super().__init__(mainwindow);self.s=service;self.window=mainwindow;self.model=Analytics(service);self.current_chart=None;self.ready=False;self.updating=False
        root=QVBoxLayout(self);root.setContentsMargins(0,0,0,0);root.setSpacing(12)
        filters=QFrame();filters.setObjectName('card');f=QGridLayout(filters);f.setContentsMargins(16,12,16,12)
        self.category=combo([('','全部类别')]);self.department=combo([('','全部部门')]);self.status=combo([('','全部状态')]+[(s,s) for s in STATUS]);self.location=combo([('','全部位置')])
        for col,(title,w) in enumerate([('类别',self.category),('使用员工部门',self.department),('资产状态',self.status),('存放位置',self.location)]):f.addWidget(QLabel(title),0,col);f.addWidget(w,1,col)
        self.date_enabled=QCheckBox('按购入日期筛选');self.start=QDateEdit(QDate.currentDate().addYears(-3));self.end=QDateEdit(QDate.currentDate());self.undated=QCheckBox('含未填写购入日期');self.undated.setChecked(True)
        for w in [self.start,self.end]:w.setCalendarPopup(True);w.setDisplayFormat('yyyy-MM-dd');w.setEnabled(False)
        self.date_enabled.toggled.connect(self.start.setEnabled);self.date_enabled.toggled.connect(self.end.setEnabled)
        dates=QHBoxLayout();dates.addWidget(self.date_enabled);dates.addWidget(self.start);dates.addWidget(QLabel('至'));dates.addWidget(self.end);dates.addWidget(self.undated);dates.addStretch();f.addLayout(dates,2,0,1,4);root.addWidget(filters)
        options=QHBoxLayout();self.chart_selector=combo([(key,title) for key,title in CHARTS.items()]);options.addWidget(self.chart_selector,2);options.addWidget(QLabel('趋势周期'));self.months=combo([(6,'6个月'),(12,'12个月'),(24,'24个月')]);self.months.setCurrentIndex(1);options.addWidget(self.months);options.addWidget(QLabel('核算日期'));self.asof=QDateEdit(QDate.currentDate());self.asof.setCalendarPopup(True);self.asof.setDisplayFormat('yyyy-MM-dd');options.addWidget(self.asof);options.addWidget(button('应用筛选',self.redraw));options.addWidget(button('刷新数据',self.refresh));root.addLayout(options)
        metrics=QHBoxLayout();self.metric_labels={}
        for key,title in [('quantity','筛选资产总量'),('use_rate','在用比例'),('original','资产原值'),('net','核算净值')]:
            card=QFrame();card.setObjectName('card');v=QVBoxLayout(card);v.setContentsMargins(15,10,15,10);v.setSpacing(2);label=QLabel(title);label.setStyleSheet('color:#75859d;font-size:12px;');v.addWidget(label);value=QLabel('—');value.setStyleSheet('font-size:23px;font-weight:700;color:#365cf5;');v.addWidget(value);detail=QLabel();detail.setStyleSheet('font-size:11px;color:#8795aa;');v.addWidget(detail);metrics.addWidget(card,1);self.metric_labels[key]=(value,detail)
        root.addLayout(metrics)
        self.canvas=ChartCanvas();self.canvas.pointClicked.connect(self.drill_down);root.addWidget(self.canvas,1)
        tools=QHBoxLayout();self.preset_selector=combo([('','选择已保存方案')]);tools.addWidget(self.preset_selector,1);tools.addWidget(button('加载方案',self.load_preset));tools.addWidget(button('保存方案',self.save_preset));tools.addWidget(button('删除方案',self.remove_preset));tools.addStretch();tools.addWidget(button('双图对比',self.compare));tools.addWidget(button('重置',self.reset));tools.addWidget(button('PNG / PDF',self.export_chart));tools.addWidget(button('导出图表数据',self.export_data));root.addLayout(tools)
        self.info=QLabel('点击数据图形查看明细 · 悬停显示数值 · 散点图滚轮放大，双击复位');self.info.setWordWrap(True);self.info.setObjectName('subtitle');root.addWidget(self.info)
        self.chart_selector.currentIndexChanged.connect(lambda:safe(self,self.redraw) if self.ready and not self.updating else None)
    def config(self):
        return {'category':self.category.currentData() or '','department':self.department.currentData() or '','status':self.status.currentData() or '','location':self.location.currentData() or '','date_enabled':self.date_enabled.isChecked(),'include_undated':self.undated.isChecked(),'start':self.start.date().toString('yyyy-MM-dd'),'end':self.end.date().toString('yyyy-MM-dd'),'asof':self.asof.date().toString('yyyy-MM-dd'),'months':self.months.currentData(),'chart':self.chart_selector.currentData()}
    def refresh(self):
        self.model.load();self.updating=True
        try:
            choices=self.model.options()
            for w,options,default in [(self.category,[(x,x) for x in choices['categories']],'全部类别'),(self.location,[(x,x) for x in choices['locations']],'全部位置'),(self.department,choices['departments'],'全部部门')]:
                selected=w.currentData();w.clear();w.addItem(default,'')
                for value,title in options:w.addItem(title,value)
                w.setCurrentIndex(max(0,w.findData(selected)))
            self.refresh_presets();self.ready=True
        finally:self.updating=False
        self.redraw()
    def redraw(self):
        if not self.ready:return
        self.s.require()
        conf=self.config();asof=date.fromisoformat(conf['asof']);assets=self.model.filter(conf);metrics=self.model.metrics(assets,asof)
        for key,(label,detail) in self.metric_labels.items():
            if key=='quantity':label.setText(f"{metrics[key]:,} 件");detail.setText(f"{metrics['records']:,} 条台账，含报废")
            elif key=='use_rate':label.setText(f"{metrics[key]:.1f}%");detail.setText(f"在用 {metrics['used']:,} / 非报废 {metrics['active']:,} 件")
            else:label.setText(short(metrics[key])+' 元');detail.setText('单价 × 数量' if key=='original' else '核算日期 '+conf['asof'])
        chart=self.model.chart(conf['chart'],conf);self.current_chart=chart
        parts=[self.category.currentText(),self.department.currentText(),self.status.currentText(),self.location.currentText()]
        if conf['date_enabled']:parts.append(conf['start']+'～'+conf['end']+('（含未填日期）' if conf['include_undated'] else ''))
        timestamp=datetime.fromisoformat(self.model.snapshot['loaded_at']).astimezone().strftime('%Y-%m-%d %H:%M:%S')
        caption=' · '.join(parts)+' · 核算 '+conf['asof']+' · 数据刷新 '+timestamp
        self.canvas.set_data(chart,caption);self.info.setText('点击图形查看明细，悬停显示数值；散点图滚轮放大、双击复位。数据为刷新时快照。')
    def reset(self):
        self.updating=True
        for w in [self.category,self.department,self.status,self.location]:w.setCurrentIndex(0)
        self.date_enabled.setChecked(False);self.undated.setChecked(True);self.months.setCurrentIndex(1);self.asof.setDate(QDate.currentDate());self.chart_selector.setCurrentIndex(0);self.updating=False;self.redraw()
    def refresh_presets(self):
        previous=self.preset_selector.currentData();self.preset_selector.clear();self.preset_selector.addItem('选择已保存方案','')
        for name in self.model.presets():self.preset_selector.addItem(name,name)
        self.preset_selector.setCurrentIndex(max(0,self.preset_selector.findData(previous)))
    def save_preset(self):
        name,ok=QInputDialog.getText(self,'保存个人筛选方案','名称（同名将覆盖）：')
        if ok:self.model.save_preset(name,self.config());self.refresh_presets()
    def load_preset(self):
        name=self.preset_selector.currentData()
        if not name:raise BusinessError('请选择筛选方案')
        conf=self.model.presets().get(name)
        if conf is None:raise BusinessError('方案已不存在，请刷新')
        self.updating=True
        try:
            for key,w in [('category',self.category),('department',self.department),('status',self.status),('location',self.location),('months',self.months),('chart',self.chart_selector)]:w.setCurrentIndex(max(0,w.findData(conf.get(key,''))))
            for key,w in [('start',self.start),('end',self.end),('asof',self.asof)]:
                parsed=QDate.fromString(conf.get(key,''),'yyyy-MM-dd')
                if parsed.isValid():w.setDate(parsed)
            self.date_enabled.setChecked(bool(conf.get('date_enabled')));self.undated.setChecked(bool(conf.get('include_undated',True)))
        finally:self.updating=False
        self.redraw()
    def remove_preset(self):
        name=self.preset_selector.currentData()
        if not name:raise BusinessError('请选择要删除的方案')
        if QMessageBox.question(self,'删除方案','删除个人筛选方案 '+name+'？')==QMessageBox.Yes:self.model.save_preset(name,{},True);self.refresh_presets()
    def export_data(self):
        if not self.current_chart:return
        self.s.permit('data.export');self.s.permit('analytics.read')
        path,_=QFileDialog.getSaveFileName(self,'导出当前图表数据',self.current_chart['title']+'.xlsx','Excel (*.xlsx);;CSV (*.csv)')
        if not path:return
        rows=[[p['label'],p['value'],self.current_chart['unit'],len(p['rows']),p.get('x',''),p.get('size','')] for p in self.current_chart['points']]
        write_table(path,['数据项','数值','单位','明细记录数','横轴值','数量（散点）'],rows);QMessageBox.information(self,'数据已导出','已导出当前图表数据。点击图形可另行导出完整明细。')
    def export_chart(self):
        if not self.current_chart:return
        self.s.permit('data.export');self.s.permit('analytics.read')
        path,selected=QFileDialog.getSaveFileName(self,'导出图表',self.current_chart['title']+'.png','高清 PNG (*.png);;PDF (*.pdf)')
        if not path:return
        if not Path(path).suffix:path+='.pdf' if 'PDF' in selected else '.png'
        if Path(path).suffix.lower()=='.pdf':
            self.s.permit('data.print')
            printer=QPrinter(QPrinter.HighResolution);printer.setOutputFormat(QPrinter.PdfFormat);printer.setOutputFileName(path);printer.setPageOrientation(QPageLayout.Landscape)
            p=QPainter(printer);rect=printer.pageRect(QPrinter.DevicePixel);scale=min(rect.width()/self.canvas.width(),rect.height()/self.canvas.height());p.translate(rect.left(),rect.top());p.scale(scale,scale);self.canvas.render(p,QPoint());p.end()
        elif Path(path).suffix.lower()=='.png':self.canvas.save_png(path,2)
        else:raise BusinessError('图表导出请选择 PNG 或 PDF')
        QMessageBox.information(self,'图表已导出','保存成功：\n'+path)
    def drill_down(self,point):safe(self,lambda:self.open_detail(point))
    def open_detail(self,point,chart=None):
        self.s.permit('analytics.read');chart=chart or self.current_chart;rows=point['rows'];kind=chart.get('detail_type','assets')
        if kind=='assets':
            target=date.fromisoformat(point['label']) if chart.get('forecast') else date.fromisoformat(self.config()['asof'])
            columns=[('id','编号'),('name','设备名称'),('category','类别'),('quantity','数量'),('status','状态'),('location','位置'),('original','原值（元）'),('net','净值（元）')]
            display=[dict(a,original=self.s.depreciation(a,target)[0]/100,net=self.s.depreciation(a,target)[2]/100) for a in rows]
        elif kind=='repairs':
            columns=[('asset_id','资产编号'),('problem','故障'),('vendor','维修方'),('status','状态'),('cost','费用（元）'),('closed_at','结束时间'),('result','结果')];display=[dict(r,cost=r['cost_cents']/100) for r in rows]
        elif kind=='purchases':
            columns=[('id','申请编号'),('name','设备名称'),('quantity','数量'),('budget','申请预算（元）'),('status','审批状态'),('created_at','申请时间')];display=[dict(r,budget=r['budget_cents']/100) for r in rows]
        else:
            columns=[('asset_id','资产编号'),('action','操作'),('created_at','时间'),('detail','详细记录')];display=rows
        dialog=QDialog(self);dialog.setWindowTitle('图表明细 · '+point['label']);dialog.resize(1040,620);root=QVBoxLayout(dialog);label=QLabel(point['label']+f"  ·  {point['value']:,.2f} {chart['unit']}  ·  {len(rows):,} 条明细"+('  ·  净值测算日期 '+point['label'] if chart.get('forecast') else ''));label.setWordWrap(True);root.addWidget(label)
        table=QTableWidget(min(5000,len(display)),len(columns));table.setHorizontalHeaderLabels([title for _,title in columns]);center_table(table);table.setEditTriggers(QAbstractItemView.NoEditTriggers);table.setSelectionBehavior(QAbstractItemView.SelectRows);table.verticalHeader().setVisible(False)
        for i,row in enumerate(display[:5000]):
            for j,(key,_) in enumerate(columns):table.setItem(i,j,QTableWidgetItem(str(row.get(key,''))))
        table.resizeColumnsToContents();table.horizontalHeader().setStretchLastSection(True);root.addWidget(table,1)
        if kind=='assets':table.cellDoubleClicked.connect(lambda r,c:safe(self,lambda:self.window.asset_detail(display[r])))
        root.addWidget(QLabel('双击资产行查看实时资产资料与历史。' if kind=='assets' else '以上明细与图表使用同一份数据快照。'))
        if len(display)>5000:root.addWidget(QLabel('表格显示前 5000 条，导出包含全部明细。'))
        def export():
            self.s.permit('data.export');self.s.permit('analytics.read')
            path,_=QFileDialog.getSaveFileName(dialog,'导出全部明细','图表明细.xlsx','Excel (*.xlsx);;CSV (*.csv)')
            if path:write_table(path,[title for _,title in columns],[[r.get(k,'') for k,_ in columns] for r in display]);QMessageBox.information(dialog,'导出完成','全部明细已导出。')
        bottom=QHBoxLayout();bottom.addWidget(button('导出全部明细',export));bottom.addStretch();bottom.addWidget(button('关闭',dialog.accept));root.addLayout(bottom);dialog.exec()

    def compare(self):
        if not self.ready:return
        self.s.permit('analytics.read')
        dialog=QDialog(self);dialog.setWindowTitle('双图对比 · 同一筛选范围');dialog.resize(1260,650);root=QVBoxLayout(dialog);root.addWidget(QLabel('左右图表共享当前授权范围、筛选、核算日期和数据快照。'))
        panel=QWidget();grid=QGridLayout(panel);grid.setContentsMargins(0,0,0,0);root.addWidget(panel,1);canvases=[];selectors=[]
        for column,default in enumerate([self.chart_selector.currentData(),'category']):
            selector=combo([(key,title) for key,title in CHARTS.items()]);selector.setCurrentIndex(selector.findData(default));canvas=ChartCanvas();grid.addWidget(selector,0,column);grid.addWidget(canvas,1,column);canvases.append(canvas);selectors.append(selector)
            def update(c=canvas,s=selector):
                chart=self.model.chart(s.currentData(),self.config());c.set_data(chart,self.canvas.caption)
            selector.currentIndexChanged.connect(lambda _,fn=update:safe(dialog,fn));canvas.pointClicked.connect(lambda point,c=canvas:safe(dialog,lambda:self.open_detail(point,c.data)));update()
        def export():
            self.s.permit('data.export');self.s.permit('analytics.read')
            path,_=QFileDialog.getSaveFileName(dialog,'导出双图对比','资产双图对比.png','PNG (*.png)')
            if not path:return
            image=QImage(panel.width()*2,panel.height()*2,QImage.Format_ARGB32);image.fill(QColor('white'));p=QPainter(image);p.scale(2,2);panel.render(p,QPoint());p.end()
            if not image.save(path,'PNG'):raise BusinessError('图片保存失败')
            QMessageBox.information(dialog,'已保存','双图对比图片已保存。')
        bottom=QHBoxLayout();bottom.addWidget(button('导出高清对比图',export));bottom.addStretch();bottom.addWidget(button('关闭',dialog.accept));root.addLayout(bottom);dialog.exec()
