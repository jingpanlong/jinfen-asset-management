"""Application wait-cursor management.

Qt's override cursor is *application wide*: it applies to every window of the
process, including modal dialogs opened by the running action. A form, a detail
window or a message box started inside a wrapped action therefore inherited the
wait cursor and made the pointer spin across the whole dialog, which reads as a
frozen application.

This module keeps nested pushes balanced and suspends the wait cursor whenever a
modal window is on screen, so long-running work still shows progress on the main
window while dialogs keep a normal pointer.
"""
from PySide6.QtCore import QObject,Qt,QTimer
from PySide6.QtWidgets import QApplication

class BusyCursor(QObject):
    """Reference-counted wait cursor that never covers a modal window."""
    POLL_MS=200
    def __init__(self,application):
        super().__init__(application);self.depth=0;self.shown=False
        self.timer=QTimer(self);self.timer.setInterval(self.POLL_MS);self.timer.timeout.connect(self.sync)
    def push(self):
        self.depth+=1
        if not self.timer.isActive():self.timer.start()
        self.sync()
    def pop(self):
        self.depth=max(0,self.depth-1)
        if not self.depth:self.timer.stop()
        self.sync()
    def sync(self):
        """Match the real cursor state to 'busy work running and no modal dialog'."""
        wanted=self.depth>0 and QApplication.activeModalWidget() is None
        if wanted==self.shown:return
        if wanted:QApplication.setOverrideCursor(Qt.WaitCursor)
        else:QApplication.restoreOverrideCursor()
        self.shown=wanted
    def reset(self):
        self.depth=0;self.timer.stop();self.sync()

_manager=None

def manager():
    """Return the process-wide manager, creating it once a QApplication exists."""
    global _manager
    try:
        if _manager is not None:return _manager
    except RuntimeError:
        _manager=None
    application=QApplication.instance()
    if application is None:return None
    _manager=BusyCursor(application)
    return _manager

def push_busy():
    current=manager()
    if current:current.push()

def pop_busy():
    current=manager()
    if current:current.pop()

def reset_busy():
    current=manager()
    if current:current.reset()

def sync_busy():
    """Re-evaluate the cursor immediately instead of waiting for the poll timer."""
    current=manager()
    if current:current.sync()
