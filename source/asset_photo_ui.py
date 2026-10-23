from pathlib import Path
from datetime import datetime
from decimal import Decimal
from PySide6.QtCore import Qt,Signal,QSize
from PySide6.QtGui import QPixmap,QIcon
from PySide6.QtWidgets import (QTableWidget,QDialog,QLabel,QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QFileDialog,QMessageBox,QTabWidget,QTextEdit,QListWidget,QListWidgetItem,QAbstractItemView)
from service import LABELS,BusinessError
from asset_photos import EXTENSIONS,upload_images,image_list,image_bytes,thumbnail
from ui_extras import AttachmentDialog

def files_from_mime(mime):
    if not mime.hasUrls():return []
    urls=mime.urls()
    return [u.toLocalFile() for u in urls] if urls and all(u.isLocalFile() for u in urls) else []

class AssetTable(QTableWidget):
    imagesDropped=Signal(str,list)
    def __init__(self,parent=None):
        super().__init__(parent);self.photo_drop_enabled=False;self.setAcceptDrops(True);self.viewport().setAcceptDrops(True);self.setDragDropMode(QAbstractItemView.DropOnly);self.setIconSize(QSize(90,60))
    def dragEnterEvent(self,event):
        if self.photo_drop_enabled and files_from_mime(event.mimeData()):event.acceptProposedAction()
        else:event.ignore()
    def dragMoveEvent(self,event):
        row=self.rowAt(int(event.position().y()))
        if self.photo_drop_enabled and row>=0 and files_from_mime(event.mimeData()):self.setCurrentCell(row,0);event.acceptProposedAction()
        else:event.ignore()
    def dropEvent(self,event):
        row=self.rowAt(int(event.position().y()));paths=files_from_mime(event.mimeData())
        if not self.photo_drop_enabled or row<0 or not paths or not self.item(row,0):event.ignore();return
        aid=self.item(row,0).data(Qt.UserRole)
        if not aid:event.ignore();return
        self.setCurrentCell(row,0);event.acceptProposedAction();self.imagesDropped.emit(aid,paths)

class PhotoDropArea(QLabel):
    filesDropped=Signal(list)
    def __init__(self):
        super().__init__();self.setAlignment(Qt.AlignCenter);self.setMinimumSize(360,250);self.setStyleSheet('background:#f3f6fc;border:2px dashed #b3c3f5;border-radius:12px;color:#647595;');self.setAcceptDrops(True);self.upload_enabled=False
    def dragEnterEvent(self,event):
        if self.upload_enabled and files_from_mime(event.mimeData()):event.acceptProposedAction()
        else:event.ignore()
    def dragMoveEvent(self,event):self.dragEnterEvent(event)
    def dropEvent(self,event):
        paths=files_from_mime(event.mimeData())
        if self.upload_enabled and paths:event.acceptProposedAction();self.filesDropped.emit(paths)
        else:event.ignore()

class PhotoPicker(QWidget):
    """Choose-or-drag image picker embedded in the asset add / edit form.

    Files are only staged here; they are uploaded once the asset exists, so a
    brand-new asset can carry photos in the very same save.
    """
    def __init__(self,parent=None):
        super().__init__(parent);self.paths=[]
        root=QVBoxLayout(self);root.setContentsMargins(0,0,0,0);root.setSpacing(8)
        head=QLabel('资产图片（可选）');head.setStyleSheet('font-weight:600;color:#365cf5;');root.addWidget(head)
        self.drop=PhotoDropArea();self.drop.setMinimumSize(320,104);self.drop.setMaximumHeight(138);self.drop.upload_enabled=True
        self.drop.setText('拖动上传：把本地图片拖到此处\n选择上传：点击下方“选择图片”\n支持 PNG / JPEG / BMP / WebP，可多选、可多次追加')
        self.drop.filesDropped.connect(self.add_paths);root.addWidget(self.drop)
        self.gallery=QListWidget();self.gallery.setViewMode(QListWidget.IconMode);self.gallery.setIconSize(QSize(64,46));self.gallery.setGridSize(QSize(88,78))
        self.gallery.setSelectionMode(QAbstractItemView.ExtendedSelection);self.gallery.setMinimumHeight(84);self.gallery.setMaximumHeight(106);root.addWidget(self.gallery)
        bar=QHBoxLayout();self.choose_button=QPushButton('选择图片');self.choose_button.clicked.connect(self.choose);bar.addWidget(self.choose_button)
        self.remove_button=QPushButton('移除选中');self.remove_button.clicked.connect(self.remove_selected);bar.addWidget(self.remove_button)
        self.clear_button=QPushButton('清空');self.clear_button.clicked.connect(self.clear);bar.addWidget(self.clear_button)
        self.count=QLabel('');self.count.setObjectName('subtitle');bar.addStretch();bar.addWidget(self.count);root.addLayout(bar)
        self.update_count()
    def choose(self):
        paths,_=QFileDialog.getOpenFileNames(self,'选择资产图片','','图片 (*.png *.jpg *.jpeg *.bmp *.webp)')
        if paths:self.add_paths(paths)
    def add_paths(self,paths):
        rejected=[]
        for path in paths:
            file=Path(path)
            if file.suffix.lower() not in EXTENSIONS or not file.is_file():rejected.append(file.name+'（仅支持 PNG / JPEG / BMP / WebP）');continue
            if str(file) in self.paths:continue
            try:raw=thumbnail(file.read_bytes())
            except BusinessError as e:rejected.append(file.name+'（'+str(e)+'）');continue
            self.paths.append(str(file));pix=QPixmap();pix.loadFromData(raw)
            item=QListWidgetItem(QIcon(pix),file.name);item.setToolTip(str(file));self.gallery.addItem(item)
        self.update_count()
        if rejected:QMessageBox.warning(self,'资产图片','以下图片未加入：\n'+'\n'.join(rejected))
    def remove_selected(self):
        rows=sorted({self.gallery.row(item) for item in self.gallery.selectedItems()},reverse=True)
        for row in rows:self.gallery.takeItem(row);self.paths.pop(row)
        self.update_count()
    def clear(self):
        self.gallery.clear();self.paths=[];self.update_count()
    def update_count(self):
        size=0
        for path in self.paths:
            try:size+=Path(path).stat().st_size
            except OSError:pass
        self.count.setText('已选择 '+str(len(self.paths))+' 张 · '+f'{size/1048576:.1f} MB，保存资产后一并上传' if self.paths else '尚未选择图片（可留空）')
        self.remove_button.setEnabled(bool(self.paths));self.clear_button.setEnabled(bool(self.paths))

class AssetDetailDialog(QDialog):
    photosChanged=Signal()
    def __init__(self,service,asset_id,parent=None):
        super().__init__(parent);self.s=service;service.permit('assets.read');self.asset=service.check_asset(asset_id);self.asset_id=asset_id;self.photos=[];self.setWindowTitle('资产详情 · '+asset_id+' · '+self.asset['name']);self.resize(1050,780)
        root=QVBoxLayout(self);head=QLabel(self.asset['name']+'  ·  '+asset_id);head.setStyleSheet('font-size:22px;font-weight:700;color:#365cf5;');head.setWordWrap(True);root.addWidget(head)
        self.tabs=QTabWidget();root.addWidget(self.tabs,1);overview=QDialog();layout=QHBoxLayout(overview);left=QVBoxLayout();self.preview=PhotoDropArea();self.preview.filesDropped.connect(lambda paths:self.safe(lambda:self.upload(paths)));left.addWidget(self.preview,1)
        self.caption=QLabel();self.caption.setWordWrap(True);left.addWidget(self.caption);self.gallery=QListWidget();self.gallery.setViewMode(QListWidget.IconMode);self.gallery.setIconSize(QSize(90,62));self.gallery.setGridSize(QSize(122,100));self.gallery.setMaximumHeight(140);self.gallery.currentRowChanged.connect(lambda i:self.safe(lambda:self.show_image(i)));left.addWidget(self.gallery);layout.addLayout(left,1)
        details=QTextEdit();details.setReadOnly(True);employees={e['id']:e['name'] for e in service.list('employees')}
        lines=[]
        for key,title in LABELS.items():
            value=self.asset[key]
            if key=='price_cents':value=f'￥{Decimal(value)/100:,.2f}'
            elif key=='residual_bp':value=f'{Decimal(value)/100}%'
            elif key=='employee_id':value=employees.get(value,value) or '未领用'
            lines.append(title+'：'+str(value or '—'))
        lines.append('最近更新：'+self.local_time(self.asset['updated_at']));details.setPlainText('\n\n'.join(lines));layout.addWidget(details,1);self.tabs.addTab(overview,'资产概览与图片')
        history=QTextEdit();history.setReadOnly(True);events=sorted([e for e in service.list('events') if e['asset_id']==asset_id],key=lambda e:e['created_at'],reverse=True);history.setPlainText('\n\n'.join(self.local_time(e['created_at'])+'  '+e['action']+'\n'+e['detail'] for e in events) or '暂无流转历史');self.tabs.addTab(history,'完整流转历史')
        row=QHBoxLayout();self.upload_button=self.add_button(row,'上传资产图片',self.choose);self.download_button=self.add_button(row,'下载当前图片',self.download);self.remove_button=self.add_button(row,'移除当前图片',self.remove);self.attachment_button=self.add_button(row,'全部附件',self.attachments);row.addStretch();self.add_button(row,'关闭',self.accept);root.addLayout(row);self.reload()
    @staticmethod
    def local_time(value):
        try:return datetime.fromisoformat(value).astimezone().strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:return value
    def add_button(self,row,title,fn):
        b=QPushButton(title);b.clicked.connect(lambda:self.safe(fn));row.addWidget(b);return b
    def safe(self,fn):
        try:fn()
        except Exception as e:QMessageBox.warning(self,'资产图片',str(e))
    def reload(self):
        self.s.permit('assets.read');self.s.check_asset(self.asset_id);read=self.s.can('attachments.read');write=self.s.can('attachments.write');self.preview.upload_enabled=write;self.upload_button.setEnabled(write);self.attachment_button.setEnabled(read)
        self.gallery.blockSignals(True);self.gallery.clear();self.photos=image_list(self.s,self.asset_id) if read else []
        for p in self.photos:
            pix=QPixmap()
            try:pix.loadFromData(thumbnail(image_bytes(self.s,p['id'])))
            except BusinessError:pass
            item=QListWidgetItem(QIcon(pix),p['filename']);item.setToolTip(p['filename']);self.gallery.addItem(item)
        self.gallery.blockSignals(False);self.download_button.setEnabled(read and bool(self.photos));self.remove_button.setEnabled(write and bool(self.photos))
        if self.photos:self.gallery.setCurrentRow(0)
        else:
            self.preview.setPixmap(QPixmap());self.preview.setText('将图片拖到这里\n或点击“上传资产图片”' if write else '暂无资产图片' if read else '当前账号没有查看图片权限');self.caption.setText('PNG / JPEG / BMP / WebP · 单张最多 10MB\n图片与资产关联，包含在完整数据库备份中。')
    def show_image(self,index):
        if not 0<=index<len(self.photos):return
        p=self.photos[index];pix=QPixmap();pix.loadFromData(thumbnail(image_bytes(self.s,p['id']),(1000,800)))
        if pix.isNull():self.preview.setText('图片无法预览');self.preview.setPixmap(QPixmap())
        else:self.preview.setPixmap(pix.scaled(460,380,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        self.caption.setText(p['filename']+f" · {p['size_bytes']/1024:,.1f} KB\n"+('可以继续拖入图片，支持一次上传多张。' if self.preview.upload_enabled else '图片可查看及下载。'))
    def choose(self):
        paths,_=QFileDialog.getOpenFileNames(self,'选择资产图片','','图片 (*.png *.jpg *.jpeg *.bmp *.webp)')
        if paths:self.upload(paths)
    def upload(self,paths):upload_images(self.s,self.asset_id,paths);self.reload();self.photosChanged.emit()
    def selected(self):
        i=self.gallery.currentRow()
        if not 0<=i<len(self.photos):raise BusinessError('请选择一张图片')
        return self.photos[i]
    def download(self):
        p=self.selected();path,_=QFileDialog.getSaveFileName(self,'保存资产图片',p['filename'],'所有文件 (*)')
        if path:self.s.download_attachment(p['id'],path)
    def remove(self):
        p=self.selected()
        if QMessageBox.question(self,'移除图片','确认移除图片 '+p['filename']+'？')!=QMessageBox.Yes:return
        self.s.remove_attachment(p['id']);self.reload();self.photosChanged.emit()
    def attachments(self):AttachmentDialog(self.s,self.asset,self).exec();self.reload();self.photosChanged.emit()
