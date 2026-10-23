"""Role matrix and individual grants, with readable effective-access preview."""
from datetime import datetime,timezone
from PySide6.QtCore import Qt,QDateTime
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,QComboBox,QLineEdit,QTextEdit,QListWidget,QListWidgetItem,QTableWidget,QTableWidgetItem,QCheckBox,QDateTimeEdit,QMessageBox,QAbstractItemView)
from access import PERMISSIONS
from table_style import center_table,center_header,cell_box

def invoke(parent,fn):
    try:fn()
    except Exception as e:QMessageBox.warning(parent,'权限设置',str(e))

def action(layout,text,fn):
    b=QPushButton(text);b.clicked.connect(lambda:invoke(b.window(),fn));layout.addWidget(b);return b

class RolesDialog(QDialog):
    def __init__(self,s,parent=None):
        super().__init__(parent);self.s=s;s.permit('security.manage');self.setWindowTitle('角色管理 · 功能权限矩阵');self.resize(830,770)
        root=QVBoxLayout(self);note=QLabel('多个角色的权限合并生效，账号“拒绝”优先。管理员始终拥有全部权限。\n角色调整后，相关在线账号在下一次操作时需重新登录。');note.setWordWrap(True);root.addWidget(note)
        line=QHBoxLayout();self.roles=QComboBox();line.addWidget(self.roles,1);action(line,'新建 / 复制角色',self.new);action(line,'删除角色',self.delete);root.addLayout(line)
        f=QFormLayout();self.name=QLineEdit();self.description=QLineEdit();f.addRow('角色名称',self.name);f.addRow('角色说明',self.description);root.addLayout(f)
        self.table=QTableWidget(len(PERMISSIONS),2);self.table.setHorizontalHeaderLabels(['功能 / 操作','允许']);center_table(self.table);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.verticalHeader().hide();self.table.verticalHeader().setDefaultSectionSize(44);self.checks={}
        for i,(key,title) in enumerate(PERMISSIONS.items()):
            item=QTableWidgetItem(title+'  ['+key+']');self.table.setItem(i,0,item);box=QCheckBox();self.table.setCellWidget(i,1,cell_box(box));self.checks[key]=box
        self.table.horizontalHeader().setStretchLastSection(True);self.table.setColumnWidth(0,550);root.addWidget(self.table,1)
        line=QHBoxLayout();action(line,'全选',lambda:self.select(True));action(line,'清空',lambda:self.select(False));line.addStretch();action(line,'保存角色',self.save);action(line,'关闭',self.accept);root.addLayout(line)
        self.roles.currentTextChanged.connect(self.load);self.reload()
    def reload(self,selected=''):
        self.roles.blockSignals(True);self.roles.clear();self.roles.addItems(sorted(self.s.policy()['roles']));self.roles.setCurrentText(selected);self.roles.blockSignals(False);self.load(self.roles.currentText())
    def load(self,name):
        r=self.s.policy()['roles'].get(name,{'permissions':[],'description':''});self.name.setText(name);self.description.setText(r['description']);self.name.setReadOnly(True)
        for k,b in self.checks.items():b.setChecked(k in r['permissions'])
    def select(self,checked):
        for b in self.checks.values():b.setChecked(checked)
    def new(self):self.name.setReadOnly(False);self.name.setText('');self.name.setFocus();self.description.setText('自定义角色')
    def save(self):
        name=self.name.text().strip()
        self.s.save_role(name,[k for k,b in self.checks.items() if b.isChecked()],self.description.text());self.reload(name);QMessageBox.information(self,'保存完成','角色权限已保存。')
    def delete(self):
        name=self.roles.currentText()
        if QMessageBox.question(self,'删除角色','删除角色“'+name+'”？')!=QMessageBox.Yes:return
        self.s.delete_role(name);self.reload()

class UserAccessDialog(QDialog):
    def __init__(self,s,user,parent=None):
        super().__init__(parent);s.permit('security.manage');self.s=s;self.user=user;self.setWindowTitle('高级授权 · '+user['username']);self.resize(950,830)
        root=QVBoxLayout(self);root.addWidget(QLabel('账号：'+user['display_name']+'（'+user['username']+'） · 基础角色：'+user['role']))
        c=s.profile(user);self.explicit=user['id'] in s.policy()['users']
        root.addWidget(QLabel('选择多个角色叠加权限，再按账号单独允许或拒绝。拒绝始终优先。'))
        self.roles=QListWidget();self.roles.setMaximumHeight(110)
        for role in sorted(s.policy()['roles']):
            item=QListWidgetItem(role);item.setFlags(item.flags()|Qt.ItemIsUserCheckable);item.setCheckState(Qt.Checked if role in c['roles'] else Qt.Unchecked);self.roles.addItem(item)
        root.addWidget(self.roles)
        f=QFormLayout();self.scope=QComboBox();self.scope.addItem('全部数据','all');self.scope.addItem('指定存放位置','locations');self.scope.addItem('本人领用资产','self');self.scope.setCurrentIndex(max(0,self.scope.findData(c['scope'])));f.addRow('数据范围',self.scope)
        self.locations=QLineEdit(';'.join(c['locations']));self.locations.setPlaceholderText('位置必须与台账一致，多项用英文分号分隔');f.addRow('授权位置',self.locations)
        self.employee=QComboBox();self.employee.addItem('请选择关联员工','')
        for e in s.db.rows('employees'):self.employee.addItem(e['name']+' · '+e['id'],e['id'])
        self.employee.setCurrentIndex(max(0,self.employee.findData(c['employee_id'])));f.addRow('本人关联员工',self.employee)
        self.expiring=QCheckBox('设置授权有效期（到期后禁止继续使用）');self.expiring.setChecked(bool(c['expires']));self.expiry=QDateTimeEdit(QDateTime.currentDateTime().addDays(30));self.expiry.setDisplayFormat('yyyy-MM-dd HH:mm');self.expiry.setCalendarPopup(True)
        if c['expires']:self.expiry.setDateTime(QDateTime.fromString(c['expires'],Qt.ISODate).toLocalTime())
        row=QHBoxLayout();row.addWidget(self.expiring);row.addWidget(self.expiry);f.addRow('授权有效期',row);root.addLayout(f)
        self.table=QTableWidget(len(PERMISSIONS),3);self.table.setHorizontalHeaderLabels(['功能 / 操作','账号覆盖','最终结果']);center_table(self.table);self.table.verticalHeader().hide();self.table.verticalHeader().setDefaultSectionSize(46);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.overrides={}
        for i,(key,title) in enumerate(PERMISSIONS.items()):
            self.table.setItem(i,0,QTableWidgetItem(title));combo=QComboBox();combo.addItems(['继承角色','单独允许','明确拒绝']);combo.setCurrentIndex(2 if key in c['deny'] else 1 if key in c['allow'] else 0);self.table.setCellWidget(i,1,cell_box(combo,150));self.overrides[key]=combo;combo.currentIndexChanged.connect(self.preview)
        self.table.setColumnWidth(0,370);self.table.setColumnWidth(1,220);self.table.horizontalHeader().setStretchLastSection(True);root.addWidget(self.table,1)
        self.summary=QLabel();self.summary.setWordWrap(True);root.addWidget(self.summary)
        line=QHBoxLayout();action(line,'恢复基础角色默认授权',self.reset);line.addStretch();self.save_button=action(line,'保存高级授权',self.save);action(line,'关闭',self.accept);root.addLayout(line)
        self.roles.itemChanged.connect(self.preview);self.scope.currentIndexChanged.connect(self.preview);self.expiring.toggled.connect(self.preview)
        if user['role']=='管理员':self.save_button.setEnabled(False);root.addWidget(QLabel('管理员拥有固定完整权限。如需限制，请先调整账号基础角色。'))
        self.preview()
    def config(self):
        return {'roles':[self.roles.item(i).text() for i in range(self.roles.count()) if self.roles.item(i).checkState()==Qt.Checked],
         'allow':[k for k,b in self.overrides.items() if b.currentIndex()==1],'deny':[k for k,b in self.overrides.items() if b.currentIndex()==2],
         'scope':self.scope.currentData(),'locations':list(dict.fromkeys(v.strip() for v in self.locations.text().replace('；',';').split(';') if v.strip())),
         'employee_id':self.employee.currentData(),'expires':self.expiry.dateTime().toPython().astimezone(timezone.utc).isoformat(timespec='seconds') if self.expiring.isChecked() else ''}
    def preview(self,*args):
        c=self.config();p=self.s.policy();granted=set(c['allow'])
        for r in c['roles']:granted.update(p['roles'][r]['permissions'])
        granted-=set(c['deny'])
        for i,k in enumerate(PERMISSIONS):self.table.setItem(i,2,QTableWidgetItem('允许' if k in granted else '拒绝'))
        self.locations.setEnabled(c['scope']=='locations');self.employee.setEnabled(c['scope']=='self');self.expiry.setEnabled(self.expiring.isChecked())
        self.summary.setText('最终允许 '+str(len(granted))+' / '+str(len(PERMISSIONS))+' 项操作。范围同时约束列表、图表、导出、附件和操作。\n限定范围的基础资料仅可查看；完整备份和恢复仅允许全部数据范围。')
    def save(self):self.s.assign_access(self.user['id'],self.config());QMessageBox.information(self,'保存完成','账号高级授权已保存，相关在线账号需重新登录。');self.accept()
    def reset(self):
        if QMessageBox.question(self,'恢复默认','清除账号高级授权、有效期和数据范围，恢复基础角色权限？')!=QMessageBox.Yes:return
        self.s.reset_access(self.user['id']);self.accept()
