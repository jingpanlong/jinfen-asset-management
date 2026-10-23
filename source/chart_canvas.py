"""Offline, native interactive charts using Qt's LGPL painting components."""
import math
from PySide6.QtCore import Qt,QRectF,QPointF,Signal
from PySide6.QtGui import QPainter,QColor,QPen,QBrush,QFont,QPainterPath,QImage
from PySide6.QtWidgets import QWidget,QToolTip

BLUE='#365cf5';INK='#25354f';MUTED='#7888a0'
PALETTE=['#365cf5','#2aad88','#eca548','#d66579','#8292b0','#8065cc']
COLORS={'闲置':'#2aad88','在用':'#365cf5','维修中':'#eca548','已报废':'#d66579','待审批':'#eca548','已批准':'#365cf5','已拒绝':'#d66579','已入库':'#2aad88','已到期':'#d66579','30天内到期':'#eca548','31至90天到期':'#8065cc','90天后到期':'#2aad88','未填写保修日期':'#8292b0'}

def short(value,unit=''):
    if abs(value)>=100000000:return f'{value/100000000:.2f}亿'
    if abs(value)>=10000:return f'{value/10000:.2f}万'
    if abs(value)>=1000:return f'{value:,.0f}'
    return f'{value:,.2f}'.rstrip('0').rstrip('.') if value!=int(value) else f'{int(value):,}'

class ChartCanvas(QWidget):
    pointClicked=Signal(object)
    def __init__(self,parent=None):
        super().__init__(parent);self.data=None;self.hits=[];self.hover=None;self.zoom=1.0;self.setMouseTracking(True);self.setMinimumHeight(390);self.setMinimumWidth(550);self.caption='';self.setAccessibleName('资产交互图表')
    def set_data(self,data,caption=''):
        self.data=data;self.caption=caption;self.hover=None;self.zoom=1.0;self.setAccessibleName(data['title']+'，单位'+data['unit']);self.update()
    def reset_zoom(self):self.zoom=1.0;self.update()
    def mouseMoveEvent(self,event):
        position=event.position();hit=next((index for shape,index in reversed(self.hits) if shape.contains(position)),None)
        if hit!=self.hover:self.hover=hit;self.update()
        if hit is not None:
            p=self.data['points'][hit];text=p['label']+'\n'+f"{p['value']:,.2f}".rstrip('0').rstrip('.')+' '+self.data['unit']
            if self.data['kind']=='scatter':text+=f"\n使用 {p['x']} 个月 · 数量 {p['size']} 件"
            text+='\n点击查看明细';QToolTip.showText(event.globalPosition().toPoint(),text,self)
        else:QToolTip.hideText()
    def leaveEvent(self,event):self.hover=None;QToolTip.hideText();self.update()
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            index=next((index for shape,index in reversed(self.hits) if shape.contains(event.position())),None)
            if index is not None:self.pointClicked.emit(self.data['points'][index])
    def wheelEvent(self,event):
        if self.data and self.data['kind']=='scatter':
            self.zoom=max(1,min(8,self.zoom*(1.2 if event.angleDelta().y()>0 else 1/1.2)));self.update();event.accept()
        else:event.ignore()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.fillRect(self.rect(),QColor('white'));p.setFont(QFont('Microsoft YaHei UI',10));self.hits=[]
        if not self.data:p.setPen(QColor(MUTED));p.drawText(self.rect(),Qt.AlignCenter,'请选择图表并刷新数据');return
        p.setFont(QFont('Microsoft YaHei UI',13,QFont.DemiBold));p.setPen(QColor(INK));p.drawText(QRectF(24,14,self.width()-48,30),Qt.AlignLeft|Qt.AlignVCenter,self.data['title'])
        p.setFont(QFont('Microsoft YaHei UI',9));p.setPen(QColor(MUTED));p.drawText(QRectF(self.width()-140,17,110,23),Qt.AlignRight|Qt.AlignVCenter,'单位：'+self.data['unit'])
        note=self.data.get('note','')+'\n'+self.caption
        p.drawText(QRectF(24,self.height()-75,self.width()-48,67),Qt.TextWordWrap|Qt.AlignLeft,note)
        points=self.data['points']
        if not points or (self.data['kind']=='donut' and not any(pt['value'] for pt in points)):
            p.setFont(QFont('Microsoft YaHei UI',12));p.drawText(QRectF(20,55,self.width()-40,self.height()-145),Qt.AlignCenter,'当前条件下暂无可绘制数据');return
        if self.data['kind']=='bar':self.draw_bars(p)
        elif self.data['kind']=='donut':self.draw_donut(p)
        elif self.data['kind']=='line':self.draw_line(p)
        elif self.data['kind']=='scatter':self.draw_scatter(p)
        elif self.data['kind']=='heatmap':self.draw_heatmap(p)
    def color(self,index,point):return QColor(COLORS.get(point['label'],PALETTE[index%len(PALETTE)]))
    def highlight(self,p,index):
        if self.hover==index:p.setPen(QPen(QColor('#243eac'),2))
        else:p.setPen(Qt.NoPen)
    def axes(self,p,rect,maximum,xlabels=None):
        maximum=max(1,maximum);p.setFont(QFont('Microsoft YaHei UI',9))
        for n in range(5):
            y=rect.bottom()-rect.height()*n/4;p.setPen(QPen(QColor('#e8edf5'),1));p.drawLine(QPointF(rect.left(),y),QPointF(rect.right(),y));p.setPen(QColor(MUTED));p.drawText(QRectF(4,y-10,rect.left()-14,20),Qt.AlignRight|Qt.AlignVCenter,short(maximum*n/4))
        if xlabels:
            step=max(1,math.ceil(len(xlabels)/6));indexes=sorted(set(range(0,len(xlabels),step))|{len(xlabels)-1})
            for i in indexes:
                x=rect.left()+rect.width()*i/max(1,len(xlabels)-1);label=xlabels[i][2:] if len(xlabels[i])>=7 else xlabels[i];p.drawText(QRectF(x-46,rect.bottom()+10,92,25),Qt.AlignCenter,label)
        return maximum
    def draw_bars(self,p):
        points=self.data['points'];left=min(230,max(120,self.width()*.24));rect=QRectF(left,66,self.width()-left-115,self.height()-165);maximum=max(1,max(pt['value'] for pt in points));row=rect.height()/len(points);h=min(25,row*.64)
        for index,point in enumerate(points):
            y=rect.top()+index*row+(row-h)/2;shape=QRectF(rect.left(),y,rect.width()*point['value']/maximum,h)
            p.setFont(QFont('Microsoft YaHei UI',9));p.setPen(QColor(INK));label=p.fontMetrics().elidedText(point['label'],Qt.ElideRight,int(left-42));p.drawText(QRectF(22,y-3,left-40,h+6),Qt.AlignRight|Qt.AlignVCenter,label)
            p.setPen(Qt.NoPen);p.setBrush(QColor('#eef2fb'));p.drawRoundedRect(QRectF(rect.left(),y,rect.width(),h),4,4);self.highlight(p,index);p.setBrush(QColor(BLUE if self.hover!=index else '#2448d7'));p.drawRoundedRect(shape,4,4)
            self.hits.append((QRectF(rect.left()-left+20,y-4,self.width()-40,h+8),index));p.setPen(QColor(INK));p.drawText(QRectF(shape.right()+9,y-3,100,h+6),Qt.AlignVCenter|Qt.AlignLeft,short(point['value']))
    def draw_donut(self,p):
        points=self.data['points'];total=sum(pt['value'] for pt in points);area=QRectF(22,60,self.width()*.55,self.height()-155);radius=min(area.width(),area.height())*.42;center=area.center();outer=QRectF(center.x()-radius,center.y()-radius,2*radius,2*radius);angle=90
        for index,point in enumerate(points):
            if point['value']<=0:continue
            span=point['value']/total*360;path=QPainterPath();path.moveTo(center);path.arcTo(outer,angle,-span);path.closeSubpath();self.highlight(p,index);p.setBrush(self.color(index,point));p.drawPath(path);self.hits.append((path,index));angle-=span
        p.setPen(Qt.NoPen);p.setBrush(QColor('white'));inner=radius*.66;p.drawEllipse(center,inner,inner);p.setPen(QColor(INK));p.setFont(QFont('Microsoft YaHei UI',23,QFont.DemiBold));p.drawText(QRectF(center.x()-inner,center.y()-28,inner*2,48),Qt.AlignCenter,short(total));p.setFont(QFont('Microsoft YaHei UI',10));p.setPen(QColor(MUTED));p.drawText(QRectF(center.x()-inner,center.y()+20,inner*2,28),Qt.AlignCenter,'合计 / '+self.data['unit'])
        # Replace wedge hit paths with rings so the center is not clickable.
        hole=QPainterPath();hole.addEllipse(center,inner,inner);self.hits=[(shape.subtracted(hole),idx) for shape,idx in self.hits]
        x=self.width()*.60;y=max(75,area.center().y()-len(points)*17)
        for index,point in enumerate(points):
            p.setBrush(self.color(index,point));p.setPen(Qt.NoPen);p.drawRoundedRect(QRectF(x,y+6,9,9),2,2);p.setPen(QColor(INK));p.setFont(QFont('Microsoft YaHei UI',10));text=f"{point['label']}  {short(point['value'])}  ({point['value']/total*100:.1f}%)";p.drawText(QRectF(x+20,y,self.width()-x-30,25),Qt.AlignVCenter|Qt.AlignLeft,text);self.hits.append((QRectF(x,y,self.width()-x-20,29),index));y+=34
    def draw_line(self,p):
        points=self.data['points'];rect=QRectF(84,68,self.width()-120,self.height()-177);maximum=self.axes(p,rect,max(pt['value'] for pt in points)*1.15,[pt['label'] for pt in points]);coords=[]
        for i,pt in enumerate(points):coords.append(QPointF(rect.left()+rect.width()*i/max(1,len(points)-1),rect.bottom()-rect.height()*pt['value']/maximum))
        line=QPainterPath();line.moveTo(coords[0])
        for q in coords[1:]:line.lineTo(q)
        fill=QPainterPath(line);fill.lineTo(QPointF(rect.right(),rect.bottom()));fill.lineTo(QPointF(rect.left(),rect.bottom()));fill.closeSubpath();p.setPen(Qt.NoPen);p.setBrush(QColor('#edf2ff'));p.drawPath(fill);p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor(BLUE),2.5));p.drawPath(line)
        maximum_index=max(range(len(points)),key=lambda i:points[i]['value'])
        for i,q in enumerate(coords):
            p.setPen(QPen(QColor('white'),2));p.setBrush(QColor(BLUE));p.drawEllipse(q,6 if i==self.hover else 4,6 if i==self.hover else 4);self.hits.append((QRectF(q.x()-10,q.y()-10,20,20),i))
            if i==self.hover or i==maximum_index or i==len(points)-1:
                p.setFont(QFont('Microsoft YaHei UI',9));p.setPen(QColor(INK));p.drawText(QRectF(max(15,min(q.x()-55,self.width()-120)),max(rect.top()-18,q.y()-27),110,20),Qt.AlignCenter,short(points[i]['value']))
    def draw_scatter(self,p):
        points=self.data['points'];rect=QRectF(84,68,self.width()-120,self.height()-182);xmax=max(1,max(pt['x'] for pt in points)*1.12)/self.zoom;ymax=max(1,max(pt['value'] for pt in points)*1.12)/self.zoom;self.axes(p,rect,ymax)
        for n in range(6):
            x=rect.left()+rect.width()*n/5;p.setPen(QColor(MUTED));p.drawText(QRectF(x-32,rect.bottom()+7,64,23),Qt.AlignCenter,short(xmax*n/5))
        p.drawText(QRectF(rect.left(),rect.bottom()+33,rect.width(),22),Qt.AlignCenter,'使用月数 · 滚轮放大，双击空白处复位')
        p.setClipRect(rect.adjusted(-1,-1,1,1))
        for i,pt in enumerate(points):
            if pt['x']>xmax or pt['value']>ymax:continue
            q=QPointF(rect.left()+pt['x']/xmax*rect.width(),rect.bottom()-pt['value']/ymax*rect.height());r=min(18,4+math.sqrt(pt['size'])*1.5);color=QColor(BLUE);color.setAlpha(125 if i!=self.hover else 220);p.setPen(QPen(QColor(BLUE),1));p.setBrush(color);p.drawEllipse(q,r,r);self.hits.append((QRectF(q.x()-r,q.y()-r,r*2,r*2),i))
        p.setClipping(False)
    def mouseDoubleClickEvent(self,event):
        if self.data and self.data['kind']=='scatter':self.reset_zoom()
    def draw_heatmap(self,p):
        points=self.data['points'];xs=self.data['x_labels'];ys=self.data['y_labels'];rect=QRectF(91,80,self.width()-125,self.height()-189);cw=rect.width()/len(xs);rh=rect.height()/len(ys);maximum=max(1,max(pt['value'] for pt in points))
        for i,pt in enumerate(points):
            ratio=pt['value']/maximum;color=QColor(BLUE);color.setAlpha(int(20+ratio*220));shape=QRectF(rect.left()+pt['x']*cw+2,rect.top()+pt['y']*rh+2,cw-4,rh-4);self.highlight(p,i);p.setBrush(color);p.drawRoundedRect(shape,3,3);self.hits.append((shape,i))
            if cw>=35:p.setPen(QColor('white' if ratio>.55 else INK));p.drawText(shape,Qt.AlignCenter,str(pt['value']))
        p.setPen(QColor(MUTED));p.setFont(QFont('Microsoft YaHei UI',9))
        for row,label in enumerate(ys):p.drawText(QRectF(8,rect.top()+row*rh,75,rh),Qt.AlignRight|Qt.AlignVCenter,label)
        step=max(1,math.ceil(len(xs)/8))
        for col in range(0,len(xs),step):p.drawText(QRectF(rect.left()+col*cw-18,rect.bottom()+8,max(70,cw),25),Qt.AlignLeft|Qt.AlignVCenter,xs[col][2:])
    def save_png(self,path,scale=2):
        image=QImage(self.width()*scale,self.height()*scale,QImage.Format_ARGB32);image.fill(QColor('white'));p=QPainter(image);p.scale(scale,scale);self.render(p,QPointF(0,0).toPoint());p.end()
        if not image.save(path,'PNG'):raise OSError('图片保存失败')
