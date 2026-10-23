"""Password inputs with a built-in show / hide eye button.

Every password field in the application uses :class:`PasswordEdit`. The eye
glyph is drawn with QPainter so no icon asset, font or platform theme is
required, and the widget stays a plain ``QLineEdit`` so all existing
``.text()`` / ``isinstance(widget, QLineEdit)`` handling keeps working.
"""
from PySide6.QtCore import QPointF,Qt
from PySide6.QtGui import QColor,QIcon,QPainter,QPainterPath,QPen,QPixmap
from PySide6.QtWidgets import QLineEdit

ICON_COLOR='#6b7b95'

def eye_icon(revealed,size=20):
    """An eye glyph; a slash marks the password as currently hidden."""
    ratio=2
    pixmap=QPixmap(size*ratio,size*ratio);pixmap.setDevicePixelRatio(ratio);pixmap.fill(Qt.transparent)
    painter=QPainter(pixmap);painter.setRenderHint(QPainter.Antialiasing)
    pen=QPen(QColor(ICON_COLOR));pen.setWidthF(1.8);pen.setCapStyle(Qt.RoundCap);painter.setPen(pen)
    lid=QPainterPath();lid.moveTo(size*0.08,size*0.5);lid.quadTo(size*0.5,size*0.10,size*0.92,size*0.5);lid.quadTo(size*0.5,size*0.90,size*0.08,size*0.5)
    painter.drawPath(lid)
    painter.setBrush(QColor(ICON_COLOR));painter.drawEllipse(QPointF(size*0.5,size*0.5),size*0.14,size*0.14)
    painter.setBrush(Qt.NoBrush)
    if not revealed:painter.drawLine(QPointF(size*0.17,size*0.83),QPointF(size*0.83,size*0.17))
    painter.end()
    return QIcon(pixmap)

class PasswordEdit(QLineEdit):
    """A password input whose trailing eye button toggles hidden / plain text."""
    def __init__(self,text='',parent=None,maximum=200):
        super().__init__(text,parent)
        self.setEchoMode(QLineEdit.Password)
        if maximum:self.setMaxLength(maximum)
        self.reveal_action=self.addAction(eye_icon(False),QLineEdit.TrailingPosition)
        self.reveal_action.setToolTip('显示密码')
        self.reveal_action.triggered.connect(self.toggle_reveal)
    def is_revealed(self):
        return self.echoMode()==QLineEdit.Normal
    def toggle_reveal(self):
        revealed=not self.is_revealed()
        self.setEchoMode(QLineEdit.Normal if revealed else QLineEdit.Password)
        self.reveal_action.setIcon(eye_icon(revealed))
        self.reveal_action.setToolTip('隐藏密码' if revealed else '显示密码')
        return revealed
