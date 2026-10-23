"""Validated asset photos reuse shared, backed-up attachment storage."""
import base64,hashlib,io
from pathlib import Path
from PIL import Image,ImageOps
from service import BusinessError

EXTENSIONS={'.png','.jpg','.jpeg','.bmp','.webp'}
MAX_PIXELS=20_000_000

def thumbnail(raw,size=(192,128)):
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width*image.height>MAX_PIXELS:raise BusinessError('图片最多 2000 万像素')
            image=ImageOps.exif_transpose(image);image.thumbnail(size);image=image.convert('RGBA')
            out=io.BytesIO();image.save(out,'PNG');return out.getvalue()
    except BusinessError:raise
    except Exception as e:raise BusinessError('图片内容无法读取，请使用有效的 PNG、JPEG、BMP 或 WebP 图片') from e

def upload_images(service,asset_id,paths):
    service.permit('attachments.write');service.check_asset(asset_id)
    if not paths:raise BusinessError('请选择图片')
    for path in paths:
        file=Path(path)
        if file.suffix.lower() not in EXTENSIONS:raise BusinessError('请拖入 PNG、JPEG、BMP 或 WebP 图片')
        if file.stat().st_size>10*1024*1024:raise BusinessError('单张图片最多 10MB')
        thumbnail(file.read_bytes())
    return service.upload_attachments(asset_id,paths)

def image_list(service,asset_id):
    return [a for a in service.attachments(asset_id) if Path(a['filename']).suffix.lower() in EXTENSIONS]

def image_bytes(service,attachment_id):
    service.permit('attachments.read');service.check_row('attachments',attachment_id)
    row=service.db.one('SELECT * FROM attachments WHERE id=?',(attachment_id,))
    if not row or Path(row['filename']).suffix.lower() not in EXTENSIONS:raise BusinessError('资产图片不存在')
    try:raw=base64.b64decode(row['content_b64'],validate=True)
    except Exception as e:raise BusinessError('图片编码损坏') from e
    if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise BusinessError('图片校验失败')
    return raw

def table_thumbnails(service,asset_ids):
    service.permit('assets.read');service.permit('attachments.read')
    allowed={a['id'] for a in service.list('assets')} & set(asset_ids);result={}
    rows=service.db.all('SELECT * FROM attachments ORDER BY created_at DESC,id DESC')
    for row in rows:
        aid=row['asset_id']
        if aid not in allowed or aid in result or Path(row['filename']).suffix.lower() not in EXTENSIONS:continue
        try:
            raw=base64.b64decode(row['content_b64'],validate=True)
            if hashlib.sha256(raw).hexdigest()!=row['sha256']:continue
            result[aid]=thumbnail(raw)
        except (ValueError,TypeError):continue
    return result
