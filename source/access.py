"""Database-backed RBAC. Deny overrides grants; checks run on every operation."""
import json, hashlib, contextlib
from datetime import datetime, timezone

PERMISSIONS = {
 'assets.read':'查看资产与流转历史','assets.create':'新增资产','assets.edit':'编辑资产',
 'assets.issue':'领用 / 归还','assets.transfer':'调拨 / 移动位置','assets.repair':'维修办理','assets.scrap':'资产报废',
 'assets.delete':'删除资产（仅未被引用的台账）',
 'employees.read':'查看员工','employees.write':'维护员工','employees.delete':'删除员工',
 'departments.read':'查看部门','departments.write':'维护部门','departments.delete':'删除部门',
 'suppliers.read':'查看供应商','suppliers.write':'维护供应商','suppliers.delete':'删除供应商',
 'purchases.read':'查看采购','purchases.create':'申请采购','purchases.edit':'修改待审批申请','purchases.delete':'删除待审批 / 已拒绝申请',
 'purchases.approve':'审批采购','purchases.receive':'采购入库',
 'repairs.write':'编辑维修工单','repairs.delete':'删除已结束的维修工单',
 'counts.read':'查看盘点','counts.write':'建立 / 登记 / 结束盘点','counts.delete':'删除盘点（含明细）',
 'tasks.read':'查看待办','tasks.write':'维护待办','tasks.delete':'删除待办',
 'users.delete':'删除账号',
 'audit.delete':'删除留痕记录（流转记录 / 操作审计 / 导入履历）',
 'attachments.read':'查看 / 下载附件','attachments.write':'上传 / 移除 / 重命名附件',
 'analytics.read':'总览 / 分析 / 折旧报表','data.import':'文件导入','data.export':'Excel / CSV / 图片导出',
 'data.print':'打印 / PDF 输出','data.backup':'完整数据库备份','data.restore':'完整数据库恢复','audit.read':'查看操作审计',
 'data.share':'开启 / 关闭局域网数据共享',
}
READ = {p for p in PERMISSIONS if p.endswith('.read')} - {'audit.read'}
# Destructive removals stay with the administrator (who always holds every
# permission) unless a role is explicitly granted them in the permission matrix.
REMOVAL = {p for p in PERMISSIONS if p.endswith('.delete')}
# Changing where the company's data lives is an administrator decision too.
ADMIN_ONLY = {'assets.scrap','purchases.approve','data.backup','data.restore','audit.read','data.share'}
BUILTINS = {
 '操作员': sorted(set(PERMISSIONS)-ADMIN_ONLY-REMOVAL),
 '只读用户': sorted(READ|{'data.export','data.print'}),
 '采购审批员': ['purchases.read','purchases.approve'],
 '资产维护员': ['assets.read','assets.create','assets.edit','assets.issue','assets.transfer','assets.repair','employees.read','departments.read','suppliers.read','attachments.read','attachments.write'],
 '审计员': sorted(READ|{'audit.read','data.export'}),
}
TABLE_PERMISSION = {'events':'assets.read','repairs':'assets.read','count_items':'counts.read','import_batches':'data.import','users':'security.manage'}

# The built-in administrator: the only account allowed to create or promote
# administrators.  Its credentials are fixed when the program is built, so every
# copy of the program - and therefore every database it creates - carries the
# very same account.  Only the PBKDF2 hash ships inside the program; the password
# itself is kept by the owner and can never be changed from inside the program.
OWNER_KEY = 'owner_user_id'
BUILTIN_USERNAME = 'owner'
BUILTIN_DISPLAY = '内置管理员'
BUILTIN_PASSWORD_HASH = 'pbkdf2_sha256$310000$fcf05c8c33d3d3b334de4de3a7a91e20$f6cf1e7f56f1558c02dae6721be9abc995fc2f71b1e8f5ab5b76c9ed86b201d3'
BUILTIN_LABEL = '★ 内置管理员（出厂账号）'

def defaults():return {'version':1,'roles':{n:{'permissions':p,'description':'预设角色，可调整权限'} for n,p in BUILTINS.items()},'users':{}}

def error(message):
    from service import BusinessError
    return BusinessError(message)

def validate_policy(policy, users=None):
    if not isinstance(policy,dict) or set(policy)!={'version','roles','users'} or policy['version']!=1 or not isinstance(policy['roles'],dict) or not isinstance(policy['users'],dict):raise error('权限配置格式无效')
    if len(policy['roles'])>100 or len(policy['users'])>200000:raise error('权限配置过大')
    for name,role in policy['roles'].items():
        if not isinstance(name,str) or not name.strip() or len(name)>40 or name=='管理员' or not isinstance(role,dict) or set(role)!={'permissions','description'}:raise error('角色配置无效')
        if not isinstance(role['description'],str) or len(role['description'])>500:raise error('角色说明无效')
        if not isinstance(role['permissions'],list) or len(role['permissions'])>len(PERMISSIONS) or any(p not in PERMISSIONS for p in role['permissions']):raise error('角色权限无效')
    if not {'操作员','只读用户'}<=set(policy['roles']):raise error('必须保留操作员和只读用户角色')
    for uid,c in policy['users'].items():
        if not isinstance(uid,str) or (users is not None and uid not in users) or not isinstance(c,dict) or set(c)!={'roles','allow','deny','scope','locations','employee_id','expires'}:raise error('账号授权配置无效')
        for field,options in [('roles',policy['roles']),('allow',PERMISSIONS),('deny',PERMISSIONS)]:
            if not isinstance(c[field],list) or len(c[field])>100 or any(v not in options for v in c[field]):raise error('账号权限或角色不存在')
        if c['scope'] not in ['all','locations','self'] or not isinstance(c['locations'],list) or len(c['locations'])>200 or any(not isinstance(v,str) or not v or len(v)>300 for v in c['locations']):raise error('数据范围无效')
        if c['scope']=='locations' and not c['locations']:raise error('位置范围至少填写一项')
        if not isinstance(c['employee_id'],str) or len(c['employee_id'])>100 or (c['scope']=='self' and not c['employee_id']):raise error('本人范围必须关联员工')
        if not isinstance(c['expires'],str):raise error('到期时间无效')
        if c['expires']:
            try:
                d=datetime.fromisoformat(c['expires'])
                if d.tzinfo is None:raise ValueError()
            except ValueError:raise error('到期时间必须包含时区')
    return policy

class AccessMixin:
    # ---------------------------------------------------------- 内置管理员账号

    def owner_id(self):
        row=self.db.one("SELECT value FROM meta WHERE key='owner_user_id'")
        return str(row['value']) if row and row['value'] else ''

    def _mark_owner(self,uid):
        self.db.execute("INSERT INTO meta (key,value) VALUES ('owner_user_id',?) ON CONFLICT (key) DO UPDATE SET value=excluded.value",(str(uid),))

    def is_owner(self,user=None):
        """True for the built-in administrator, which alone may grant 管理员."""
        user=user or self.db.one('SELECT * FROM users WHERE id=?',(self.user_id,))
        if not user:return False
        marked=self.owner_id()
        if marked:return str(user['id'])==marked
        return user['username']==BUILTIN_USERNAME and user['role']=='管理员'

    def ensure_builtin_owner(self):
        """Guarantee the factory built-in administrator exists and is intact.

        A fresh database gets the account automatically, and a database whose
        copy was tampered with (never mind how: disabled, downgraded, given a
        different password or locked out by repeated wrong guesses) is repaired
        on the next login, so the same credentials always work on every copy of
        the program - and the owner can never be shut out of their own data.
        """
        from service import ident,stamp
        row=self.db.one('SELECT * FROM users WHERE username=?',(BUILTIN_USERNAME,))
        if not row:
            uid=ident()
            self.db.insert('users',{'id':uid,'username':BUILTIN_USERNAME,'display_name':BUILTIN_DISPLAY,'password':BUILTIN_PASSWORD_HASH,'role':'管理员','active':1,'failures':0,'locked_until':'','created_at':stamp()})
            self._mark_owner(uid)
            self.audit('创建内置管理员','内置管理员账号已在此数据库生成（出厂账号）')
            return uid
        repaired=[]
        if row['password']!=BUILTIN_PASSWORD_HASH:repaired.append('口令')
        if row['role']!='管理员':repaired.append('角色')
        if not row['active']:repaired.append('启用状态')
        if row['failures'] or row['locked_until']:repaired.append('试错锁定')
        if repaired:
            self.db.update('users',row['id'],{'password':BUILTIN_PASSWORD_HASH,'role':'管理员','active':1,'failures':0,'locked_until':''})
            self.audit('恢复内置管理员','内置管理员账号的'+'、'.join(repaired)+'被改动，已恢复为出厂设置')
        if self.owner_id()!=str(row['id']):self._mark_owner(row['id'])
        return str(row['id'])

    def policy(self):
        row=self.db.one("SELECT value FROM meta WHERE key='access_policy'")
        if not row:return defaults()
        try:return validate_policy(json.loads(row['value']))
        except (ValueError,TypeError,KeyError):raise error('权限配置损坏，请由管理员恢复有效备份')

    def profile(self,user=None):
        user=user or self.db.one('SELECT * FROM users WHERE id=?',(self.user_id,))
        if not user:raise error('请先登录')
        if user['role']=='管理员':return {'permissions':sorted(PERMISSIONS),'roles':['管理员'],'scope':'all','locations':[],'employee_id':'','expires':'','allow':[],'deny':[]}
        p=self.policy();c=p['users'].get(user['id'],{'roles':[user['role']],'allow':[],'deny':[],'scope':'all','locations':[],'employee_id':'','expires':''})
        granted=set(c['allow'])
        for role in c['roles']:granted.update(p['roles'].get(role,{}).get('permissions',[]))
        return dict(c,permissions=sorted(granted-set(c['deny'])))

    def access_signature(self,user=None):
        return hashlib.sha256(json.dumps(self.profile(user),sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def can(self,permission):
        try:
            u=self.require()
            return (u['role']=='管理员') if permission=='security.manage' else permission in self.profile(u)['permissions']
        except ValueError:return False

    def permit(self,permission):
        u=self.require()
        if (permission=='security.manage' and u['role']=='管理员') or permission in self.profile(u)['permissions']:return u
        # Denial is committed outside the failed business transaction.
        if not self.db.inside:self.audit('权限拒绝',PERMISSIONS.get(permission,permission))
        raise error('无权执行：'+PERMISSIONS.get(permission,permission))

    def save_role(self,name,permissions,description=''):
        from service import clean
        with self.db.transaction():
            self.permit('security.manage');p=self.policy();name=clean(name,40)
            p['roles'][name]={'permissions':list(dict.fromkeys(permissions)),'description':clean(description,500)}
            self._write_policy(p);self.audit('角色授权调整',name+' / '+','.join(permissions))

    def delete_role(self,name):
        with self.db.transaction():
            self.permit('security.manage');p=self.policy()
            if name in ['操作员','只读用户']:raise error('基础角色不能删除')
            if name not in p['roles']:raise error('角色不存在')
            if any(name in c['roles'] for c in p['users'].values()):raise error('角色仍被账号使用，请先移除分配')
            del p['roles'][name];self._write_policy(p);self.audit('删除角色',name)

    def _write_policy(self,p):
        validate_policy(p)
        self.db.execute("INSERT INTO meta (key,value) VALUES ('access_policy',?) ON CONFLICT (key) DO UPDATE SET value=excluded.value",(json.dumps(p,ensure_ascii=False),))

    def assign_access(self,uid,config):
        with self.db.transaction():
            self.permit('security.manage');u=self.db.one('SELECT * FROM users WHERE id=?',(uid,))
            if not u:raise error('账号不存在')
            if u['role']=='管理员':raise error('总管理员不配置局部权限；请先在账号管理中改为普通账号')
            if config.get('employee_id') and not self.db.one('SELECT id FROM employees WHERE id=?',(config['employee_id'],)):raise error('关联员工不存在')
            p=self.policy();p['users'][uid]=config;self._write_policy(p);self.audit('账号高级授权',u['username']+' / '+json.dumps(config,ensure_ascii=False))

    def reset_access(self,uid):
        with self.db.transaction():
            self.permit('security.manage');p=self.policy();p['users'].pop(uid,None);self._write_policy(p);self.audit('恢复基础角色授权',uid)

    def asset_visible(self,a,profile=None):
        c=profile or self.profile()
        return c['scope']=='all' or (c['scope']=='locations' and a['location'] in c['locations']) or (c['scope']=='self' and a['employee_id']==c['employee_id'])

    def check_asset(self,aid):
        self.require();a=self.db.one('SELECT * FROM assets WHERE id=?',(aid,))
        if not a or not self.asset_visible(a):raise error('资产不存在或不在授权的数据范围内')
        return a

    def scoped_rows(self,table):
        if table=='meta':raise error('禁止读取内部设置')
        self.require();permission=TABLE_PERMISSION.get(table,table+'.read')
        if not self.can(permission):return []
        rows=self.db.rows(table);c=self.profile()
        if table=='users':return [{k:v for k,v in u.items() if k!='password'} for u in rows]
        if c['scope']=='all':return rows
        assets={a['id'] for a in self.db.rows('assets') if self.asset_visible(a,c)}
        if table=='assets':return [a for a in rows if a['id'] in assets]
        if table in ['events','repairs','attachments','count_items']:return [r for r in rows if r['asset_id'] in assets]
        if table in ['tasks','purchases','import_batches']:return [r for r in rows if r.get('creator_id',r.get('requester_id',r.get('actor_id')))==self.user_id or (r.get('asset_id') in assets)]
        if table=='counts':
            allowed={i['count_id'] for i in self.db.rows('count_items') if i['asset_id'] in assets}
            return [r for r in rows if r['creator_id']==self.user_id or r['id'] in allowed]
        if table=='employees':
            if c['scope']=='self':return [e for e in rows if e['id']==c['employee_id']]
            employees={a['employee_id'] for a in self.db.rows('assets') if a['id'] in assets}
            return [e for e in rows if e['id'] in employees]
        if table=='departments':
            ids={e['department_id'] for e in self.scoped_rows('employees')};return [d for d in rows if d['id'] in ids]
        if table=='suppliers':
            ids={a['supplier_id'] for a in self.db.rows('assets') if a['id'] in assets};return [d for d in rows if d['id'] in ids]
        # Audit contains cross-company details; restricted accounts see their own actions.
        if table=='audit':return [r for r in rows if r['actor_id']==self.user_id]
        return []

    def check_row(self,table,uid):
        if not any(r['id']==uid for r in self.scoped_rows(table)):raise error('记录不存在或不在授权范围内')

    def global_scope(self):
        if self.profile()['scope']!='all':raise error('此操作涉及全局资料，仅允许全部数据范围的账号执行')

    def page_allowed(self,key):
        if key in ['dashboard','analytics','reports']:return self.can('analytics.read') and self.can('assets.read')
        if key=='data':return True
        return self.can(TABLE_PERMISSION.get(key,key+'.read'))
