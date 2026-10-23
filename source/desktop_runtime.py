"""Per-user Windows startup, single instance activation and tray lifecycle."""
import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from PySide6.QtCore import QObject,QTimer,QEventLoop
from PySide6.QtGui import QIcon,QAction
from PySide6.QtNetwork import QLocalServer,QLocalSocket
from PySide6.QtWidgets import QApplication,QSystemTrayIcon,QMenu,QMessageBox,QDialog,QVBoxLayout,QCheckBox,QLabel,QHBoxLayout,QPushButton
from database import data_dir

NAME='锦纷资产管理系统'
RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE='JinfenAssets'
DEFAULTS={'close_to_tray':True,'minimize_to_tray':False,'startup_to_tray':True}

def load_preferences():
    file=data_dir()/'desktop.json'
    if not file.exists():return dict(DEFAULTS)
    try:
        saved=json.loads(file.read_text('utf-8'));return {k:bool(saved.get(k,v)) for k,v in DEFAULTS.items()}
    except (ValueError,OSError):return dict(DEFAULTS)

def save_preferences(values):
    file=data_dir()/'desktop.json';temp=file.with_suffix('.tmp')
    temp.write_text(json.dumps({k:bool(values.get(k,v)) for k,v in DEFAULTS.items()},ensure_ascii=False,indent=2),'utf-8');temp.replace(file)

def startup_command():
    if not getattr(sys,'frozen',False):raise RuntimeError('开机自启动请使用打包后的 EXE，源码运行模式不写入启动项')
    return subprocess.list2cmdline([str(Path(sys.executable).resolve()),'--startup'])

class StartupRegistry:
    def __init__(self,key=RUN_KEY,value=RUN_VALUE):self.key=key;self.value=value
    def read(self):
        if os.name!='nt':return ''
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.key,0,winreg.KEY_READ) as key:return winreg.QueryValueEx(key,self.value)[0]
        except FileNotFoundError:return ''
    def write(self,enabled,command=None):
        if os.name!='nt':raise RuntimeError('开机自启动只支持 Windows')
        import winreg
        if enabled:
            command=command or startup_command()
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,self.key,0,winreg.KEY_SET_VALUE) as key:winreg.SetValueEx(key,self.value,0,winreg.REG_SZ,command)
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.key,0,winreg.KEY_SET_VALUE) as key:
                    try:winreg.DeleteValue(key,self.value)
                    except FileNotFoundError:pass
            except FileNotFoundError:pass

class SingleInstance(QObject):
    def __init__(self,on_activate,parent=None):
        super().__init__(parent);self.server=None;self.on_activate=on_activate;self.sockets=[]
        identity=hashlib.sha256(str(data_dir().resolve()).casefold().encode()).hexdigest()[:20]
        self.name='JinfenAssets_'+identity;self.mutex=None
    def acquire(self,startup=False):
        primary=True
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR];kernel.CreateMutexW.restype=wintypes.HANDLE
            handle=kernel.CreateMutexW(None,False,'Local\\'+self.name)
            if not handle:raise OSError('无法建立单实例锁')
            primary=ctypes.get_last_error()!=183
            if primary:self.mutex=handle
            else:
                kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(handle)
        socket=QLocalSocket();socket.connectToServer(self.name)
        if socket.waitForConnected(250):
            loop=QEventLoop();timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit);socket.readyRead.connect(loop.quit);socket.disconnected.connect(loop.quit)
            socket.write(b'ping' if startup else b'show');socket.flush();timeout.start(1500)
            if not socket.bytesAvailable():loop.exec()
            timeout.stop();socket.readAll();socket.disconnectFromServer();return False
        if not primary:return False
        self.server=QLocalServer(self);self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self.server.listen(self.name):
            # A stale endpoint can remain after abnormal termination.
            QLocalServer.removeServer(self.name)
            if not self.server.listen(self.name):raise RuntimeError('无法建立单实例通信，请稍后重试')
        self.server.newConnection.connect(self.connection);return True
    def connection(self):
        while self.server.hasPendingConnections():
            socket=self.server.nextPendingConnection();self.sockets.append(socket)
            buffer=bytearray()
            def receive(s=socket,payload=buffer):
                payload.extend(bytes(s.readAll()))
                if payload==b'show':self.on_activate();s.write(b'ack');s.flush();s.disconnectFromServer()
                elif payload==b'ping':s.write(b'ack');s.flush();s.disconnectFromServer()
                elif len(payload)>16:s.disconnectFromServer()
            socket.readyRead.connect(receive);socket.disconnected.connect(lambda s=socket:self.release_socket(s))
            if socket.bytesAvailable():receive()
    def release_socket(self,socket):
        if socket in self.sockets:self.sockets.remove(socket)
        socket.deleteLater()
    def close(self):
        if self.server:self.server.close()
        if self.mutex:
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.windll.kernel32;kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(self.mutex);self.mutex=None

class DesktopSettings(QDialog):
    def __init__(self,controller,parent=None):
        super().__init__(parent);self.c=controller;self.setWindowTitle('运行设置 · '+NAME);self.resize(590,390)
        root=QVBoxLayout(self);root.setContentsMargins(25,22,25,22)
        self.autostart=QCheckBox('随 Windows 登录自动启动');self.autostart.setChecked(bool(controller.registry.read()));self.autostart.setEnabled(os.name=='nt' and bool(getattr(sys,'frozen',False)));root.addWidget(self.autostart)
        self.fields={}
        for key,title in [('startup_to_tray','自启动时先收起到系统托盘（显示窗口后仍需登录）'),('close_to_tray','点击关闭按钮时缩小到托盘，保留后台运行'),('minimize_to_tray','点击最小化按钮时也收起到托盘')]:
            checkbox=QCheckBox(title);checkbox.setChecked(controller.preferences[key]);root.addWidget(checkbox);self.fields[key]=checkbox
        note=QLabel('托盘图标右键可以显示窗口、设置自启动或完全退出。\n若系统托盘不可用，窗口保持显示，避免后台运行后无法找回。\n自启动只影响当前 Windows 用户，不保存登录密码或自动登录。\n保存自启动选项将指向当前 EXE；移动单文件版后请重新保存设置。');note.setWordWrap(True);root.addWidget(note);root.addStretch()
        row=QHBoxLayout();row.addStretch();cancel=QPushButton('取消');cancel.clicked.connect(self.reject);row.addWidget(cancel);save=QPushButton('保存设置');save.setProperty('primary',True);save.clicked.connect(self.save);row.addWidget(save);root.addLayout(row)
    def save(self):
        try:
            values={k:w.isChecked() for k,w in self.fields.items()}
            if self.autostart.isEnabled():self.c.registry.write(self.autostart.isChecked())
            save_preferences(values);self.c.preferences=values;self.c.sync_autostart();self.accept()
        except Exception as e:QMessageBox.warning(self,'设置未保存',str(e))

class DesktopController(QObject):
    def __init__(self,application,db,login_factory,main_factory):
        super().__init__(application);self.app=application;self.db=db;self.login_factory=login_factory;self.main_factory=main_factory;self.current=None;self.preferences=load_preferences();self.registry=StartupRegistry();self.exiting=False;self.tip_shown=False
        self.tray=QSystemTrayIcon(application.windowIcon(),self);self.tray.setToolTip(NAME);self.menu=QMenu()
        self.show_action=QAction('显示窗口',self.menu);self.show_action.triggered.connect(self.show_current);self.menu.addAction(self.show_action);self.menu.addSeparator()
        self.start_action=QAction('开机自启动',self.menu);self.start_action.setCheckable(True);self.start_action.setEnabled(os.name=='nt' and bool(getattr(sys,'frozen',False)));self.start_action.triggered.connect(self.toggle_autostart);self.menu.addAction(self.start_action)
        settings=QAction('运行设置',self.menu);settings.triggered.connect(self.settings);self.menu.addAction(settings)
        about=QAction('关于系统',self.menu);about.triggered.connect(self.about);self.menu.addAction(about);self.menu.addSeparator()
        exit_action=QAction('退出程序',self.menu);exit_action.triggered.connect(self.quit);self.menu.addAction(exit_action);self.tray.setContextMenu(self.menu);self.tray.activated.connect(self.activated);self.sync_autostart()
        if self.available():self.tray.show()
    def available(self):return QSystemTrayIcon.isSystemTrayAvailable() and not self.tray.icon().isNull()
    def sync_autostart(self):self.start_action.setChecked(bool(self.registry.read()))
    def start(self,startup=False):
        self.open_login(hidden=startup and self.preferences['startup_to_tray'] and self.available())
    def open_login(self,hidden=False):
        if self.exiting:return
        login=self.login_factory(self.db);login.desktop=self;self.current=login;login.finished.connect(lambda result,w=login:self.login_finished(w,result));self.tray.setToolTip(NAME+' · 等待登录')
        if not hidden:login.show()
    def login_finished(self,login,result):
        if self.exiting:return
        self.db=login.db
        if result==QDialog.Accepted:
            login.desktop=None;login.hide();login.deleteLater();main=self.main_factory(login.service);main.desktop=self;self.current=main;main.show();self.tray.setToolTip(NAME+' · '+main.user['display_name'])
        else:self.quit()
    def show_current(self):
        if self.current:self.current.showNormal();self.current.raise_();self.current.activateWindow()
    def hide_to_tray(self,window=None):
        window=window or self.current
        if not self.available():
            if window:window.showNormal()
            QMessageBox.information(window,NAME,'当前系统托盘不可用，窗口将保持显示。');return False
        self.tray.show()
        if window:window.hide()
        if not self.tip_shown:
            self.tray.showMessage(NAME,'软件继续在托盘运行。右键图标可显示窗口或退出程序。',QSystemTrayIcon.Information,3500);self.tip_shown=True
        return True
    def request_close(self,window,event):
        if self.exiting:event.accept();return
        if getattr(window,'logout_requested',False):
            event.accept();self.db=window.db;retire=getattr(window,'retire',None)
            if callable(retire):
                try:retire()
                except Exception:logging.exception('Share shutdown failed')
            else:
                window.timer.stop();window.auto_backup_timer.stop()
            window.desktop=None;window.deleteLater();self.current=None;QTimer.singleShot(0,self.open_login);return
        if self.preferences['close_to_tray'] and self.available():event.ignore();self.hide_to_tray(window)
        else:event.accept();QTimer.singleShot(0,self.quit)
    def activated(self,reason):
        if reason in [QSystemTrayIcon.Trigger,QSystemTrayIcon.DoubleClick]:self.show_current()
    def settings(self):DesktopSettings(self,self.current).exec()
    def about(self):
        from ui_extras import AboutDialog
        AboutDialog('2.3.0',self.current).exec()
    def toggle_autostart(self,checked):
        try:self.registry.write(checked)
        except Exception as e:QMessageBox.warning(self.current,'自启动设置失败',str(e))
        self.sync_autostart()
    def quit(self):
        if self.exiting:return
        self.exiting=True;self.tray.hide()
        if self.current:
            self.db=self.current.db
            retire=getattr(self.current,'retire',None)
            if callable(retire):
                try:retire()
                except Exception:logging.exception('Share shutdown failed')
            else:
                if hasattr(self.current,'timer'):self.current.timer.stop()
                if hasattr(self.current,'auto_backup_timer'):self.current.auto_backup_timer.stop()
            if hasattr(self.current,'s') and self.current.s.user_id:
                try:self.current.s.logout()
                except Exception:logging.exception('Exit audit failed')
            self.current.close()
        self.app.quit()
