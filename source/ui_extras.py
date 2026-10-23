import html
from pathlib import Path
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,QPushButton,QComboBox,QCheckBox,QTableWidget,QTableWidgetItem,QFileDialog,QMessageBox,QAbstractItemView,QTextBrowser
from enhanced import KINDS
from service import write_table
from busy_cursor import push_busy,pop_busy
from table_style import center_table,center_header

CONTACTS='微信公众号：景同志\n小红书：景同学\nGitHub：jingpanlong/jinfen-asset-management'
GITHUB_URL='https://github.com/jingpanlong/jinfen-asset-management'

def btn(text,callback):
    button=QPushButton(text);button.clicked.connect(lambda _:callback());return button

def safe(parent,fn):
    push_busy()
    try:return fn()
    except Exception as e:QMessageBox.warning(parent,'操作未完成',str(e))
    finally:pop_busy()

def confirm(parent,text):
    box=QMessageBox(parent);box.setWindowTitle('确认导入');box.setText(text);yes=box.addButton('确认',QMessageBox.AcceptRole);box.addButton('取消',QMessageBox.RejectRole);box.exec();return box.clickedButton()==yes

class AboutDialog(QDialog):
    def __init__(self,version,parent=None):
        super().__init__(parent);self.setWindowTitle('关于锦纷资产管理系统');self.resize(690,660)
        root=QVBoxLayout(self);root.setContentsMargins(26,24,26,24)
        title=QLabel('◇  锦纷资产管理系统');title.setStyleSheet('font-size:27px;font-weight:700;color:#365cf5;');root.addWidget(title)
        root.addWidget(QLabel('版本 '+version+'  ·  Windows 桌面版'))
        box=QTextBrowser();box.setOpenExternalLinks(False);box.anchorClicked.connect(lambda url:QDesktopServices.openUrl(QUrl(url.toString())))
        box.setHtml('''<h3>让每一项资产都有清晰的去向</h3><p>面向公司、团队和机构的资产管理工具，整合台账、员工部门、领用归还、调拨、维修、采购审批、盘点、折旧统计和操作审计。</p><h3>数据与协作</h3><p>支持 SQLite 本地保存、局域网共享与 PostgreSQL 数据库共享。提供模板下载、文件选择上传导入、预览校验、错误行报告、导入履历、资产附件、批量操作、待办提醒和完整备份恢复。</p><h3>使用帮助</h3><p>登录用户名已预填 owner，输入发行方提供的内置管理员口令即可进入。先维护部门和员工，再通过资产台账领用。数据管理中可下载模板，选择 Excel / CSV 文件导入。更新已有记录前请检查预览结果。</p><p>Ctrl+F 搜索 · F5 刷新 · Ctrl+E 导出 · Ctrl+N 新增资产</p><h3>开源地址</h3><p>本软件已在 GitHub 开源，可下载最新版本、安装包与完整源码：</p><p><a href="https://github.com/jingpanlong/jinfen-asset-management"><b>https://github.com/jingpanlong/jinfen-asset-management</b></a></p><p>点击链接即可在浏览器打开；也可以通过下方按钮直达主页或复制地址。</p><h3>联系人 / 内容平台</h3><p><b>微信公众号：景同志</b></p><p><b>小红书：景同学</b></p><p>可在对应平台搜索以上名称联系和了解系统。</p><h3>数据说明</h3><p>请定期备份。附件随完整备份保存；单个附件限 10MB，当前数据库附件总量限 30MB。软件没有自动发送消息或上传至上述社交平台的功能。</p>''')
        root.addWidget(box,1);row=QHBoxLayout();row.addWidget(btn('复制联系方式',self.copy_contacts));row.addWidget(btn('复制开源地址',self.copy_github));row.addWidget(btn('打开 GitHub 主页',self.open_github));row.addStretch();row.addWidget(btn('关闭',self.accept));root.addLayout(row)
    def copy_contacts(self):QApplication.clipboard().setText(CONTACTS);QMessageBox.information(self,'联系方式','已复制：\n'+CONTACTS)
    def copy_github(self):QApplication.clipboard().setText(GITHUB_URL);QMessageBox.information(self,'开源地址','已复制：\n'+GITHUB_URL)
    def open_github(self):QDesktopServices.openUrl(QUrl(GITHUB_URL))

class TemplateDialog(QDialog):
    def __init__(self,service,parent=None,kind='assets'):
        super().__init__(parent);self.s=service;self.setWindowTitle('下载导入模板');self.resize(560,340)
        root=QVBoxLayout(self);root.setContentsMargins(25,22,25,22);form=QFormLayout();root.addLayout(form)
        self.kind=QComboBox()
        for key,label in KINDS.items():self.kind.addItem(label,key)
        self.kind.setCurrentIndex(max(0,self.kind.findData(kind)));form.addRow('模板类型',self.kind)
        self.format=QComboBox();self.format.addItems(['Excel（.xlsx）','CSV UTF-8（.csv）']);form.addRow('文件格式',self.format)
        self.example=QCheckBox('包含一行示例数据（请替换后导入）');self.example.setChecked(True);root.addWidget(self.example)
        note=QLabel('资产：单价单位为元，残值率单位为百分比。\n员工：启用填写 1 或 0，部门编号需已存在。\n建议先导入部门和供应商，再导入员工与资产。');note.setWordWrap(True);root.addWidget(note);root.addStretch();row=QHBoxLayout();row.addStretch();row.addWidget(btn('取消',self.reject));row.addWidget(btn('选择保存位置并下载',lambda:safe(self,self.download)));root.addLayout(row)
    def download(self):
        kind=self.kind.currentData();ext='.xlsx' if self.format.currentIndex()==0 else '.csv';path,_=QFileDialog.getSaveFileName(self,'保存模板',KINDS[kind]+'_导入模板'+ext,'Excel (*.xlsx)' if ext=='.xlsx' else 'CSV (*.csv)')
        if path:self.s.template(path,kind,self.example.isChecked());QMessageBox.information(self,'模板已下载','已保存到：\n'+path);self.accept()

class ImportWizard(QDialog):
    def __init__(self,service,path,parent=None,kind='assets'):
        super().__init__(parent);self.s=service;self.path=path;self.plan=None;self.records=[];self.import_result=None;self.setWindowTitle('文件上传导入 · 预览与校验');self.resize(1050,720)
        root=QVBoxLayout(self);root.setContentsMargins(24,20,24,20);info=QLabel('文件：'+Path(path).name+'\n本地模式把数据写入本地数据库；PostgreSQL 模式写入连接的共享数据库。');info.setWordWrap(True);root.addWidget(info)
        row=QHBoxLayout();self.kind=QComboBox()
        for key,label in KINDS.items():self.kind.addItem(label,key)
        self.kind.setCurrentIndex(max(0,self.kind.findData(kind)));row.addWidget(QLabel('导入类型'));row.addWidget(self.kind)
        self.mode=QComboBox();self.mode.addItems(['仅新增','按编号新增或更新']);row.addWidget(QLabel('编号处理'));row.addWidget(self.mode)
        self.sheet=QComboBox()
        if Path(path).suffix.lower()=='.xlsx':self.sheet.addItems(service.sheet_names(path));row.addWidget(QLabel('工作表'));row.addWidget(self.sheet)
        row.addStretch();row.addWidget(btn('重新预览',lambda:safe(self,self.reload)));root.addLayout(row)
        self.summary=QLabel();self.summary.setWordWrap(True);root.addWidget(self.summary)
        self.preview=QTableWidget();center_table(self.preview);self.preview.setEditTriggers(QAbstractItemView.NoEditTriggers);self.preview.verticalHeader().setVisible(False);root.addWidget(self.preview,1)
        self.errors=QTableWidget();self.errors.setColumnCount(3);self.errors.setHorizontalHeaderLabels(['文件行号','编号','错误原因']);center_table(self.errors);self.errors.setEditTriggers(QAbstractItemView.NoEditTriggers);self.errors.verticalHeader().setVisible(False);self.errors.setMaximumHeight(150);root.addWidget(self.errors)
        warning=QLabel('更新模式仅更新模板中的基础资料；资产使用状态和领用关系保留。模板中空白基础字段也会覆盖原值，请检查预览。新资产初始闲置；原使用人信息保留为历史。');warning.setWordWrap(True);warning.setObjectName('subtitle');root.addWidget(warning)
        bottom=QHBoxLayout();self.error_button=btn('导出错误行报告',lambda:safe(self,self.export_errors));bottom.addWidget(self.error_button);bottom.addStretch();bottom.addWidget(btn('取消',self.reject));self.apply_button=btn('校验通过，确认导入',lambda:safe(self,self.apply));self.apply_button.setProperty('primary',True);bottom.addWidget(self.apply_button);root.addLayout(bottom)
        self.kind.currentIndexChanged.connect(lambda:safe(self,self.reload));self.mode.currentIndexChanged.connect(lambda:safe(self,self.reload));self.sheet.currentIndexChanged.connect(lambda:safe(self,self.reload));self.reload()
    def reload(self):
        self.apply_button.setEnabled(False);self.plan=None
        self.records=self.s.read_import(self.path,self.sheet.currentText() or None)
        if Path(self.path).suffix.lower()=='.json' and self.kind.currentData()!='assets':raise ValueError('旧网页 JSON 仅可导入资产台账')
        self.plan=self.s.plan_import(self.records,self.kind.currentData(),self.mode.currentText())
        p=self.plan;self.summary.setText(f"共 {p['total']} 行 · 可新增 {p['added']} 行 · 可更新 {p['updated']} 行 · 错误 {len(p['errors'])} 行\n以下预览前 50 行原始数据，所有行均已校验。")
        headers=[k for k in self.records[0] if not k.startswith('__')][:24];self.preview.clear();self.preview.setColumnCount(len(headers)+1);self.preview.setHorizontalHeaderLabels(['文件行号']+headers);center_header(self.preview);self.preview.setRowCount(min(50,len(self.records)))
        for i,r in enumerate(self.records[:50]):
            for j,value in enumerate([r['__line']]+[r.get(k,'') for k in headers]):self.preview.setItem(i,j,QTableWidgetItem(str(value if value is not None else '')))
        self.preview.resizeColumnsToContents();self.preview.horizontalHeader().setStretchLastSection(True);self.errors.setRowCount(min(200,len(p['errors'])))
        for i,error in enumerate(p['errors'][:200]):
            for j,k in enumerate(['line','id','error']):self.errors.setItem(i,j,QTableWidgetItem(str(error[k])))
        self.errors.horizontalHeader().setStretchLastSection(True);self.errors.setVisible(bool(p['errors']));self.error_button.setEnabled(bool(p['errors']));self.apply_button.setEnabled(not p['errors'])
    def export_errors(self):
        path,_=QFileDialog.getSaveFileName(self,'保存错误行报告','导入错误行.csv','CSV (*.csv)')
        if path:write_table(path,['文件行号','编号','错误原因'],[[e['line'],e['id'],e['error']] for e in self.plan['errors']]);QMessageBox.information(self,'错误报告','已保存全部错误行报告。')
    def apply(self):
        if not self.plan or self.plan['errors']:return
        if not confirm(self,f"确认导入 {self.plan['total']} 行？\n新增 {self.plan['added']}，更新 {self.plan['updated']}。\n所有行将统一提交，更新会覆盖基础资料。"):return
        self.import_result=self.s.apply_import(self.records,self.kind.currentData(),self.mode.currentText(),self.path,self.plan);self.accept()

class AttachmentDialog(QDialog):
    def __init__(self,service,asset,parent=None):
        super().__init__(parent);self.s=service;self.asset=asset;self.items=[];self.setWindowTitle('资产附件 · '+asset['id']);self.resize(850,480)
        root=QVBoxLayout(self);note=QLabel('上传照片、采购凭证、保修单、合同等文件。附件保存在当前数据库，包含在完整备份中。\n单个最多 10MB；数据库附件总量最多 30MB。');note.setWordWrap(True);root.addWidget(note)
        self.table=QTableWidget();self.table.setColumnCount(4);self.table.setHorizontalHeaderLabels(['文件名','大小','上传时间','SHA256']);center_table(self.table);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.verticalHeader().setVisible(False);root.addWidget(self.table,1)
        row=QHBoxLayout();upload=btn('上传附件',lambda:safe(self,self.upload));upload.setEnabled(service.can('attachments.write'));row.addWidget(upload);row.addWidget(btn('下载选中文件',lambda:safe(self,self.download)));rename=btn('重命名',lambda:safe(self,self.rename));rename.setEnabled(service.can('attachments.write'));row.addWidget(rename);remove=btn('移除选中附件',lambda:safe(self,self.remove));remove.setEnabled(service.can('attachments.write'));row.addWidget(remove);row.addStretch();row.addWidget(btn('关闭',self.accept));root.addLayout(row);self.reload()
    def reload(self):
        self.items=self.s.attachments(self.asset['id']);self.table.setRowCount(len(self.items))
        for i,row in enumerate(self.items):
            for j,value in enumerate([row['filename'],f"{row['size_bytes']/1024:,.1f} KB",row['created_at'],row['sha256']]):self.table.setItem(i,j,QTableWidgetItem(value))
        self.table.resizeColumnsToContents();self.table.horizontalHeader().setStretchLastSection(True)
    def selected(self):
        row=self.table.currentRow()
        if row<0:raise ValueError('请选择附件')
        return self.items[row]
    def upload(self):
        paths,_=QFileDialog.getOpenFileNames(self,'选择上传附件','','附件 (*.pdf *.docx *.xlsx *.csv *.txt *.png *.jpg *.jpeg *.bmp *.webp *.zip)')
        if paths:self.s.upload_attachments(self.asset['id'],paths);self.reload()
    def download(self):
        selected=self.selected();path,_=QFileDialog.getSaveFileName(self,'下载附件',selected['filename'],'所有文件 (*)')
        if path:self.s.download_attachment(selected['id'],path);QMessageBox.information(self,'附件已下载','文件已保存：\n'+path)
    def remove(self):
        selected=self.selected()
        if confirm(self,'确认移除附件 '+selected['filename']+'？建议先下载留存。'):self.s.remove_attachment(selected['id']);self.reload()
    def rename(self):
        selected=self.selected();suffix=Path(selected['filename']).suffix
        dialog=QDialog(self);dialog.setWindowTitle('重命名附件');dialog.resize(440,160)
        layout=QVBoxLayout(dialog);form=QFormLayout();layout.addLayout(form)
        field=QLineEdit(Path(selected['filename']).stem);field.setMaxLength(200);form.addRow('新文件名（不含扩展名）',field)
        hint=QLabel('扩展名 '+suffix+' 保持不变，文件内容不受影响。');hint.setObjectName('subtitle');layout.addWidget(hint)
        buttons=QHBoxLayout();buttons.addStretch();buttons.addWidget(btn('取消',dialog.reject));buttons.addWidget(btn('确定',lambda:safe(dialog,lambda:self.apply_rename(dialog,field))));layout.addLayout(buttons);dialog.exec()
    def apply_rename(self,dialog,field):
        selected=self.selected();self.s.rename_attachment(selected['id'],field.text());self.reload();dialog.accept()

