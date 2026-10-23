"""Jinfen Assets native Windows desktop application."""
from __future__ import annotations
import html
import json
import logging
import os
import socket
import sys
import tempfile
import threading
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import Qt, QTimer,QEvent
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QColor, QPixmap, QTextDocument,QShortcut,QKeySequence
from PySide6.QtWidgets import (QApplication,QMainWindow,QDialog,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QGridLayout,QLabel,QPushButton,QLineEdit,QComboBox,QSpinBox,QTextEdit,QCheckBox,QTabWidget,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,QMessageBox,QStackedWidget,QFrame,QProgressBar,QScrollArea,QAbstractItemView,QMenu,QStyleOptionViewItem)
from PySide6.QtPrintSupport import QPrinter, QPrintDialog
from database import Database,StorageBusy,data_dir,default_config,load_config,load_share,save_config,save_share,default_share,open_database
from service import BusinessError,ASSET_FIELDS,LABELS,STATUS,ROLES,money,write_table,day
from enhanced import Service
from ui_extras import AboutDialog,TemplateDialog,ImportWizard,AttachmentDialog
from analytics_ui import AnalyticsPage
from desktop_runtime import DesktopController,SingleInstance
from access_ui import RolesDialog,UserAccessDialog
from access import BUILTIN_USERNAME,BUILTIN_LABEL
from asset_photo_ui import AssetTable,AssetDetailDialog,PhotoPicker
from asset_photos import upload_images,table_thumbnails
from busy_cursor import push_busy,pop_busy,reset_busy,sync_busy
from table_style import center_table,center_header,cell_box,CenterDelegate
from password_field import PasswordEdit
from share_client import LanShareError,RemoteDatabase,test_connection
from share_server import ShareServer,describe_share
from share_protocol import ShareError,public_address

VERSION='2.5.0'
STYLE='''
QWidget {font-family:"Microsoft YaHei UI";font-size:13px;color:#283750;}
QMainWindow,QDialog {background:#f5f7fb;} QLabel {background:transparent;}
QLineEdit,QComboBox,QSpinBox,QTextEdit {background:white;border:1px solid #dce3f0;border-radius:6px;padding:8px;selection-background-color:#4266ee;}
QLineEdit:focus,QComboBox:focus,QTextEdit:focus {border:1px solid #5977ec;}
QPushButton {background:white;border:1px solid #dce3f0;border-radius:6px;padding:8px 15px;}
QPushButton:hover {background:#edf1ff;border-color:#a7b9fb;}
QPushButton:disabled {color:#a3acbc;background:#f1f3f7;}
QPushButton[primary="true"] {background:#365cf5;border-color:#365cf5;color:white;font-weight:600;}
QPushButton[danger="true"] {color:#d85a68;}
QFrame#sidebar {background:white;border-right:1px solid #e8edf5;}
QPushButton#nav {text-align:left;border:0;padding:12px 18px;background:transparent;border-radius:7px;}
QPushButton#nav:checked {background:#edf1ff;color:#365cf5;font-weight:600;}
QLabel#title {font-size:25px;font-weight:700;color:#263650;}
QLabel#subtitle {color:#8a96ac;font-size:12px;}
QFrame#card {background:white;border:1px solid #e7ecf4;border-radius:10px;}
QTableWidget {background:white;border:1px solid #e4eaf4;border-radius:7px;gridline-color:#edf1f7;alternate-background-color:#fafbfe;selection-background-color:#e6edff;selection-color:#25427b;}
QHeaderView::section {background:#f0f4fb;border:0;border-bottom:1px solid #dfe7f3;padding:12px;color:#748299;font-weight:600;}
QTableWidget::item {padding:8px;}
QTabWidget::pane {border:1px solid #e0e6f2;background:white;border-radius:8px;}
QTabBar::tab {padding:12px 24px;background:#edf1fa;border:0;}
QTabBar::tab:selected {background:white;color:#365cf5;}
QProgressBar {border:0;background:#edf1fb;border-radius:4px;height:9px;}
QProgressBar::chunk {background:#5979f1;border-radius:4px;}
QScrollArea {border:0;background:transparent;}
'''

def fmt_money(cents):
    return f'{Decimal(cents)/100:,.2f}'

def text_time(value):
    if not value:
        return '—'
    try:
        from datetime import datetime
        return datetime.fromisoformat(value).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        return value

def clock_time(seconds):
    try:
        from datetime import datetime
        return datetime.fromtimestamp(float(seconds)).strftime('%H:%M:%S')
    except (TypeError,ValueError,OSError):
        return '—'

def button(text,slot=None,primary=False,danger=False):
    b=QPushButton(text);b.setProperty('primary',primary);b.setProperty('danger',danger)
    if slot:
        b.clicked.connect(lambda _=False:slot())
    return b

def message(parent,text):
    QMessageBox.information(parent,'锦纷资产',text)

def question(parent,text):
    box=QMessageBox(parent);box.setWindowTitle('请确认');box.setText(text);box.setIcon(QMessageBox.Question)
    yes=box.addButton('确认',QMessageBox.AcceptRole);box.addButton('取消',QMessageBox.RejectRole);box.exec()
    return box.clickedButton()==yes

def handle(parent,callback):
    push_busy()
    try:
        return callback()
    except StorageBusy as e:
        QMessageBox.warning(parent,'请稍候',str(e))
    except BusinessError as e:
        QMessageBox.warning(parent,'操作未完成',str(e))
    except Exception as e:
        logging.exception('Application operation failed')
        QMessageBox.warning(parent,'操作未完成',str(e)+'\n\n详细诊断已写入本地 application.log。')
    finally:
        pop_busy()
    return None

def mode_text(db):
    """The one-line description of where this window's data lives."""
    if getattr(db,'shared',False):
        return '局域网共享 · 主机 '+str(db.host)+':'+str(db.port)
    if db.pg:
        return 'PostgreSQL · '+str(db.config.get('host',''))+' / '+str(db.config.get('database',''))
    return '本地 SQLite · '+Path(db.config['path']).name

def db_identity(db):
    """A stable fingerprint of a store, so settings never leak between databases."""
    config=getattr(db,'config',{}) or {}
    return str(config.get('path') or '|'.join(str(config.get(k,'')) for k in ['host','port','database','user']))

class FormDialog(QDialog):
    """A reusable, genuinely editable form. Combo values use IDs, never display text."""
    def __init__(self,title,spec,values=None,parent=None,description='',extra=None):
        super().__init__(parent);self.setWindowTitle(title);self.resize(590,420);self.widgets={};self.spec=spec;self.extra=extra
        root=QVBoxLayout(self);root.setContentsMargins(24,20,24,20)
        if description:
            label=QLabel(description);label.setWordWrap(True);label.setObjectName('subtitle');root.addWidget(label)
        scroll=QScrollArea();scroll.setWidgetResizable(True);box=QWidget();form=QFormLayout(box);form.setVerticalSpacing(12);scroll.setWidget(box);root.addWidget(scroll)
        values=values or {}
        for item in spec:
            key,label,kind,*opts=item;value=values.get(key,'')
            if kind=='choice':
                widget=QComboBox()
                for option in opts[0]:
                    val,txt=option if isinstance(option,tuple) else (option,option)
                    widget.addItem(str(txt),val)
                index=widget.findData(value)
                if index>=0:widget.setCurrentIndex(index)
            elif kind=='int':
                widget=QSpinBox();widget.setRange(*opts[0] if opts else (0,1000000));widget.setValue(int(value or 0))
            elif kind=='check':
                widget=QCheckBox('启用');widget.setChecked(bool(value))
            elif kind=='text':
                widget=QTextEdit();widget.setPlainText(str(value or ''));widget.setMinimumHeight(100);widget.setMaximumHeight(160)
            else:
                widget=PasswordEdit(str(value or '')) if kind=='password' else QLineEdit(str(value or ''))
                if kind!='password':widget.setMaxLength(5000)
                if kind=='readonly':widget.setReadOnly(True)
                if kind=='date':widget.setPlaceholderText('YYYY-MM-DD（可留空）')
            self.widgets[key]=widget;form.addRow(label,widget)
        if extra is not None:root.addWidget(extra)
        buttons=QHBoxLayout();buttons.addStretch();buttons.addWidget(button('取消',self.reject));buttons.addWidget(button('确定',self.accept,True));root.addLayout(buttons)

    def values(self):
        data={}
        for k,w in self.widgets.items():
            if isinstance(w,QComboBox):data[k]=w.currentData()
            elif isinstance(w,QSpinBox):data[k]=w.value()
            elif isinstance(w,QCheckBox):data[k]=int(w.isChecked())
            elif isinstance(w,QTextEdit):data[k]=w.toPlainText()
            else:data[k]=w.text()
        return data

class ConnectionDialog(QDialog):
    def __init__(self,config,parent=None):
        super().__init__(parent);self.setWindowTitle('数据存储与数据库连接');self.resize(600,600);self.result_config=None
        root=QVBoxLayout(self);root.setContentsMargins(26,24,26,24)
        self.info=QLabel();self.info.setWordWrap(True);root.addWidget(self.info)
        form=QFormLayout();root.addLayout(form);self.fields={};self.labels={}
        self.engine=QComboBox();self.engine.addItem('本地 SQLite 文件','sqlite');self.engine.addItem('局域网共享主机（免装数据库）','shared');self.engine.addItem('PostgreSQL 数据库','postgresql')
        self.engine.setCurrentIndex({'sqlite':0,'shared':1,'postgresql':2}.get(config.get('engine'),0));form.addRow('存储方式',self.engine)
        for k,label,default in [('path','本地文件',str(data_dir()/'assets.db')),('host','服务器地址','localhost'),('port','端口','5432'),('database','数据库名称','qiyun_assets'),('user','数据库用户',''),('password','数据库密码','')]:
            w=PasswordEdit(str(config.get(k,default))) if k=='password' else QLineEdit(str(config.get(k,default)));self.fields[k]=w
            caption=QLabel(label);self.labels[k]=caption
            if k=='path':
                line=QHBoxLayout();line.addWidget(w);line.addWidget(button('浏览',self.browse));form.addRow(caption,line)
            else:form.addRow(caption,w)
        ssl=QComboBox();ssl.addItems(['require','verify-full','verify-ca','prefer','disable']);ssl.setCurrentText(config.get('sslmode','require'));self.fields['sslmode']=ssl
        ssl_label=QLabel('SSL 模式');self.labels['sslmode']=ssl_label;form.addRow(ssl_label,ssl)
        self.note=QLabel();self.note.setWordWrap(True);self.note.setObjectName('subtitle');root.addWidget(self.note)
        root.addStretch();row=QHBoxLayout();row.addWidget(button('测试连接',lambda:handle(self,self.test)));row.addStretch();row.addWidget(button('取消',self.reject));row.addWidget(button('保存并连接',lambda:handle(self,self.save),True));root.addLayout(row)
        self.engine.currentIndexChanged.connect(self.toggle);self.toggle()
    def toggle(self):
        mode=self.engine.currentData();local=mode=='sqlite';shared=mode=='shared';postgres=mode=='postgresql'
        for k,w in self.fields.items():
            if k=='path':show=local
            elif k in ['host','port','password']:show=not local
            else:show=postgres
            w.setVisible(show);w.setEnabled(show);self.labels[k].setVisible(show)
        port=self.fields['port'].text().strip()
        if shared and port in ['','5432']:self.fields['port'].setText('8765')
        elif postgres and port in ['','8765']:self.fields['port'].setText('5432')
        self.fields['host'].setText(self.fields['host'].text() or ('localhost' if postgres else ''))
        self.labels['host'].setText('主机电脑地址' if shared else '服务器地址')
        self.labels['port'].setText('共享端口' if shared else '端口')
        self.labels['password'].setText('访问口令（≥8 位）' if shared else '数据库密码')
        self.info.setText('本地模式使用 SQLite，无需数据库服务器。\n局域网共享由管理员电脑当主机，其他电脑填主机地址与访问口令即可共用同一份数据。\n数据库模式支持 PostgreSQL，多台电脑填写相同连接信息即可共享。\n保存连接前会创建业务表，请使用专用空数据库。' if not shared else '局域网共享：管理员电脑开启共享后，本机填它的局域网地址（同一网络）\n或公网 IP（主机已开通公网访问并做好端口映射时）、共享端口和访问口令。\n数据只保存在主机电脑上，本机需要网络可达且主机软件保持运行。\n管理员可在主机「模板与数据管理」中查看本机地址与在线客户端。')
        self.note.setText('默认 require 使用加密连接；验证服务器身份请用 verify-full，\n并由数据库管理员安装根证书。密码通过 Windows DPAPI 加密保存。\n更换数据库只切换连接，不自动迁移；迁移请使用完整备份与恢复。' if not shared else '访问口令用挑战应答校验，不会明文经过网络。\n建议为共享单独设置 8 位以上口令，并请勿把口令写在本机以外的位置。\n主机需在防火墙上放行所选端口；首次开启时 Windows 会弹出提示，请选择「允许」。\n跨公网使用时，主机需开启「允许公网访问」并设置 12 位以上强口令。')
    def browse(self):
        path,_=QFileDialog.getSaveFileName(self,'选择 SQLite 数据文件',self.fields['path'].text(),'SQLite (*.db)')
        if path:self.fields['path'].setText(path)
    def config(self):
        mode=self.engine.currentData()
        if mode=='sqlite':
            path=self.fields['path'].text().strip()
            if not path:raise BusinessError('请选择本地数据库文件')
            return {'engine':'sqlite','path':str(Path(path).resolve())}
        if mode=='shared':
            host=self.fields['host'].text().strip();port=self.fields['port'].text().strip();password=self.fields['password'].text()
            if not host:raise BusinessError('请填写主机电脑的局域网地址、公网 IP 或计算机名')
            if not port.isdigit() or not 1<=int(port)<=65535:raise BusinessError('共享端口无效，请填写 1～65535 之间的端口')
            if len(password)<8:raise BusinessError('访问口令至少 8 位，请与主机管理员设置的口令保持一致')
            return {'engine':'shared','host':host,'port':int(port),'password':password}
        values={k:(w.currentText() if isinstance(w,QComboBox) else w.text()) for k,w in self.fields.items() if k not in ['path','port']}
        port=self.fields['port'].text().strip()
        if not all(values[k].strip() for k in ['host','database','user']) or not port:raise BusinessError('服务器、端口、库名和用户必填')
        if not port.isdigit() or not 1<=int(port)<=65535:raise BusinessError('端口无效')
        values['port']=port;values['engine']='postgresql';return values
    def test(self):
        config=self.config()
        if config['engine']=='sqlite':
            parent=Path(config['path']).parent
            if not parent.exists():raise BusinessError('目标目录不存在，请先创建目录')
            with tempfile.TemporaryFile(dir=parent):pass
        elif config['engine']=='shared':
            name=test_connection(config)
            message(self,'连接测试成功，已与主机「'+name+'」建立连接并完成口令校验。')
            return
        else:
            db=Database(config,initialize=False)
            try:db.one('SELECT 1 AS connected')
            finally:db.close()
        message(self,'连接测试成功。测试未创建业务表。')
    def save(self):
        config=self.config()
        db=open_database(config)
        try:save_config(config)
        finally:db.close()
        self.result_config=config;self.accept()

class LoginWindow(QDialog):
    def __init__(self,db):
        super().__init__();self.db=db;self.service=Service(db);self.setWindowTitle('锦纷资产管理系统 · 登录 / 注册');self.resize(960,600)
        root=QHBoxLayout(self);root.setContentsMargins(0,0,0,0)
        hero=QFrame();hero.setStyleSheet('QFrame{background:#365cf5;} QLabel{color:white;background:transparent;}');h=QVBoxLayout(hero);h.setContentsMargins(42,50,42,50)
        h.addWidget(QLabel('◇  锦纷资产管理系统'));h.addStretch();title=QLabel('每一项资产\n都有清晰的去向');title.setStyleSheet('font-size:34px;font-weight:700;');h.addWidget(title);h.addWidget(QLabel('资产台账 · 流转追踪 · 盘点 · 采购\n原生桌面界面，本地与数据库双模式'));h.addStretch();h.addWidget(QLabel('WINDOWS DESKTOP  /  '+VERSION));root.addWidget(hero,4)
        right=QWidget();r=QVBoxLayout(right);r.setContentsMargins(36,30,36,28);label=QLabel('欢迎使用锦纷资产管理系统');label.setObjectName('title');r.addWidget(label)
        self.mode_label=QLabel();self.mode_label.setObjectName('subtitle');r.addWidget(self.mode_label)
        tabs=QTabWidget();r.addWidget(tabs)
        self.inputs={}
        for name,register in [('登录',False),('注册',True)]:
            panel=QWidget();v=QVBoxLayout(panel);v.setContentsMargins(22,25,22,25)
            form=QFormLayout();v.addLayout(form);fields={}
            for key,text in ([('username','用户名'),('name','姓名'),('password','密码'),('confirm','确认密码')] if register else [('username','用户名'),('password','密码')]):
                w=PasswordEdit() if key in ['password','confirm'] else QLineEdit();w.setMaxLength(200)
                form.addRow(text,w);fields[key]=w
            tip=QLabel('用户名：3～40 位英文字母或数字\n密码：至少 10 位，包含英文字母和数字\n注册后即为只读账号：可以查看数据；\n需要更多权限时，请由内置管理员账号授权。\n任何人都不会在注册时成为管理员。' if register else '本软件内置一个出厂固定账号：用户名 '+BUILTIN_USERNAME+'，\n口令由发行方保管，任何一台电脑、任何一份数据库都相同，\n且不能在本软件中修改。\n如需为同事新增账号，请切换到「注册」页。');tip.setWordWrap(True);tip.setObjectName('subtitle');v.addWidget(tip);v.addStretch()
            fn=self.register if register else self.login
            submit=button('创建账号' if register else '登录系统',lambda f=fn:handle(self,f),True);v.addWidget(submit)
            if not register:fields['password'].returnPressed.connect(lambda:handle(self,self.login))
            self.inputs['register' if register else 'login']=fields;tabs.addTab(panel,name)
        self.tabs=tabs
        r.addWidget(button('配置本地存储 / 数据库连接',lambda:handle(self,self.connection)));r.addWidget(button('关于系统 / 联系方式',lambda:AboutDialog(VERSION,self).exec()));r.addWidget(button('运行设置 / 自启动与托盘',self.desktop_settings));root.addWidget(right,5);self.update_mode()
        # The factory administrator is part of every database: make sure it exists
        # before the window shows, so it is listed from the first run onwards.
        try:
            with db.transaction():self.service.ensure_builtin_owner()
        except Exception:pass
        tabs.setCurrentIndex(0)
        if not db.one('SELECT id FROM users WHERE username<>? LIMIT 1',(BUILTIN_USERNAME,)):
            self.inputs['login']['username'].setText(BUILTIN_USERNAME)
    def closeEvent(self,event):
        desktop=getattr(self,'desktop',None)
        if desktop:desktop.request_close(self,event)
        else:super().closeEvent(event)
    def reject(self):
        desktop=getattr(self,'desktop',None)
        if desktop and not desktop.exiting:
            if desktop.preferences['close_to_tray'] and desktop.available():desktop.hide_to_tray(self)
            else:desktop.quit()
        else:super().reject()
    def desktop_settings(self):
        if getattr(self,'desktop',None):self.desktop.settings()
        else:message(self,'运行设置请通过正式桌面启动入口打开。')
    def update_mode(self):
        self.mode_label.setText(mode_text(self.db))
    def login(self):
        f=self.inputs['login'];self.service.login(f['username'].text(),f['password'].text());f['password'].clear();self.accept()
    def register(self):
        f=self.inputs['register']
        if f['password'].text()!=f['confirm'].text():raise BusinessError('两次密码输入不一致')
        self.service.register(f['username'].text(),f['name'].text(),f['password'].text())
        self.inputs['login']['username'].setText(f['username'].text());f['password'].clear();f['confirm'].clear();self.tabs.setCurrentIndex(0)
        message(self,'注册成功。当前为只读账号：可以查看数据；\n如需更多权限，请由内置管理员账号（用户名 '+BUILTIN_USERNAME+'）授权。')
    def connection(self):
        dialog=ConnectionDialog(self.db.config,self)
        if dialog.exec()==QDialog.Accepted:
            newdb=open_database(dialog.result_config);self.db.close();self.db=newdb;self.service=Service(newdb);self.update_mode()
            for group in self.inputs.values():
                for w in group.values():w.clear()
            try:
                with newdb.transaction():self.service.ensure_builtin_owner()
            except Exception:pass
            self.tabs.setCurrentIndex(0)

MODULES={
 'tasks':('待办与提醒','记录资产相关工作，跟踪截止日期和完成情况',[('title','待办标题'),('asset_id','关联资产'),('priority','优先级'),('due_date','截止日期'),('status','状态'),('creator','创建人'),('notes','说明'),('created','创建时间')]),
 'import_batches':('导入履历','记录模板文件上传导入、新增与更新结果',[('filename','文件名'),('kind','资料类型'),('mode','导入模式'),('added','新增条数'),('updated','更新条数'),('actor','经办人'),('created','导入时间')]),
 'assets':('资产台账','拖入图片到对应资产行上传 · 双击任意一列查看完整详情',[('id','编号'),('photo','资产图片'),('name','设备名称'),('category','类别'),('brand','品牌'),('quantity','数量'),('status','状态'),('employee','使用人'),('location','位置'),('price','单价'),('due_date','应还日期')]),
 'employees':('员工资料','部门归属和资产使用员工档案',[('id','员工编号'),('name','姓名'),('department','部门'),('phone','电话'),('email','邮箱'),('active_text','状态'),('notes','备注')]),
 'departments':('部门管理','维护公司部门和负责人',[('id','编号'),('name','部门名称'),('manager','负责人'),('notes','备注')]),
 'suppliers':('供应商','采购与维修供应商档案',[('id','编号'),('name','供应商名称'),('contact','联系人'),('phone','电话'),('notes','备注')]),
 'repairs':('维修工单','送修、完成、费用和处理结果',[('asset_id','资产编号'),('asset_name','设备名称'),('problem','问题描述'),('vendor','维修方'),('status','状态'),('cost','费用'),('opened','送修时间'),('closed','结束时间'),('result','结果')]),
 'purchases':('采购申请','提交、独立审批、实际入库形成闭环',[('short_id','申请编号'),('name','设备名称'),('category','类别'),('quantity','数量'),('budget','总预算'),('requester','申请人'),('status','状态'),('approver','审批人'),('asset_id','入库编号'),('comment','审批意见')]),
 'counts':('资产盘点','按位置建立快照，登记实盘数量和差异',[('short_id','盘点编号'),('name','盘点名称'),('location','范围位置'),('status','状态'),('progress','登记进度'),('differences','差异项目'),('created','创建时间')]),
 'events':('流转记录','完整保留历史，原表使用人列可持续追加',[('asset_id','资产编号'),('action','操作'),('actor','经办人'),('created','时间'),('detail','详细记录')]),
 'users':('账号与权限','管理员只能由内置管理员（出厂账号 '+BUILTIN_USERNAME+'）授予 · 多角色授权 · 拒绝优先 · 数据范围 · 授权有效期',[('username','用户名'),('display_name','姓名'),('role','基础角色'),('owner_text','内置管理员'),('effective_roles','有效角色'),('scope_text','数据范围'),('permission_count','允许操作数'),('access_expiry','授权到期'),('active_text','启用状态'),('failures','失败次数'),('locked','锁定至'),('created','注册时间')]),
 'audit':('操作审计','记录账号、资产、审批及数据管理操作',[('actor','经办账号'),('action','操作'),('created','时间'),('detail','内容')]),
 'reports':('折旧与报表','按购入日期计算完整月数，直线法折旧',[('id','资产编号'),('name','设备名称'),('category','类别'),('status','状态'),('quantity','数量'),('original','原值'),('depreciated','累计折旧'),('net','净值'),('purchase_date','购入日期'),('life_months','使用月数'),('residual','残值率')]),
}

class MainWindow(QMainWindow):
    def __init__(self,service):
        super().__init__();self.s=service;self.db=service.db;self.user=service.require();self.admin=self.user['role']=='管理员';self.owner=self.s.is_owner(self.user);self.writer=any(not p.endswith('.read') and p not in ['data.export','data.print'] for p in self.s.profile()['permissions']);self.page='dashboard';self.visible=[];self.logout_requested=False
        self.share=None;self.share_lines=[];self.share_status=None;self.share_clients=None;self.share_gone=False
        self.setWindowTitle('锦纷资产管理系统 '+VERSION);self.resize(1420,890);self.setMinimumSize(1080,700)
        outer=QWidget();self.setCentralWidget(outer);layout=QHBoxLayout(outer);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        sidebar=QFrame();sidebar.setObjectName('sidebar');sidebar.setFixedWidth(205);side=QVBoxLayout(sidebar);side.setContentsMargins(16,27,16,24)
        brand_row=QHBoxLayout();brand_row.setSpacing(9);brand_icon=QLabel();brand_icon.setObjectName('brandIcon');brand_icon.setPixmap(QIcon(str(Path(__file__).with_name('app.ico'))).pixmap(30,30));brand_icon.setFixedSize(30,30);brand_row.addWidget(brand_icon);brand=QLabel('锦纷资产');brand.setStyleSheet('font-size:22px;font-weight:700;color:#365cf5;');brand_row.addWidget(brand);brand_row.addStretch();side.addLayout(brand_row);side.addSpacing(15)
        nav_scroll=QScrollArea();nav_scroll.setWidgetResizable(True);nav_widget=QWidget();nav_layout=QVBoxLayout(nav_widget);nav_layout.setContentsMargins(0,0,0,0);nav_layout.setSpacing(3);nav_scroll.setWidget(nav_widget);side.addWidget(nav_scroll,1)
        self.nav={}
        for key,title in [('dashboard','总览工作台'),('analytics','数据分析中心'),('assets','资产台账'),('employees','员工资料'),('departments','部门管理'),('suppliers','供应商'),('purchases','采购申请'),('repairs','维修工单'),('counts','资产盘点'),('tasks','待办与提醒'),('events','流转记录'),('import_batches','导入履历'),('reports','折旧与报表'),('users','账号与权限'),('audit','操作审计'),('data','模板与数据管理')]:
            if not self.s.page_allowed(key):continue
            b=button(title,lambda k=key:handle(self,lambda:self.navigate(k)));b.setObjectName('nav');b.setCheckable(True);nav_layout.addWidget(b);self.nav[key]=b
        nav_layout.addStretch();self.db_mode=QLabel('● '+mode_text(self.db));self.db_mode.setObjectName('subtitle');self.db_mode.setWordWrap(True);side.addWidget(self.db_mode);self.share_badge=QLabel();self.share_badge.setObjectName('subtitle');self.share_badge.setWordWrap(True);side.addWidget(self.share_badge);side.addWidget(QLabel('v'+VERSION));layout.addWidget(sidebar)
        body=QWidget();main=QVBoxLayout(body);main.setContentsMargins(32,25,32,20);main.setSpacing(18)
        top=QHBoxLayout();self.title=QLabel();self.title.setObjectName('title');top.addWidget(self.title);top.addStretch();top.addWidget(QLabel(self.user['display_name']+' · '+self.user['role']));top.addWidget(button('运行设置',self.desktop_settings));top.addWidget(button('缩小到托盘',self.hide_to_tray));top.addWidget(button('关于系统',lambda:AboutDialog(VERSION,self).exec()));top.addWidget(button('修改密码',lambda:handle(self,self.password)));top.addWidget(button('退出登录',lambda:handle(self,self.logout)));main.addLayout(top)
        self.subtitle=QLabel();self.subtitle.setObjectName('subtitle');main.addWidget(self.subtitle)
        self.stack=QStackedWidget();main.addWidget(self.stack,1);layout.addWidget(body,1)
        self.dashboard=QWidget();self.dashboard_layout=QVBoxLayout(self.dashboard);self.dashboard_layout.setContentsMargins(0,0,0,0);self.stack.addWidget(self.dashboard)
        self.list_page=QWidget();lp=QVBoxLayout(self.list_page);lp.setContentsMargins(0,0,0,0);lp.setSpacing(14)
        searchrow=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('搜索当前列表中的编号、名称、人员或其他信息…');self.search.textChanged.connect(self.render_table);searchrow.addWidget(self.search,1)
        self.status_filter=QComboBox();self.status_filter.addItems(['全部状态']+STATUS);self.status_filter.currentTextChanged.connect(self.render_table);searchrow.addWidget(self.status_filter)
        self.category_filter=QComboBox();self.category_filter.currentTextChanged.connect(self.render_table);searchrow.addWidget(self.category_filter);searchrow.addWidget(button('刷新',lambda:handle(self,self.refresh)));lp.addLayout(searchrow)
        self.action_row=QHBoxLayout();lp.addLayout(self.action_row);self.table=AssetTable();center_table(self.table);self.table.imagesDropped.connect(lambda aid,paths:handle(self,lambda:self.upload_asset_photos(aid,paths)));self.table.setAlternatingRowColors(True);self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.verticalHeader().setVisible(False);self.table.setWordWrap(False);self.table.cellDoubleClicked.connect(self.open_clicked_cell);lp.addWidget(self.table,1);self.foot=QLabel();self.foot.setObjectName('subtitle');lp.addWidget(self.foot);self.stack.addWidget(self.list_page)
        self.data_page=self.make_data();self.stack.addWidget(self.data_page);self.statusBar().showMessage('就绪 · 数据实时写入数据库 · 请定期备份')
        self.analytics_page=AnalyticsPage(self.s,self);self.stack.addWidget(self.analytics_page)
        self.navigate(next(iter(self.nav)));self.timer=QTimer(self);self.timer.timeout.connect(self.ping_session);self.timer.start(10000)
        self.auto_backup_timer=QTimer(self);self.auto_backup_timer.setSingleShot(True);self.auto_backup_timer.timeout.connect(lambda:handle(self,self.auto_backup));self.auto_backup_timer.start(500)
        self.share_timer=QTimer(self);self.share_timer.timeout.connect(self.refresh_share);QTimer.singleShot(0,self.restore_share)
        self.shortcuts=[]
        for key,callback in [('Ctrl+F',lambda:self.search.setFocus()),('F5',lambda:handle(self,self.refresh)),('Ctrl+E',lambda:handle(self,self.analytics_page.export_data if self.page=='analytics' else self.export_current) if self.page in MODULES or self.page=='analytics' else None),('Ctrl+N',lambda:handle(self,self.asset_edit) if self.writer else None)]:
            shortcut=QShortcut(QKeySequence(key),self);shortcut.activated.connect(callback);self.shortcuts.append(shortcut)

    def ping_session(self):
        try:
            current=self.s.require()
            if current['role']!=self.user['role']:
                message(self,'账号权限已变化，请重新登录。');self.logout_requested=True;self.close()
        except BusinessError:
            self.logout_requested=True;self.s.logout();self.close()
        except Exception:
            self.statusBar().showMessage('账号或数据库连接异常，请刷新或重新登录')
    def closeEvent(self,event):
        desktop=getattr(self,'desktop',None)
        if desktop:desktop.request_close(self,event)
        else:super().closeEvent(event)
    def changeEvent(self,event):
        super().changeEvent(event)
        desktop=getattr(self,'desktop',None)
        if desktop and event.type()==QEvent.WindowStateChange and self.isMinimized() and desktop.preferences['minimize_to_tray']:
            QTimer.singleShot(0,lambda:desktop.hide_to_tray(self) if not desktop.exiting else None)
    def hide_to_tray(self):
        if getattr(self,'desktop',None):self.desktop.hide_to_tray(self)
        else:self.showMinimized()
    def desktop_settings(self):
        if getattr(self,'desktop',None):self.desktop.settings()
        else:message(self,'运行设置请通过正式桌面启动入口打开。')

    def clear_layout(self,layout):
        while layout.count():
            item=layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
            if item.layout():self.clear_layout(item.layout())

    def navigate(self,key):
        self.s.require()
        if not self.s.page_allowed(key):raise BusinessError('无权访问此功能模块')
        self.page=key
        for k,b in self.nav.items():b.setChecked(k==key)
        if key=='dashboard':
            self.title.setText('总览工作台');self.subtitle.setText(date.today().isoformat()+'  /  资产概况、待办与风险提醒');self.stack.setCurrentWidget(self.dashboard);self.refresh_dashboard()
        elif key=='analytics':
            self.title.setText('数据分析中心');self.subtitle.setText('当前台账分析 · 历史流转趋势 · 财务测算 · 点击图表查看明细');self.stack.setCurrentWidget(self.analytics_page);self.analytics_page.refresh()
        elif key=='data':
            self.title.setText('模板与数据管理');self.subtitle.setText('下载模板 → 填写资料 → 上传文件 → 预览校验 → 确认导入');self.stack.setCurrentWidget(self.data_page)
        else:
            title,subtitle,_=MODULES[key];self.title.setText(title);self.subtitle.setText(subtitle);self.stack.setCurrentWidget(self.list_page);self.rows=[];self.search.clear();self.configure_actions();self.refresh()

    def configure_actions(self):
        self.clear_layout(self.action_row)
        def add(name,callback,write=False,admin=False,primary=False,permission=None):
            specific={'＋ 新增资产':'assets.create','编辑资料':'assets.edit','审批申请':'purchases.approve','＋ 采购申请':'purchases.create','批准后入库':'purchases.receive','＋ 建立盘点':'counts.write','结束盘点':'counts.write','＋ 新增待办':'tasks.write','完成 / 重新开启':'tasks.write','导出 Excel / CSV':'data.export','打印 / 导出 PDF':'data.print'}
            permission=permission or specific.get(name,self.page+'.write' if write else None)
            if name=='办理流转':permission=None
            b=button(name,lambda:handle(self,callback),primary);b.setEnabled((not permission or self.s.can(permission)) and (not admin or self.admin or name=='审批申请'));self.action_row.addWidget(b)
            if name=='办理流转':b.setEnabled(any(self.s.can(p) for p in ['assets.issue','assets.transfer','assets.repair','assets.scrap']))
        if self.page=='assets':
            add('＋ 新增资产',self.asset_edit,True,primary=True);add('编辑资料',lambda:self.asset_edit(self.selected()),True);add('查看详情',self.asset_detail);add('办理流转',self.asset_flow,True)
            more=QPushButton('更多功能 ▾');menu=QMenu(more)
            for title,callback,write in [('上传资产图片',self.choose_asset_photos,True),('下载资产模板',self.template,False),('上传文件导入',self.import_assets,True),('管理资产附件',lambda:AttachmentDialog(self.s,self.selected(),self).exec(),False),('批量流转',self.batch_flow,True),('复制新增资产',self.copy_asset,True),('二维码 / 标签',self.label,False),('删除资产',self.asset_delete,False)]:
                p={'上传资产图片':'attachments.write','上传文件导入':'data.import','管理资产附件':'attachments.read','复制新增资产':'assets.create','二维码 / 标签':'data.export','删除资产':'assets.delete'}.get(title)
                enabled=self.s.can(p) if p else (any(self.s.can(x) for x in ['assets.issue','assets.transfer']) if title=='批量流转' else True)
                action=menu.addAction(title);action.setEnabled(enabled);action.triggered.connect(lambda _,fn=callback:handle(self,fn))
            more.setMenu(menu);self.action_row.addWidget(more)
        elif self.page in ['employees','departments','suppliers']:
            add('＋ 新增',lambda:self.catalog_edit(),True,primary=True);add('编辑',lambda:self.catalog_edit(self.selected()),True);add('删除',self.catalog_delete,permission=self.page+'.delete')
            add('下载模板',lambda:TemplateDialog(self.s,self,self.page).exec());add('上传文件导入',lambda:self.import_assets(self.page),True)
        elif self.page=='purchases':
            add('＋ 采购申请',self.purchase_new,True,primary=True);add('修改申请',self.purchase_edit,permission='purchases.edit');add('审批申请',self.purchase_approve,admin=True);add('批准后入库',self.purchase_receive,True);add('申请详情',self.record_detail);add('删除申请',self.purchase_delete,permission='purchases.delete')
        elif self.page=='counts':
            add('＋ 建立盘点',self.count_new,True,primary=True);add('登记 / 查看盘点',self.count_detail);add('结束盘点',self.count_close,True);add('删除盘点',self.count_delete,permission='counts.delete')
        elif self.page=='repairs':add('查看 / 办理维修',self.repair_action);add('编辑工单',self.repair_edit,permission='repairs.write');add('删除工单',self.repair_delete,permission='repairs.delete')
        elif self.page=='users':
            add('审核 / 基础角色 / 解锁',self.user_edit,admin=True,primary=True);add('高级授权',lambda:UserAccessDialog(self.s,self.selected(),self).exec(),admin=True);add('角色权限矩阵',lambda:RolesDialog(self.s,self).exec(),admin=True)
            add('删除账号',self.user_delete,permission='users.delete')
        elif self.page=='tasks':add('＋ 新增待办',self.task_new,True,primary=True);add('编辑待办',self.task_edit,permission='tasks.write');add('完成 / 重新开启',self.task_finish,True);add('删除待办',self.task_delete,permission='tasks.delete');add('完整内容',self.record_detail)
        elif self.page in ['events','audit','import_batches']:add('查看完整内容',self.record_detail);add('删除记录',self.log_delete,permission='audit.delete')
        elif self.page=='reports':add('计算说明',lambda:message(self,'按购入日期经过的完整月数进行直线法折旧。\n可折旧额 = 单价 × 数量 × (1 − 残值率)。\n累计折旧不超过设定使用月数。\n已报废资产管理净值按 0 展示。\n此统计为资产管理估算，不替代财务凭证。'))
        self.action_row.addStretch();add('导出 Excel / CSV',self.export_current);add('打印 / 导出 PDF',self.print_current)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection if self.page=='assets' else QAbstractItemView.SingleSelection)

    def refresh(self):
        if self.page=='dashboard':return self.refresh_dashboard()
        if self.page=='analytics':return self.analytics_page.refresh()
        if self.page=='data':return
        self.s.require()
        if not self.s.page_allowed(self.page):raise BusinessError('无权查看当前模块')
        table='assets' if self.page=='reports' else self.page
        rows=self.s.list(table)
        self.photo_icons={}
        if self.page=='assets' and self.s.can('attachments.read'):
            for aid,raw in table_thumbnails(self.s,[a['id'] for a in rows]).items():
                pix=QPixmap();pix.loadFromData(raw);self.photo_icons[aid]=QIcon(pix)
        employees={x['id']:x['name'] for x in self.s.list('employees')};departments={x['id']:x['name'] for x in self.s.list('departments')};assets={x['id']:x for x in self.s.list('assets')}
        # No password or locked-user details are exposed to non-administrators.
        users={x['id']:x['display_name'] for x in self.db.all('SELECT id,display_name FROM users')} if self.admin else {self.user['id']:self.user['display_name']}
        count_items=self.s.list('count_items') if self.page=='counts' else []
        for r in rows:
            r['created']=text_time(r.get('created_at',''));r['short_id']=r['id'][:8] if 'id' in r else ''
            r['actor']=users.get(r.get('actor_id'),r.get('actor_id',''))
            if self.page=='assets':r.update(employee=employees.get(r['employee_id'],''),price=fmt_money(r['price_cents']))
            elif self.page=='employees':r.update(department=departments.get(r['department_id'],''),active_text='在职 / 启用' if r['active'] else '停用')
            elif self.page=='repairs':r.update(asset_name=assets.get(r['asset_id'],{}).get('name',''),cost=fmt_money(r['cost_cents']),opened=text_time(r['opened_at']),closed=text_time(r['closed_at']))
            elif self.page=='purchases':r.update(requester=users.get(r['requester_id'],''),approver=users.get(r['approver_id'],''),budget=fmt_money(r['budget_cents']))
            elif self.page=='counts':
                items=[i for i in count_items if i['count_id']==r['id']];r.update(progress=f"{sum(i['result']!='待盘点' for i in items)} / {len(items)}",differences=sum(i['result']=='差异' for i in items))
            elif self.page=='users':
                c=self.s.profile(r);r.update(active_text='已启用' if r['active'] else '停用',locked=text_time(r['locked_until']),effective_roles='、'.join(c['roles']),scope_text={'all':'全部数据','locations':'位置：'+'、'.join(c['locations']),'self':'本人：'+c['employee_id']}[c['scope']],permission_count=len(c['permissions']),access_expiry=text_time(c['expires']) if c['expires'] else '长期有效',owner_text=BUILTIN_LABEL if str(r['id'])==self.s.owner_id() else '')
            elif self.page=='tasks':r['creator']=users.get(r['creator_id'],'')
            elif self.page=='reports':
                original,dep,net=self.s.depreciation(r);r.update(original=fmt_money(original),depreciated=fmt_money(dep),net=fmt_money(net),residual=str(Decimal(r['residual_bp'])/100)+'%')
        self.rows=sorted(rows,key=lambda r:r.get('created_at',r.get('opened_at',r.get('id',''))),reverse=self.page in ['events','audit','purchases','counts','repairs','users'])
        self.status_filter.setVisible(self.page in ['assets','reports']);self.category_filter.setVisible(self.page in ['assets','reports'])
        prev=self.category_filter.currentText();self.category_filter.blockSignals(True);self.category_filter.clear();self.category_filter.addItems(['全部类别']+sorted({a['category'] for a in assets.values()}));idx=self.category_filter.findText(prev);self.category_filter.setCurrentIndex(max(idx,0));self.category_filter.blockSignals(False);self.render_table()

    def render_table(self):
        if self.page not in MODULES or not hasattr(self,'rows'):return
        columns=MODULES[self.page][2];query=self.search.text().strip().lower();status=self.status_filter.currentText();category=self.category_filter.currentText()
        self.visible=[r for r in self.rows if (not query or query in ' '.join(str(r.get(k,'')) for k,_ in columns).lower()) and (self.page not in ['assets','reports'] or ((status=='全部状态' or r['status']==status) and (category=='全部类别' or r['category']==category)))]
        # Table view is bounded, exports retain every matching record.
        shown=self.visible[:5000];self.table.setSortingEnabled(False);self.table.clear();self.table.setColumnCount(len(columns));self.table.setHorizontalHeaderLabels([title for _,title in columns]);center_header(self.table);self.table.setRowCount(len(shown))
        self.table.photo_drop_enabled=self.page=='assets' and self.s.can('attachments.write');self.table.verticalHeader().setDefaultSectionSize(76 if self.page=='assets' else 34)
        for i,row in enumerate(shown):
            for j,(key,_) in enumerate(columns):
                item=QTableWidgetItem(str(row.get(key,'') or ('0' if row.get(key)==0 else '')));item.setData(Qt.UserRole,row.get('id'));item.setToolTip(item.text())
                if key=='photo':
                    icon=getattr(self,'photo_icons',{}).get(row['id']);item.setText('' if icon else ('拖入图片' if self.table.photo_drop_enabled else '暂无图片'));item.setTextAlignment(Qt.AlignCenter)
                    if icon:item.setIcon(icon)
                    item.setToolTip('双击查看资产图片与详情；将图片拖到该资产行可上传。')
                if key in ['status','result']:
                    item.setForeground(QColor({'闲置':'#23956c','在用':'#365cf5','维修中':'#bb832c','已报废':'#c7667d','待审批':'#bb832c','差异':'#d75d65'}.get(item.text(),'#52647d')))
                self.table.setItem(i,j,item)
        self.table.resizeColumnsToContents();self.table.horizontalHeader().setStretchLastSection(True)
        for j in range(len(columns)):self.table.setColumnWidth(j,min(360,max(90,self.table.columnWidth(j))))
        self.foot.setText(f'共 {len(self.visible)} 条匹配记录'+(' · 仅显示前 5000 条，导出包含全部匹配数据' if len(self.visible)>5000 else '')+' · 双击查看 / 编辑')
        if self.page in ['assets','reports']:self.foot.setText(self.foot.text().replace('双击查看 / 编辑','双击任意一列查看资产详情'))
        if self.page=='reports':
            totals=[0,0,0]
            for a in self.visible:
                for i,n in enumerate(self.s.depreciation(a)):totals[i]+=n
            self.foot.setText(self.foot.text()+'   |   原值 ￥'+fmt_money(totals[0])+'   累计折旧 ￥'+fmt_money(totals[1])+'   净值 ￥'+fmt_money(totals[2]))

    def selected(self):
        row=self.table.currentRow()
        if row<0 or not self.table.item(row,0):raise BusinessError('请先选择一条记录')
        id=self.table.item(row,0).data(Qt.UserRole)
        return next(r for r in self.visible if r['id']==id)

    def default_action(self):
        if self.page in ['assets','reports']:self.asset_detail()
        elif self.page in ['employees','departments','suppliers'] and self.writer:self.catalog_edit(self.selected())
        elif self.page=='counts':self.count_detail()
        elif self.page=='repairs':self.repair_action()
        elif self.page=='users':self.user_edit()
        else:self.record_detail()

    def open_clicked_cell(self,row,column):
        self.table.setCurrentCell(row,column)
        handle(self,self.default_action)

    def upload_asset_photos(self,asset_id,paths):
        if self.page!='assets':raise BusinessError('请在资产台账中拖入图片')
        upload_images(self.s,asset_id,paths);self.refresh();self.statusBar().showMessage('资产 '+asset_id+' 的图片已上传，可双击任意一列查看详情。')

    def choose_asset_photos(self):
        aid=self.selected()['id'];paths,_=QFileDialog.getOpenFileNames(self,'上传资产图片','','图片 (*.png *.jpg *.jpeg *.bmp *.webp)')
        if paths:self.upload_asset_photos(aid,paths)

    def asset_edit(self,a=None,purchase=None,copy_values=None):
        self.s.permit('assets.edit' if a else 'assets.create')
        if a:self.s.check_asset(a['id'])
        suppliers=[('','未选择')]+[(s['id'],s['name']+' / '+s['id']) for s in self.s.list('suppliers')]
        spec=[('id','资产编号 *','readonly' if a else 'line'),('name','设备名称 / 配置 *','line'),('category','类别 *','line'),('brand','品牌型号','line'),('serial','序列号','line'),('quantity','数量 *','int',(1,1000000)),('owner','归属 / 所有权方','line'),('location','存放位置','line'),('supplier_id','供应商','choice',suppliers),('purchase_date','购入日期','date'),('price','每件单价（元）','line'),('life_months','折旧使用月数','int',(1,1200)),('residual','残值率（%）','line'),('warranty_date','保修到期','date'),('notes','其他备注','text')]
        values=dict(copy_values or a or {'quantity':1,'category':'电脑主机','life_months':36,'residual_bp':500,'price_cents':0,'owner':'公司'})
        if purchase:values.update(name=purchase['name'],category=purchase['category'],quantity=purchase['quantity'],purchase_date=date.today().isoformat(),price_cents=purchase['budget_cents']//purchase['quantity'])
        values['price']=str(Decimal(values['price_cents'])/100);values['residual']=str(Decimal(values['residual_bp'])/100)
        picker=PhotoPicker() if self.s.can('attachments.write') else None
        dialog=FormDialog('复制新增资产' if copy_values else '采购入库' if purchase else '编辑资产' if a else '新增资产',spec,values,self,'每台需要独立跟踪的设备，请分别编号。数量大于 1 的台账按整条领用。\n资产图片可直接拖入下方区域，或点击“选择图片”，保存资产时一并上传。',extra=picker)
        screen=QApplication.primaryScreen();available=screen.availableGeometry().height() if screen else 900
        dialog.resize(680,max(640,min(920 if picker is not None else 820,available-90)))
        if dialog.exec()==QDialog.Accepted:
            d=dialog.values();d['price_cents']=money(d.pop('price'));d['residual_bp']=money(d.pop('residual'))
            if purchase:
                self.s.receive_purchase(purchase['id'],d)
                row=self.s.db.one('SELECT asset_id FROM purchases WHERE id=?',(purchase['id'],));target=row['asset_id'] if row else ''
            else:
                saved=self.s.save_asset(d,a['revision'] if a else None);target=saved['id']
            uploaded=0
            if picker is not None and picker.paths and target:
                try:uploaded=upload_images(self.s,target,picker.paths)
                except BusinessError as e:QMessageBox.warning(self,'资产图片',str(e)+'\n\n资产资料已保存，但图片未上传，可稍后在详情中重新上传。')
            self.refresh()
            message(self,f'资产已保存，图片已上传 {uploaded} 张。' if uploaded else '资产已保存。')

    def asset_detail(self,a=None):
        self.s.permit('assets.read');a=a or self.selected();a=self.s.check_asset(a['id'])
        dialog=AssetDetailDialog(self.s,a['id'],self);dialog.photosChanged.connect(lambda:self.refresh() if self.page=='assets' else None);dialog.exec()

    def asset_flow(self,a=None):
        a=a or self.selected();a=self.s.check_asset(a['id'])
        options={'闲置':['领用','送修','移动位置','报废'],'在用':['归还','调拨','送修','移动位置','报废'],'维修中':['维修完成','报废'],'已报废':[]}[a['status']]
        rights={'领用':'assets.issue','归还':'assets.issue','调拨':'assets.transfer','移动位置':'assets.transfer','送修':'assets.repair','维修完成':'assets.repair','报废':'assets.scrap'}
        options=[x for x in options if self.s.can(rights[x])]
        if not options:raise BusinessError('当前状态没有可办理的流转操作')
        employees=[('','请选择员工')]+[(e['id'],e['name']+' / '+e['id']) for e in self.s.list('employees') if e['active']]
        spec=[('action','办理操作','choice',options),('employee_id','新使用员工','choice',employees),('due_date','应还日期','date'),('location','新存放位置','line'),('vendor','维修方','line'),('cost','维修完成实际费用（元）','line'),('note','说明 / 原因 / 维修结果','text')]
        dialog=FormDialog('流转 · '+a['id'],spec,{'location':a['location'],'cost':'0'},self,a['name']+' · 当前状态 '+a['status'])
        def change():
            act=dialog.widgets['action'].currentData()
            for key,enabled in [('employee_id',act in ['领用','调拨']),('due_date',act in ['领用','调拨']),('location',act=='移动位置'),('vendor',act=='送修'),('cost',act=='维修完成' or (act=='报废' and a['status']=='维修中'))]:dialog.widgets[key].setEnabled(enabled)
        dialog.widgets['action'].currentIndexChanged.connect(change);change()
        if dialog.exec()==QDialog.Accepted:
            d=dialog.values()
            if d['action']=='报废' and not question(self,'确认报废资产 '+a['id']+'？\n报废后只能查看历史，不可继续领用或编辑。'):return
            self.s.flow(a['id'],a['revision'],**d);self.refresh();message(self,'流转操作已完成，历史已记录。')

    def copy_asset(self):
        row=self.selected();values=dict(row);values['id']=''
        # Reuse the new-asset form, never copy use status or original identity.
        self.asset_edit(copy_values=values)

    def batch_flow(self):
        selected_ids={self.table.item(index.row(),0).data(Qt.UserRole) for index in self.table.selectionModel().selectedRows()}
        rows=[r for r in self.visible if r['id'] in selected_ids]
        if not rows:raise BusinessError('请按 Ctrl 或 Shift 多选资产后办理批量操作')
        employees=[('','请选择员工')]+[(r['id'],r['name']+' / '+r['id']) for r in self.s.list('employees') if r['active']]
        dialog=FormDialog('批量资产流转',[('action','批量操作','choice',['领用','归还','移动位置']),('employee_id','使用员工','choice',employees),('due_date','应还日期','date'),('location','新位置','line'),('note','说明','text')],parent=self,description=f'已选择 {len(rows)} 条资产。全部成功后统一提交；任何一条不满足条件，整批不修改。')
        def toggle():
            action=dialog.widgets['action'].currentData()
            for key,enabled in [('employee_id',action=='领用'),('due_date',action=='领用'),('location',action=='移动位置')]:dialog.widgets[key].setEnabled(enabled)
        dialog.widgets['action'].currentIndexChanged.connect(toggle);toggle()
        if dialog.exec()==QDialog.Accepted:
            values=dialog.values();action=values.pop('action')
            if question(self,f'确认批量{action} {len(rows)} 条资产？'):self.s.batch_flow(rows,action,**values);self.refresh();message(self,'批量操作已完成。')

    def task_new(self):
        assets=[('','不关联资产')]+[(a['id'],a['id']+' / '+a['name']) for a in self.s.list('assets')]
        dialog=FormDialog('新增待办',[('title','待办标题 *','line'),('asset_id','关联资产','choice',assets),('priority','优先级','choice',['普通','重要','紧急']),('due_date','截止日期','date'),('notes','任务说明','text')],parent=self)
        if dialog.exec()==QDialog.Accepted:self.s.save_task(dialog.values());self.refresh()

    def task_finish(self):
        task=self.selected();self.s.finish_task(task['id'],task['status']=='已完成');self.refresh()

    def catalog_edit(self,row=None):
        self.s.permit(self.page+'.write');self.s.global_scope()
        table=self.page;readonly='readonly' if row else 'line';spec=[('id','编号 *',readonly),('name','名称 / 姓名 *','line')]
        if table=='departments':spec += [('manager','负责人','line')]
        elif table=='suppliers':spec += [('contact','联系人','line'),('phone','电话','line')]
        else:
            departments=[('','未选择')]+[(x['id'],x['name']) for x in self.s.list('departments')]
            spec += [('department_id','部门','choice',departments),('phone','电话','line'),('email','邮箱','line'),('active','状态','check')]
        spec += [('notes','备注','text')]
        dialog=FormDialog('编辑资料' if row else '新增资料',spec,row or {'active':1},self)
        if dialog.exec()==QDialog.Accepted:self.s.catalog(table,dialog.values(),bool(row));self.refresh()

    def purchase_new(self):
        dialog=FormDialog('采购申请',[('name','设备名称 *','line'),('category','类别','line'),('quantity','采购数量','int',(1,1000000)),('budget','总预算（元）','line'),('reason','采购理由 *','text')],{'quantity':1,'budget':'0','category':'电脑主机'},self,'需要由另一名管理员审批，申请人与审批人不能是同一账号。')
        if dialog.exec()==QDialog.Accepted:self.s.new_purchase(dialog.values());self.refresh()
    def purchase_approve(self):
        p=self.selected();dialog=FormDialog('采购审批',[('approved','审批结果','choice',[(True,'批准'),(False,'拒绝')]),('comment','审批意见 / 拒绝理由','text')],parent=self,description=p['name']+' · 总预算 ￥'+fmt_money(p['budget_cents']))
        if dialog.exec()==QDialog.Accepted:
            d=dialog.values();self.s.approve_purchase(p['id'],d['approved'],d['comment']);self.refresh()
    def purchase_receive(self):
        p=self.selected()
        if p['status']!='已批准':raise BusinessError('请选择已批准的采购申请')
        self.asset_edit(purchase=p)
    def repair_action(self):
        r=self.selected();a=self.s.check_asset(r['asset_id'])
        if r['status']=='维修中' and self.s.can('assets.repair'):self.asset_flow(a)
        else:self.plain_dialog('维修工单','\n'.join(f'{k}：{v}' for k,v in r.items()))

    def count_new(self):
        locations=sorted({a['location'] for a in self.s.list('assets') if a['location']})
        dialog=FormDialog('建立盘点',[('name','盘点名称 *','line'),('location','盘点位置范围','choice',[('','全部位置')]+[(x,x) for x in locations])],{'name':date.today().isoformat()+' 资产盘点'},self,'创建时保存数量快照。盘点差异作为记录保留，不会自动覆盖台账。')
        if dialog.exec()==QDialog.Accepted:self.s.new_count(**dialog.values());self.refresh()
    def count_close(self):
        c=self.selected()
        if question(self,'结束盘点 '+c['name']+'？\n所有项目须已登记，结束后不能修改。'):self.s.close_count(c['id']);self.refresh()
    def count_detail(self):
        count=self.selected();dialog=QDialog(self);dialog.setWindowTitle('盘点 · '+count['name']);dialog.resize(1000,620);layout=QVBoxLayout(dialog)
        info=QLabel('双击项目登记实盘数量。数量差异不会自动修改资产台账。');info.setObjectName('subtitle');layout.addWidget(info)
        table=QTableWidget();table.setColumnCount(7);table.setHorizontalHeaderLabels(['资产编号','设备名称','账面数量','账面状态','实盘数量','结果','说明']);center_table(table);table.setSelectionBehavior(QAbstractItemView.SelectRows);table.setEditTriggers(QAbstractItemView.NoEditTriggers);table.verticalHeader().setVisible(False);layout.addWidget(table)
        items=[];assets={a['id']:a for a in self.s.list('assets')}
        def reload():
            nonlocal items
            items=[i for i in self.s.list('count_items') if i['count_id']==count['id']];table.setRowCount(len(items))
            for row,i in enumerate(items):
                values=[i['asset_id'],assets.get(i['asset_id'],{}).get('name',''),i['expected_quantity'],i['expected_status'],i['actual_quantity'] if i['actual_quantity']>=0 else '未登记',i['result'],i['notes']]
                for col,value in enumerate(values):table.setItem(row,col,QTableWidgetItem(str(value)))
            table.resizeColumnsToContents();table.horizontalHeader().setStretchLastSection(True)
        def edit():
            row=table.currentRow()
            if row<0:raise BusinessError('请选择盘点项目')
            i=items[row];form=FormDialog('登记实盘 · '+i['asset_id'],[('actual','实盘数量','int',(0,1000000)),('notes','差异说明 / 备注','text')],{'actual':max(0,i['actual_quantity']),'notes':i['notes']},dialog)
            if form.exec()==QDialog.Accepted:self.s.check_item(i['id'],**form.values());reload();self.refresh()
        def export():
            self.s.permit('data.export');self.s.permit('counts.read')
            path,_=QFileDialog.getSaveFileName(dialog,'导出盘点',count['name']+'.xlsx','Excel (*.xlsx);;CSV (*.csv)')
            if path:write_table(path,['资产编号','设备名称','账面数量','账面状态','实盘数量','结果','说明'],[[table.item(r,c).text() for c in range(7)] for r in range(table.rowCount())]);message(dialog,'盘点明细已导出。')
        table.cellDoubleClicked.connect(lambda r,c:handle(dialog,edit) if self.s.can('counts.write') and count['status']=='进行中' else None)
        buttons=QHBoxLayout();b=button('登记选中项目',lambda:handle(dialog,edit),True);b.setEnabled(self.s.can('counts.write') and count['status']=='进行中');buttons.addWidget(b);buttons.addWidget(button('导出盘点明细',lambda:handle(dialog,export)));buttons.addStretch();buttons.addWidget(button('关闭',dialog.accept));layout.addLayout(buttons);reload();dialog.exec()

    def user_edit(self):
        u=self.selected();target_owner=str(u['id'])==self.s.owner_id()
        if target_owner:
            return message(self,'内置管理员（用户名 '+BUILTIN_USERNAME+'）是软件出厂固定账号：\n\n· 用户名、口令、角色（管理员）在所有电脑、所有数据库中都相同；\n· 不能在本软件中修改口令、停用或降级，也不能删除；\n· 若数据库中的该账号被改动，下次登录时会自动恢复为出厂设置并写入操作审计。\n\n如需增加管理员，请在本页选中「普通账号」后把「角色」设为管理员。')
        if not self.owner and u['role']=='管理员':
            raise BusinessError('只有内置管理员账号可以管理管理员账号')
        roles=list(ROLES) if self.owner else [r for r in ROLES if r!='管理员']
        if self.owner:
            note='保存会清除失败次数和临时锁定。停用或降级当前管理员被禁止。\n把角色设为「管理员」即授予完整权限；只有内置管理员账号可以这样做。'
        else:
            note='保存会清除失败次数和临时锁定。\n管理员角色只能由内置管理员账号授予，此处的角色不包含管理员。'
        dialog=FormDialog('账号管理 · '+u['username'],[('role','角色','choice',roles),('active','账号状态','check'),('new_password','重置密码（留空不修改）','password')],u,self,note)
        if dialog.exec()==QDialog.Accepted:self.s.manage_user(u['id'],**dialog.values());self.refresh()
    def password(self):
        d=FormDialog('修改当前账号密码',[('old','原密码','password'),('new','新密码','password'),('confirm','再次输入新密码','password')],parent=self)
        if d.exec()==QDialog.Accepted:
            v=d.values()
            if v['new']!=v['confirm']:raise BusinessError('两次新密码不一致')
            self.s.change_password(v['old'],v['new']);message(self,'密码已修改。')
    def logout(self):
        self.s.logout();self.logout_requested=True;self.close()
    def record_detail(self):
        if not self.s.page_allowed(self.page):raise BusinessError('无权查看当前模块')
        r=self.selected();self.plain_dialog('完整记录','\n'.join(f'{key}：{value}' for key,value in r.items()))
    def plain_dialog(self,title,text):
        dialog=QDialog(self);dialog.setWindowTitle(title);dialog.resize(790,650);v=QVBoxLayout(dialog);box=QTextEdit();box.setReadOnly(True);box.setPlainText(text);v.addWidget(box);v.addWidget(button('关闭',dialog.accept));dialog.exec()

    # ---- 增删改查补全：各模块的删除与内容编辑入口 ----
    def asset_delete(self):
        a=self.selected()
        if not question(self,'确认删除资产 '+a['id']+'（'+a['name']+'）？\n\n仅当该资产没有任何流转、维修、附件、盘点、采购或待办引用时才能删除；\n删除后不可恢复。已经使用过的资产请改用「办理流转 → 报废」保留完整历史。'):return
        self.s.delete_asset(a['id']);self.refresh();message(self,'资产已删除。')

    def catalog_delete(self):
        table=self.page;names={'departments':'部门','employees':'员工','suppliers':'供应商'};row=self.selected()
        if not question(self,'确认删除'+names[table]+'「'+row['name']+'」('+row['id']+')？\n\n若仍有其它资料引用该记录，删除会被拒绝并说明原因。'):return
        self.s.delete_catalog(table,row['id']);self.refresh();message(self,names[table]+'已删除。')

    def purchase_edit(self):
        p=self.selected()
        if p['status']!='待审批':raise BusinessError('只有「待审批」的采购申请可以修改内容')
        dialog=FormDialog('修改采购申请',[('name','设备名称 *','line'),('category','类别','line'),('quantity','采购数量','int',(1,1000000)),('budget','总预算（元）','line'),('reason','采购理由 *','text')],{'name':p['name'],'category':p['category'],'quantity':p['quantity'],'budget':fmt_money(p['budget_cents']),'reason':p['reason']},self,'修改后仍需由另一名管理员审批，申请人与审批人不能是同一账号。')
        if dialog.exec()==QDialog.Accepted:self.s.update_purchase(p['id'],dialog.values());self.refresh();message(self,'采购申请已更新。')

    def purchase_delete(self):
        p=self.selected()
        if not question(self,'确认删除采购申请「'+p['name']+'」('+p['id']+')？\n\n只有「待审批」或「已拒绝」的申请可以删除；已批准、已入库的申请保留审批与入库记录。'):return
        self.s.delete_purchase(p['id']);self.refresh();message(self,'采购申请已删除。')

    def repair_edit(self):
        r=self.selected()
        dialog=FormDialog('编辑维修工单',[('problem','问题描述 *','text'),('vendor','维修方','line'),('cost','维修费用（元）','line'),('result','处理结果 / 维修结果','text')],{'problem':r['problem'],'vendor':r['vendor'],'cost':fmt_money(r['cost_cents']),'result':r['result']},self,r['asset_id']+' · 当前状态 '+r['status'])
        if dialog.exec()==QDialog.Accepted:self.s.update_repair(r['id'],dialog.values());self.refresh();message(self,'维修工单已更新。')

    def repair_delete(self):
        r=self.selected()
        if not question(self,'确认删除该维修工单？\n\n资产 '+r['asset_id']+' · '+(r['problem'] or '')[:40]+'\n维修中的工单不能删除，请先办理「维修完成」或「报废」。'):return
        self.s.delete_repair(r['id']);self.refresh();message(self,'维修工单已删除。')

    def count_delete(self):
        c=self.selected()
        if not question(self,'确认删除盘点「'+c['name']+'」('+c['id']+')？\n\n该盘点的全部明细登记会一并删除，删除后不可恢复。'):return
        self.s.delete_count(c['id']);self.refresh();message(self,'盘点及明细已删除。')

    def task_edit(self):
        t=self.selected()
        assets=[('','不关联资产')]+[(a['id'],a['id']+' / '+a['name']) for a in self.s.list('assets')]
        dialog=FormDialog('编辑待办',[('title','待办标题 *','line'),('asset_id','关联资产','choice',assets),('priority','优先级','choice',['普通','重要','紧急']),('due_date','截止日期','date'),('notes','任务说明','text')],{'title':t['title'],'asset_id':t['asset_id'],'priority':t['priority'],'due_date':t['due_date'],'notes':t['notes']},self,'已完成的待办需先重新开启，才能修改内容。')
        if dialog.exec()==QDialog.Accepted:
            values=dialog.values();values['id']=t['id'];self.s.update_task(values);self.refresh();message(self,'待办已更新。')

    def task_delete(self):
        t=self.selected()
        if not question(self,'确认删除待办「'+t['title']+'」？\n\n删除后不可恢复。'):return
        self.s.delete_task(t['id']);self.refresh();message(self,'待办已删除。')

    def user_delete(self):
        u=self.selected()
        if str(u['id'])==self.s.owner_id():raise BusinessError('内置管理员是出厂固定账号，不能删除')
        if u['role']=='管理员' and not self.owner:raise BusinessError('只有内置管理员账号可以删除管理员账号')
        if not question(self,'确认删除账号「'+u['username']+'」('+u['display_name']+')？\n\n该账号的高级授权配置会一并清除，删除后不可恢复；当前登录账号、内置管理员账号与最后一名管理员不能删除。'):return
        self.s.delete_user(u['id']);self.refresh();message(self,'账号已删除。')

    def log_delete(self):
        table=self.page;labels={'events':'流转记录','audit':'操作审计','import_batches':'导入履历'};r=self.selected()
        if not question(self,'确认删除这条'+labels[table]+'记录？\n\n删除后不可恢复，本次删除操作本身会写入审计。'):return
        self.s.delete_log(table,r['id']);self.refresh();message(self,labels[table]+'已删除。')

    def export_current(self):
        self.s.permit('data.export')
        if not self.s.page_allowed(self.page):raise BusinessError('无权导出当前模块')
        title=MODULES[self.page][0];path,_=QFileDialog.getSaveFileName(self,'导出当前筛选结果',title+'.xlsx','Excel (*.xlsx);;CSV (*.csv)')
        if not path:return
        if self.page=='assets':self.s.export_assets(path,self.visible)
        else:
            columns=MODULES[self.page][2];write_table(path,[t for _,t in columns],[[r.get(k,'') for k,_ in columns] for r in self.visible])
        message(self,f'已导出 {len(self.visible)} 条记录。')
    def print_current(self):
        self.s.permit('data.print')
        if not self.s.page_allowed(self.page):raise BusinessError('无权打印当前模块')
        columns=MODULES[self.page][2];title=MODULES[self.page][0]
        if len(self.visible)>2000:raise BusinessError('打印最多 2000 条，请先缩小筛选范围，或使用 Excel 导出')
        content='<h2>'+html.escape(title)+'</h2><p>'+date.today().isoformat()+'</p><table border="1" cellspacing="0" cellpadding="5"><tr>'+''.join('<th>'+html.escape(t)+'</th>' for _,t in columns)+'</tr>'+''.join('<tr>'+''.join('<td>'+html.escape(str(r.get(k,'')))+'</td>' for k,_ in columns)+'</tr>' for r in self.visible)+'</table>'
        self.print_document(content)
    def print_document(self,content):
        self.s.permit('data.print')
        options=FormDialog('打印或保存 PDF',[('mode','输出方式','choice',['保存 PDF','打印到打印机'])],parent=self)
        if options.exec()!=QDialog.Accepted:return
        printer=QPrinter(QPrinter.HighResolution)
        from PySide6.QtGui import QPageLayout
        printer.setPageOrientation(QPageLayout.Landscape)
        if options.values()['mode']=='保存 PDF':
            path,_=QFileDialog.getSaveFileName(self,'保存 PDF','资产报表.pdf','PDF (*.pdf)')
            if not path:return
            printer.setOutputFormat(QPrinter.PdfFormat);printer.setOutputFileName(path)
        elif QPrintDialog(printer,self).exec()!=QDialog.Accepted:return
        doc=QTextDocument();doc.setDefaultFont(QFont('Microsoft YaHei',9));doc.setHtml(content);doc.print_(printer);message(self,'输出完成。')

    def label(self):
        self.s.permit('data.export');self.s.check_asset(self.selected()['id'])
        a=self.selected();import qrcode
        qr=qrcode.make(json.dumps({'app':'jinfen-assets','id':a['id']},ensure_ascii=False));path=data_dir()/'asset-label.png';qr.save(path)
        dialog=QDialog(self);dialog.setWindowTitle('资产标签 · '+a['id']);v=QVBoxLayout(dialog);label=QLabel();label.setPixmap(QPixmap(str(path)).scaled(220,220,Qt.KeepAspectRatio,Qt.SmoothTransformation));label.setAlignment(Qt.AlignCenter);v.addWidget(label);v.addWidget(QLabel('编号：'+a['id']+'\n名称：'+a['name']+'\n位置：'+a['location']))
        def save():
            dest,_=QFileDialog.getSaveFileName(dialog,'保存二维码',a['id']+'_二维码.png','PNG (*.png)')
            if dest:qr.save(dest);message(dialog,'二维码已保存。')
        def print_label():
            self.print_document('<h2>锦纷资产</h2><img width="150" height="150" src="'+path.as_uri()+'"><p>编号：'+html.escape(a['id'])+'<br>名称：'+html.escape(a['name'])+'<br>位置：'+html.escape(a['location'])+'</p>')
        buttons=QHBoxLayout();buttons.addWidget(button('保存二维码 PNG',lambda:handle(dialog,save)));buttons.addWidget(button('打印资产标签',lambda:handle(dialog,print_label)));buttons.addWidget(button('关闭',dialog.accept));v.addLayout(buttons);dialog.exec()

    def refresh_dashboard(self):
        self.s.permit('analytics.read');self.s.permit('assets.read');self.clear_layout(self.dashboard_layout);assets=self.s.list('assets');purchases=self.s.list('purchases');employees={x['id']:x['name'] for x in self.s.list('employees')}
        row=QHBoxLayout()
        totals=[('资产总量',sum(a['quantity'] for a in assets),'含报废台账'),('使用中',sum(a['quantity'] for a in assets if a['status']=='在用'),'员工正在使用'),('闲置可领用',sum(a['quantity'] for a in assets if a['status']=='闲置'),'等待下一次使用'),('维修中',sum(a['quantity'] for a in assets if a['status']=='维修中'),'跟进维修与费用')]
        for title,n,sub in totals:
            frame=QFrame();frame.setObjectName('card');v=QVBoxLayout(frame);v.setContentsMargins(22,18,22,18);v.addWidget(QLabel(title));number=QLabel(str(n));number.setStyleSheet('font-size:34px;font-weight:700;color:#365cf5;');v.addWidget(number);note=QLabel(sub);note.setObjectName('subtitle');v.addWidget(note);row.addWidget(frame)
        self.dashboard_layout.addLayout(row)
        total=sum(a['price_cents']*a['quantity'] for a in assets);net=sum(self.s.depreciation(a)[2] for a in assets);pending=sum(p['status']=='待审批' for p in purchases)
        tasks=[t for t in self.s.list('tasks') if t['status']=='待处理'];overdue=sum(bool(t['due_date']) and t['due_date']<date.today().isoformat() for t in tasks)
        brief=QLabel('资产原值 ￥'+fmt_money(total)+'    ·    管理净值 ￥'+fmt_money(net)+'    ·    待审批采购 '+str(pending)+' 项    ·    待办 '+str(len(tasks))+' 项（逾期 '+str(overdue)+'）');brief.setWordWrap(True);brief.setStyleSheet('background:#edf2ff;padding:16px;border-radius:7px;color:#5f77ac;');self.dashboard_layout.addWidget(brief)
        frame=QFrame();frame.setObjectName('card');v=QVBoxLayout(frame);v.setContentsMargins(22,18,22,18);v.addWidget(QLabel('类别分布 · 按数量统计'))
        categories=sorted({a['category'] for a in assets})
        for category in categories[:8]:
            n=sum(a['quantity'] for a in assets if a['category']==category);line=QHBoxLayout();l=QLabel(category);l.setMinimumWidth(110);line.addWidget(l);bar=QProgressBar();bar.setRange(0,max(1,sum(a['quantity'] for a in assets)));bar.setValue(n);bar.setTextVisible(False);line.addWidget(bar,1);line.addWidget(QLabel(str(n)));v.addLayout(line)
        if not categories:v.addWidget(QLabel('暂无资产，点击左侧资产台账开始入库。'))
        self.dashboard_layout.addWidget(frame)
        today=date.today().isoformat();limit=(date.today()+timedelta(days=30)).isoformat();alerts=[]
        for a in assets:
            if a['status']=='在用' and a['due_date'] and a['due_date']<today:alerts.append((a,'逾期未还 · '+employees.get(a['employee_id'],'')+' · '+a['due_date']))
            if a['status']!='已报废' and a['warranty_date'] and a['warranty_date']<=limit:alerts.append((a,('保修已到期' if a['warranty_date']<today else '30 天内保修到期')+' · '+a['warranty_date']))
        title=QLabel('提醒与待办 · '+str(len(alerts))+' 条');title.setStyleSheet('font-size:16px;font-weight:600;');self.dashboard_layout.addWidget(title)
        table=QTableWidget(len(alerts),3);table.setHorizontalHeaderLabels(['资产编号','设备名称','提醒']);center_table(table);table.setEditTriggers(QAbstractItemView.NoEditTriggers);table.setSelectionBehavior(QAbstractItemView.SelectRows);table.verticalHeader().setVisible(False)
        for r,(a,notice) in enumerate(alerts):
            for c,val in enumerate([a['id'],a['name'],notice]):table.setItem(r,c,QTableWidgetItem(val))
        table.horizontalHeader().setStretchLastSection(True);table.resizeColumnsToContents();table.cellDoubleClicked.connect(lambda r,c:handle(self,lambda:self.asset_detail(alerts[r][0])));self.dashboard_layout.addWidget(table,1);self.dashboard_layout.addWidget(button('刷新总览',lambda:handle(self,self.refresh_dashboard)))

    def share_card(self):
        frame=QFrame();frame.setObjectName('card');f=QVBoxLayout(frame);f.setContentsMargins(25,24,25,24);f.setSpacing(13)
        head=QLabel('局域网数据共享（本机作为主机）');head.setStyleSheet('font-size:18px;font-weight:600;');head.setWordWrap(True);f.addWidget(head)
        note=QLabel('开启后，同一网络里的其他电脑不需要安装数据库，只要填本机地址、端口和访问口令就能共用这一份数据。\n数据只保存在本机；本软件需要保持运行，其他电脑才能读写。建议保持每日自动备份。\n其他电脑在「配置本地存储 / 数据库连接」中选择「局域网共享主机」。');note.setWordWrap(True);f.addWidget(note)
        grid=QGridLayout();grid.setHorizontalSpacing(10);grid.setVerticalSpacing(10)
        self.share_port=QSpinBox();self.share_port.setRange(1024,65535);self.share_port.setValue(int(load_share()['port']));self.share_port.setMinimumWidth(96);self.share_port.setMaximumWidth(120);self.share_port.setToolTip('其他电脑填写的共享端口，默认 8765')
        self.share_password=PasswordEdit();self.share_password.setMaxLength(64);self.share_password.setMinimumWidth(180);self.share_password.setToolTip('至少 8 位，请告诉需要使用数据的同事')
        self.share_public=QCheckBox('允许公网访问（需在路由器把端口映射到本机，务必使用强口令）');self.share_public.setToolTip('勾选后，公网上的电脑也能通过「公网IP:端口」连接。\n公网口令将暴露在整个互联网，请使用 12 位以上强口令。\n本机已内置保护：同一地址口令连续错误 5 次将被暂停连接 10 分钟。')
        self.share_public_note=QLabel('公网访问开启后，本机会自动探测出口公网 IP 并显示在下方状态里；\n还需在路由器上把该端口映射到本机（虚拟服务器 / 端口转发），公司网络请交由网管操作。');self.share_public_note.setWordWrap(True);self.share_public_note.setObjectName('subtitle');self.share_public_note.setVisible(False)
        self.share_public.toggled.connect(self.paint_public_note)
        grid.addWidget(QLabel('端口'),0,0);grid.addWidget(self.share_port,0,1)
        grid.addWidget(QLabel('访问口令'),0,2);grid.addWidget(self.share_password,0,3)
        grid.addWidget(self.share_public,1,0,1,4);grid.addWidget(self.share_public_note,2,0,1,4)
        grid.setColumnStretch(3,1);f.addLayout(grid)
        row=QHBoxLayout()
        self.share_on=button('开启共享',lambda:handle(self,self.start_share),True);row.addWidget(self.share_on)
        self.share_off=button('关闭共享',lambda:handle(self,self.stop_share));row.addWidget(self.share_off)
        self.share_copy=button('复制连接信息',lambda:handle(self,self.copy_share));row.addWidget(self.share_copy)
        row.addWidget(button('查看共享日志',lambda:handle(self,self.show_share_log)));row.addStretch();f.addLayout(row)
        self.share_status=QLabel();self.share_status.setWordWrap(True);self.share_status.setTextInteractionFlags(Qt.TextSelectableByMouse);f.addWidget(self.share_status)
        self.share_clients=QTableWidget(0,5);self.share_clients.setHorizontalHeaderLabels(['客户端','地址','已连接','语句数','事务']);center_table(self.share_clients);self.share_clients.setEditTriggers(QAbstractItemView.NoEditTriggers);self.share_clients.setSelectionBehavior(QAbstractItemView.SelectRows);self.share_clients.verticalHeader().setVisible(False);self.share_clients.horizontalHeader().setStretchLastSection(True);self.share_clients.setMaximumHeight(165);f.addWidget(self.share_clients)
        for widget in [self.share_port,self.share_password,self.share_public,self.share_on,self.share_off,self.share_copy]:
            widget.setEnabled(self.s.can('data.share'))
        return frame

    def paint_public_note(self,on):
        if self.share_gone or self.share_public_note is None:return
        try:
            self.share_public_note.setVisible(bool(on))
        except RuntimeError:
            self.share_gone=True

    def share_line(self,message):
        # Called from the share service's own threads: no Qt work in here.
        from datetime import datetime
        self.share_lines.append(datetime.now().strftime('%H:%M:%S')+'  '+str(message))
        if len(self.share_lines)>1000:del self.share_lines[:500]

    def restore_share(self):
        if self.share_gone:return
        settings=load_share()
        if not settings['enabled']:
            self.refresh_share();return
        if getattr(self.db,'shared',False):
            self.share_line('本机正在作为客户端使用 '+str(getattr(self.db,'host',''))+' 的数据，未开启本机共享。');self.refresh_share();return
        if settings['identity'] and settings['identity']!=db_identity(self.db):
            self.share_line('上次开启共享的是另一个数据库，已跳过自动开启。如需共享当前数据库，请重新点击「开启共享」。');self.refresh_share();return
        if not self.s.can('data.share'):
            self.share_line('当前账号没有开启局域网共享的权限。');self.refresh_share();return
        try:
            self.share_port.setValue(int(settings['port']));self.share_password.setText(settings['password']);self.share_public.setChecked(bool(settings['allow_remote']))
        except RuntimeError:
            self.share_gone=True;return
        self.start_share(quiet=True)

    def start_share(self,quiet=False):
        if getattr(self.db,'shared',False):raise BusinessError('本机正在使用其他电脑的数据，不能再作为主机共享；请先在本机配置本地数据库。')
        if not self.s.can('data.share'):raise BusinessError('无权执行：开启 / 关闭局域网数据共享')
        password=self.share_password.text()
        if len(password)<8:raise BusinessError('请先设置至少 8 位的访问口令')
        public=self.share_public.isChecked()
        if public and len(password)<12:raise BusinessError('公网访问会把口令暴露在整个互联网上，请设置至少 12 位的强口令（建议大小写字母 + 数字 + 符号）')
        port=int(self.share_port.value())
        if self.share is not None:self.share.stop()
        server=ShareServer(self.db,port=port,password=password,name=socket.gethostname(),allow_remote=public,log=self.share_line)
        try:server.start()
        except ShareError as exc:
            raise BusinessError(str(exc))
        self.share=server
        save_share({'enabled':True,'port':server.bound_port,'password':password,'allow_remote':public,'identity':db_identity(self.db)})
        try:self.s.audit('开启局域网共享','端口 '+str(server.bound_port)+'，' + ('仅局域网' if not public else '局域网 + 公网'))
        except Exception:logging.exception('Share audit failed')
        self.share_timer.start(5000);self.refresh_share()
        if public:threading.Thread(target=self.detect_public,daemon=True).start()
        if not quiet:
            addresses=server.addresses()
            scope='\n公网访问：已允许（公网出口探测中，见状态栏；需在路由器把端口映射到本机）' if public else ''
            message(self,'数据共享已开启。\n\n本机局域网地址：'+('、'.join(addresses) if addresses else '未检测到局域网地址，请检查网络')+'\n端口：'+str(server.bound_port)+scope+'\n\n如果其他电脑连不上，请在 Windows 防火墙中允许本程序，或放行该端口。')

    def detect_public(self):
        """Find the internet address of this network; safe to run in a thread."""
        try:
            address=public_address()
        except Exception:
            logging.exception('Public address lookup failed');address=''
        server=self.share
        if server is not None:
            server.note_public(address)
            self.share_line('公网出口地址：'+(address if address else '暂时无法获取（不影响局域网使用）'))

    def stop_share(self,quiet=False,remember=True):
        if self.share is not None:self.share.stop();self.share=None
        if self.share_timer.isActive():self.share_timer.stop()
        if remember:
            try:save_share({'enabled':False,'port':int(self.share_port.value()),'password':self.share_password.text(),'allow_remote':self.share_public.isChecked(),'identity':db_identity(self.db)})
            except Exception:logging.exception('Share settings save failed')
        self.refresh_share()
        if not quiet:message(self,'数据共享已关闭，其他电脑暂时无法访问本机数据。')

    def retire(self):
        """Stop background work before this window goes away or the store changes."""
        for name in ['timer','auto_backup_timer','share_timer']:
            ticker=getattr(self,name,None)
            if ticker is not None:
                try:ticker.stop()
                except RuntimeError:pass
        # Keep the saved "enabled" flag so the next launch shares again.
        self.stop_share(quiet=True,remember=False)

    def refresh_share(self):
        if self.share_gone:return
        running=self.share is not None and self.share.running
        clients=self.share.status()['clients'] if running else []
        addresses=self.share.addresses() if running else []
        try:
            self.paint_share(running,clients,addresses)
        except RuntimeError:
            # The window was closed and Qt released its widgets.
            self.share_gone=True

    def paint_share(self,running,clients,addresses):
        if self.share_clients is not None:
            self.share_clients.setRowCount(len(clients))
            for row,session in enumerate(clients):
                values=[session['client'],session['address']+':'+str(session['port']),clock_time(session['since']),session['statements'],'进行中' if session['in_transaction'] else '—']
                for column,value in enumerate(values):self.share_clients.setItem(row,column,QTableWidgetItem(str(value)))
        if self.share_status is not None:
            if running:
                public_mode=bool(getattr(self.share,'allow_remote',False))
                lines=[describe_share(self.share),'局域网地址：'+('、'.join(addresses) if addresses else '未检测到局域网地址')]
                if public_mode:
                    ip=getattr(self.share,'public_ip','') or ''
                    lines.append('公网出口：'+(ip if ip else '探测中…')+'（同事填「公网IP:'+str(self.share.bound_port)+'」连接；前提是路由器已把该端口映射到本机）')
                lines.append('可点击「复制连接信息」把地址、端口和口令发给同事。')
                self.share_status.setText('\n'.join(lines))
            elif getattr(self.db,'shared',False):
                self.share_status.setText('本机是客户端：数据保存在主机 '+str(getattr(self.db,'host',''))+' 上，本机不提供共享。')
            else:
                self.share_status.setText('共享未开启：目前只有本机能访问这些数据。开启后请保持本软件运行，并设置每日自动备份。')
        if self.share_badge is not None:
            if running:
                self.share_badge.setText('● 已共享（局域网+公网）' if getattr(self.share,'allow_remote',False) else '● 已共享给局域网')
            else:
                self.share_badge.setText('')

    def copy_share(self):
        if self.share is None or not self.share.running:raise BusinessError('共享未开启，请先点击「开启共享」')
        addresses=self.share.addresses()
        if not addresses:raise BusinessError('未检测到局域网地址，请确认本机已连接网络')
        text='锦纷资产管理系统 · 数据共享\n局域网地址：'+' 或 '.join(addresses)+'\n端口：'+str(self.share.bound_port)+'\n访问口令：'+self.share_password.text()
        if getattr(self.share,'allow_remote',False):
            ip=getattr(self.share,'public_ip','') or public_address()
            if ip:text+='\n公网地址：'+ip+'（需已在路由器把端口 '+str(self.share.bound_port)+' 映射到本机）'
        text+='\n\n在本机软件的「配置本地存储 / 数据库连接」中选择「局域网共享主机」并填入地址、端口与口令。'
        QApplication.clipboard().setText(text)
        message(self,'连接信息已复制到剪贴板，可直接粘贴给同事。')

    def show_share_log(self):
        self.plain_dialog('局域网共享运行日志','\n'.join(self.share_lines[-200:]) if self.share_lines else '暂无记录。')

    def make_data(self):
        widget=QWidget();v=QVBoxLayout(widget);v.setContentsMargins(0,0,0,0)
        v.addWidget(button('查看我的有效权限与数据范围',lambda:handle(self,self.my_access)))
        frame=QFrame();frame.setObjectName('card');f=QVBoxLayout(frame);f.setContentsMargins(25,24,25,24);f.setSpacing(18);head=QLabel('模板下载与文件上传导入');head.setStyleSheet('font-size:18px;font-weight:600;');f.addWidget(head)
        desc=QLabel('① 下载资产 / 员工 / 部门 / 供应商模板 → ② 填写资料 → ③ 选择上传文件 → ④ 预览全部校验结果 → ⑤ 确认导入。\n支持 Excel .xlsx、CSV UTF-8 和旧网页 JSON。可选择仅新增或按编号新增 / 更新；错误行可导出报告。\n更新保留资产领用状态；原表使用人信息保留为历史，新资产以闲置状态入库。');desc.setWordWrap(True);f.addWidget(desc)
        line=QHBoxLayout();line.addWidget(button('下载模板',lambda:handle(self,self.template)));b=button('上传文件并预览导入',lambda:handle(self,self.import_assets),True);b.setEnabled(self.s.can('data.import'));line.addWidget(b);line.addWidget(button('查看导入履历',lambda:handle(self,lambda:self.navigate('import_batches'))));b=button('导出完整资产台账',lambda:handle(self,self.export_all));b.setEnabled(self.s.can('data.export') and self.s.can('assets.read'));line.addWidget(b);line.addStretch();f.addLayout(line);v.addWidget(frame)
        frame=QFrame();frame.setObjectName('card');f=QVBoxLayout(frame);f.setContentsMargins(25,24,25,24);head=QLabel('完整备份与跨数据库迁移');head.setStyleSheet('font-size:18px;font-weight:600;');f.addWidget(head)
        note=QLabel('管理员可备份 / 恢复全部资产、人员、账号、工单、采购、盘点与审计。\n备份含密码哈希和公司资料，请存放在受保护的目录。\n恢复前自动保存当前库备份；恢复成功后重新登录。\n切换本地 / PostgreSQL：退出登录 → 配置连接 → 在目标库创建管理员 → 恢复备份。');note.setWordWrap(True);f.addWidget(note)
        line=QHBoxLayout();b=button('创建完整备份',lambda:handle(self,self.backup));b.setEnabled(self.s.can('data.backup') and self.s.profile()['scope']=='all');line.addWidget(b);b=button('恢复完整备份',lambda:handle(self,self.restore));b.setEnabled(self.s.can('data.restore') and self.s.can('data.backup') and self.s.profile()['scope']=='all');line.addWidget(b);line.addStretch();f.addLayout(line);v.addWidget(frame)
        v.addWidget(self.share_card())
        if self.admin:
            frame=QFrame();frame.setObjectName('card');f=QVBoxLayout(frame);f.setContentsMargins(25,24,25,24);path=mode_text(self.db);info=QLabel('当前数据存储：'+path+'\n配置、日志与本地自动备份目录：'+str(data_dir())+'\n管理员每次启动时检查当日自动备份。保留历史备份，不自动删除。\nPostgreSQL 模式请同时由数据库管理员配置服务器定期备份。' if not getattr(self.db,'shared',False) else '当前数据存储：'+path+'\n本机配置、日志与本地自动备份目录：'+str(data_dir())+'\n业务数据存放在主机电脑上；主机电脑负责每日自动备份，请与主机管理员确认备份情况。');info.setTextInteractionFlags(Qt.TextSelectableByMouse);info.setWordWrap(True);f.addWidget(info);f.addWidget(button('打开本地数据目录',lambda:os.startfile(str(data_dir()))));v.addWidget(frame)
        v.addStretch()
        # Narrow windows must scroll, never clip: cards keep a sane minimum width.
        widget.setMinimumWidth(760)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.NoFrame);scroll.setWidget(widget)
        return scroll

    def my_access(self):
        from access import PERMISSIONS
        self.s.require();c=self.s.profile();scope={'all':'全部数据','locations':'指定位置：'+'、'.join(c['locations']),'self':'本人领用资产（员工编号 '+c['employee_id']+'）'}[c['scope']]
        self.plain_dialog('我的有效权限','角色：'+'、'.join(c['roles'])+'\n数据范围：'+scope+'\n授权到期：'+(text_time(c['expires']) if c['expires'] else '长期有效')+'\n\n允许的操作：\n'+'\n'.join('✓ '+PERMISSIONS[p] for p in c['permissions']))

    def import_assets(self,kind='assets'):
        path,_=QFileDialog.getOpenFileName(self,'导入资产','','资产文件 (*.xlsx *.csv *.json)')
        if not path:return
        dialog=ImportWizard(self.s,path,self,kind)
        if dialog.exec()==QDialog.Accepted:
            p=dialog.import_result;message(self,f"导入完成：新增 {p['added']} 条，更新 {p['updated']} 条。");self.refresh()
    def template(self):
        TemplateDialog(self.s,self).exec()
    def export_all(self):
        path,_=QFileDialog.getSaveFileName(self,'导出全部资产','完整资产台账.xlsx','Excel (*.xlsx);;CSV (*.csv)')
        if path:self.s.export_assets(path);message(self,'全部资产和完整历史已导出。')
    def backup(self):
        path,_=QFileDialog.getSaveFileName(self,'保存完整备份','锦纷资产备份_'+date.today().isoformat()+'.json','JSON (*.json)')
        if path:self.s.backup(path);message(self,'完整备份已保存。')
    def auto_backup(self):
        if not self.admin:return
        folder=data_dir()/'backups';folder.mkdir(exist_ok=True)
        # Distinguish databases so switching connections does not suppress backups.
        import hashlib
        key=hashlib.sha256(db_identity(self.db).encode()).hexdigest()[:10];file=folder/(date.today().isoformat()+'_'+key+'.json')
        if not file.exists():self.s.backup(file);self.statusBar().showMessage('已创建今日自动备份：'+str(file))
    def restore(self):
        self.s.permit('data.restore');self.s.global_scope()
        path,_=QFileDialog.getOpenFileName(self,'选择完整备份','','JSON (*.json)')
        if not path:return
        data=self.s.validate_backup(json.loads(Path(path).read_text('utf-8')))
        if not question(self,f"恢复备份将覆盖当前数据库，包含 {len(data['tables']['assets'])} 条资产、{len(data['tables']['users'])} 个账号。\n恢复前自动备份当前数据。恢复后使用备份中的账号重新登录。"):return
        from datetime import datetime
        folder=data_dir()/'backups';folder.mkdir(exist_ok=True);self.s.backup(folder/('恢复前_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.json'));self.s.restore(path);message(self,'恢复完成，请使用备份中的账号重新登录。');self.logout_requested=True;self.close()

def configure_startup():
    try:
        config=load_config();return open_database(config)
    except Exception as e:
        logging.exception('Database startup failed');QMessageBox.warning(None,'数据库连接未成功',str(e)+'\n请检查连接配置。')
        dialog=ConnectionDialog(default_config())
        if dialog.exec()==QDialog.Accepted:return open_database(dialog.result_config)
        return None

def run():
    application=QApplication(sys.argv)
    if not QFontDatabase.families() and os.name=='nt':
        # The offscreen QA platform has no automatic Windows font discovery.
        for font in ['msyh.ttc','msyhbd.ttc','segoeui.ttf']:
            QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/font))
    application.setApplicationName('JinfenAssets');application.setApplicationDisplayName('锦纷资产管理系统');application.setOrganizationName('Jinfen');application.setStyle('Fusion');application.setStyleSheet(STYLE);application.setFont(QFont('Microsoft YaHei UI',10))
    reset_busy()
    from logging.handlers import RotatingFileHandler
    handler=RotatingFileHandler(data_dir()/'application.log',maxBytes=2*1024*1024,backupCount=3,encoding='utf-8');logging.basicConfig(handlers=[handler],level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    icon=Path(__file__).with_name('app.ico')
    if icon.exists():application.setWindowIcon(QIcon(str(icon)))
    application.setQuitOnLastWindowClosed(False)
    # Packaged smoke mode tests the actual EXE without touching real user data.
    if '--smoke-test' in sys.argv:
        with tempfile.TemporaryDirectory() as folder:
            from service import pw_hash,stamp
            db=Database({'engine':'sqlite','path':str(Path(folder)/'smoke.db')});s=Service(db)
            # The factory password is deliberately absent from the program, so the
            # packaged checks seed an ordinary administrator instead of using the
            # built-in account, then verify the built-in account's own guarantees.
            with db.transaction():
                s.ensure_builtin_owner()
                db.insert('users',{'id':'smoke-admin','username':'admin','display_name':'测试管理员','password':pw_hash('Testpass12345'),'role':'管理员','active':1,'failures':0,'locked_until':'','created_at':stamp()})
            s.login('admin','Testpass12345');s.save_asset({'id':'TEST-001','name':'打包验证设备','category':'电脑主机','quantity':1,'price_cents':100000,'life_months':36,'residual_bp':500});window=MainWindow(s);window.timer.stop();window.show();application.processEvents();window.grab().save(str(data_dir()/'smoke-dashboard.png'));window.navigate('assets');application.processEvents();window.grab().save(str(data_dir()/'smoke-assets.png'))
            for key in MODULES:
                window.navigate(key);application.processEvents()
            window.navigate('data');application.processEvents();window.grab().save(str(data_dir()/'smoke-data.png'))
            login=LoginWindow(db);login.show();application.processEvents();login.grab().save(str(data_dir()/'smoke-login.png'))
            assert isinstance(login.inputs['login']['password'],PasswordEdit) and isinstance(login.inputs['register']['confirm'],PasswordEdit)
            login.close()
            # Confirm the packaged PostgreSQL binary driver and QR encoder work.
            import psycopg, qrcode
            version=psycopg.pq.version();qrcode.make('jinfen:TEST-001').save(data_dir()/'smoke-qr.png')
            template=Path(folder)/'asset-template.csv';s.template(template);records=s.read_import(template);plan=s.plan_import(records,'assets','仅新增');s.apply_import(records,'assets','仅新增',template,plan)
            attachment=Path(folder)/'warranty.txt';attachment.write_text('Packaged attachment test','utf-8');s.upload_attachments('TEST-001',[attachment]);s.save_task({'title':'Packaged task test','priority':'普通'})
            about=AboutDialog(VERSION);about.show();application.processEvents();about.grab().save(str(data_dir()/'smoke-about.png'));about.close()
            window.navigate('analytics')
            from analytics import CHARTS
            for key in CHARTS:
                window.analytics_page.chart_selector.setCurrentIndex(window.analytics_page.chart_selector.findData(key));application.processEvents()
            window.analytics_page.chart_selector.setCurrentIndex(0);application.processEvents();window.grab().save(str(data_dir()/'smoke-analytics.png'));window.analytics_page.canvas.save_png(str(data_dir()/'smoke-chart.png'))
            from access import PERMISSIONS
            # The factory account owns administration: self-registration is read-only,
            # and a granted administrator cannot touch the built-in account.  Its
            # password is deliberately absent from the program, so the check stands a
            # session on the built-in row directly instead of logging in with it.
            builtin_row=db.one('SELECT * FROM users WHERE username=?',(BUILTIN_USERNAME,))
            builtin=Service(db);builtin.user_id=builtin_row['id'];builtin.session_access=builtin.access_signature(builtin_row);builtin.session_password=builtin_row['password']
            epoch=db.one("SELECT value FROM meta WHERE key='auth_epoch'");builtin.session_epoch=epoch['value'] if epoch else ''
            owner_row=db.one("SELECT * FROM users WHERE username='admin'")
            assert s.owner_id()==str(builtin_row['id']) and s.is_owner(builtin_row) and not s.is_owner(owner_row)
            assert builtin_row['role']=='管理员' and builtin_row['active']==1
            s.register('limited','打包权限验证','Testpass12345');limited=db.one("SELECT * FROM users WHERE username='limited'")
            assert limited['role']=='只读用户' and limited['active']==1 and str(limited['id'])!=s.owner_id()
            owner_check=True
            try:s.manage_user(limited['id'],'管理员',True);owner_check=False
            except BusinessError:pass
            builtin.manage_user(limited['id'],'管理员',True)
            granted=Service(db);granted.login('limited','Testpass12345')
            try:granted.manage_user(builtin_row['id'],'只读用户',True);owner_check=False
            except BusinessError:pass
            try:granted.manage_user(owner_row['id'],'只读用户',True);owner_check=False
            except BusinessError:pass
            builtin.manage_user(limited['id'],'只读用户',True)
            s.save_role('打包验证只读',['assets.read','analytics.read'])
            target=s.check_asset('TEST-001')
            s.catalog('employees',{'id':'EXE-E1','name':'权限验证员工','active':1});s.flow('TEST-001',target['revision'],'领用','EXE-E1')
            s.assign_access(limited['id'],{'roles':['打包验证只读'],'allow':[],'deny':['data.export'],'scope':'self','locations':[],'employee_id':'EXE-E1','expires':''})
            viewer=Service(db);viewer.login('limited','Testpass12345');assert [a['id'] for a in viewer.list('assets')]==['TEST-001']
            denied=False
            try:viewer.export_assets(Path(folder)/'forbidden.csv')
            except BusinessError:denied=True
            assert denied and not (Path(folder)/'forbidden.csv').exists()
            roles=RolesDialog(s);roles.show();application.processEvents();roles.grab().save(str(data_dir()/'smoke-roles.png'));roles.close()
            access=UserAccessDialog(s,limited);access.show();application.processEvents();access.grab().save(str(data_dir()/'smoke-access.png'));access.close()
            upload_images(s,'TEST-001',[data_dir()/'smoke-qr.png']);window.navigate('assets');application.processEvents();window.grab().save(str(data_dir()/'smoke-assets.png'));assert not window.photo_icons['TEST-001'].isNull()
            # Every column in every table - header and data - renders centred.
            assert isinstance(window.table.itemDelegate(),CenterDelegate) and window.table.rowCount()>0
            option=QStyleOptionViewItem();window.table.itemDelegate().initStyleOption(option,window.table.model().index(0,0))
            assert option.displayAlignment==Qt.AlignCenter and window.table.horizontalHeaderItem(0).textAlignment()==Qt.AlignCenter
            detail=AssetDetailDialog(s,'TEST-001');detail.show();application.processEvents();assert detail.photos and not detail.preview.pixmap().isNull();detail.grab().save(str(data_dir()/'smoke-photo-detail.png'));detail.close()
            # A modal form opened from a toolbar action must keep a normal pointer.
            pointers={}
            def probe_exec(dialog):
                dialog.setModal(True);dialog.show();application.processEvents();sync_busy();pointers['open']=QApplication.overrideCursor();dialog.close();application.processEvents();sync_busy();pointers['closed']=QApplication.overrideCursor();return QDialog.Rejected
            builtin_exec=FormDialog.exec;FormDialog.exec=probe_exec
            handle(window,window.asset_edit);FormDialog.exec=builtin_exec
            assert pointers['open'] is None and pointers['closed'] is not None and QApplication.overrideCursor() is None
            # Deletion works while nothing references the record and is refused otherwise.
            s.save_asset({'id':'TMP-001','name':'待删除设备','category':'电脑'});s.delete_asset('TMP-001')
            assert not [a for a in s.list('assets') if a['id']=='TMP-001']
            try:
                s.delete_asset('TEST-001');raise AssertionError('被引用的资产不应允许删除')
            except BusinessError:pass
            assert s.list('assets') and [e for e in s.list('audit') if e['action']=='删除资产']
            # A real LAN share: a second client talks to this process over a socket.
            share=ShareServer(db,port=0,password='SmokeShare1234',name='打包主机',log=lambda m:None);share.start()
            client=RemoteDatabase({'engine':'shared','host':'127.0.0.1','port':share.bound_port,'password':'SmokeShare1234'})
            assert test_connection({'host':'127.0.0.1','port':share.bound_port,'password':'SmokeShare1234'})=='打包主机'
            remote=Service(client);remote.login('admin','Testpass12345')
            before=[a['id'] for a in s.list('assets')]
            remote.save_asset({'id':'SHARE-001','name':'共享写入设备','category':'电脑主机','quantity':1,'price_cents':100,'life_months':12,'residual_bp':0})
            assert {a['id'] for a in remote.list('assets')}==set(before)|{'SHARE-001'}
            assert db.one("SELECT name FROM assets WHERE id='SHARE-001'")['name']=='共享写入设备'
            share_denied=False
            try:client.execute('DROP TABLE assets')
            except BusinessError:share_denied=True
            assert share_denied and db.one("SELECT COUNT(*) AS n FROM assets")['n']==len(before)+1
            assert share.status()['clients'][0]['statements']>0
            client.close();share.stop();assert not share.running
            marker={'ok':True,'version':VERSION,'assets':len(s.list('assets')),'libpq_version':version,'pages':len(MODULES)+3,'templates':True,'attachments':len(s.attachments('TEST-001')),'tasks':len(s.list('tasks')),'charts':len(CHARTS),'permissions':len(PERMISSIONS),'scope_isolation':True,'export_denied':denied,'access_dialogs':True,'asset_photos':True,'photo_detail':True,'cursor_guard':True,'crud_delete':True,'password_eye':True,'table_center':True,'lan_share':True,'share_screen':share_denied,'owner_account':owner_check};(data_dir()/'smoke-result.json').write_text(json.dumps(marker),'utf-8');window.close();db.close()
        return 0
    if '--tray-smoke-test' in sys.argv:
        with tempfile.TemporaryDirectory() as folder:
            db=Database({'engine':'sqlite','path':str(Path(folder)/'tray.db')});controller=DesktopController(application,db,LoginWindow,MainWindow);controller.start(True)
            def check_tray():
                marker={'ok':True,'version':VERSION,'brand':application.applicationDisplayName(),'tray_available':controller.available(),'tray_visible':controller.tray.isVisible(),'startup_login_visible':controller.current.isVisible(),'autologin':bool(controller.current.service.user_id)}
                controller.show_current();marker['restore_visible']=controller.current.isVisible()
                if controller.available():controller.hide_to_tray();marker['hidden_after_minimize']=not controller.current.isVisible()
                (data_dir()/'tray-smoke-result.json').write_text(json.dumps(marker,ensure_ascii=False),'utf-8');controller.quit()
            QTimer.singleShot(700,check_tray);code=application.exec();controller.tray.hide();db.close();return code
    controller=None
    guard=SingleInstance(lambda:controller.show_current() if controller else None,application)
    if not guard.acquire('--startup' in sys.argv):guard.close();return 0
    try:
        db=configure_startup()
        if not db:return 1
        controller=DesktopController(application,db,LoginWindow,MainWindow);controller.start('--startup' in sys.argv)
        code=application.exec();controller.tray.hide();controller.db.close();return code
    finally:guard.close()

if __name__=='__main__':
    try:
        sys.exit(run())
    except Exception as e:
        logging.exception('Fatal application error')
        # The packaged self-check runs unattended: a modal box there would wait
        # for a click that never comes, so report to the console and fail instead.
        if '--smoke-test' in sys.argv:
            import traceback
            traceback.print_exc()
        elif QApplication.instance():
            QMessageBox.critical(None,'启动异常',str(e))
        sys.exit(1)


