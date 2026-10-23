"""测试公共辅助。

注册账号现在一律是只读用户（管理员只能由内置管理员账号授予），而内置账号的
明文口令刻意只由发行方保管、不写进程序，所以需要管理员的测试改为直接播种一
行管理员记录，再用它登录。生产代码不提供这条路径，它仅存在于测试夹具中。
"""
from access import BUILTIN_USERNAME
from service import pw_hash, stamp

ADMIN_PASSWORD = 'Password12345'


def seed_admin(db, username='admin', password=ADMIN_PASSWORD, display='管理员'):
    """Insert an enabled administrator row and return its id."""
    uid = 'seed-' + username
    if db.one('SELECT id FROM users WHERE username=?', (username,)):
        return db.one('SELECT id FROM users WHERE username=?', (username,))['id']
    db.insert('users', {'id': uid, 'username': username, 'display_name': display,
                        'password': pw_hash(password), 'role': '管理员', 'active': 1,
                        'failures': 0, 'locked_until': '', 'created_at': stamp()})
    return uid


def as_builtin(session):
    """Make *session* act as the factory administrator, without its password.

    Tests cannot know the factory password by design, so they adopt the built-in
    account's row directly.  The session bookkeeping mirrors Service.login().
    """
    db = session.db
    row = db.one('SELECT * FROM users WHERE username=?', (BUILTIN_USERNAME,))
    if not row:
        with db.transaction():
            session.ensure_builtin_owner()
        row = db.one('SELECT * FROM users WHERE username=?', (BUILTIN_USERNAME,))
    session.user_id = row['id']
    session.session_access = session.access_signature(row)
    session.session_password = row['password']
    epoch = db.one("SELECT value FROM meta WHERE key='auth_epoch'")
    session.session_epoch = epoch['value'] if epoch else ''
    return session
