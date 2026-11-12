"""Stable private-object registry. Local JSON remains readable for existing installations."""
import hashlib
import time
from sqlalchemy import text
from common.database_schema import engine_for, ensure_schema


class AssetRegistry:
    def __init__(self,url):
        self.engine=engine_for(url)
        ensure_schema(self.engine)

    def save(self,identity,kind,bucket,key,path=None,width=0,height=0,mime=None):
        digest=hashlib.sha256(); size=0
        if path:
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):
                    size+=len(block); digest.update(block)
        values=dict(id=identity,kind=kind,bucket=bucket,key=key,
                    mime=mime or ('image/jpeg' if kind=='portrait' else 'video/mp4'),
                    size=size,width=width,height=height,sha=digest.hexdigest() if path else '',created=time.time())
        suffix=('ON DUPLICATE KEY UPDATE bucket=VALUES(bucket),object_key=VALUES(object_key),size_bytes=VALUES(size_bytes),sha256=VALUES(sha256),width=VALUES(width),height=VALUES(height)' if self.engine.dialect.name=='mysql' else
                'ON CONFLICT(id,kind) DO UPDATE SET bucket=excluded.bucket,object_key=excluded.object_key,size_bytes=excluded.size_bytes,sha256=excluded.sha256,width=excluded.width,height=excluded.height')
        with self.engine.begin() as db:
            db.execute(text('INSERT INTO phase2_media_objects(id,kind,bucket,object_key,mime,size_bytes,width,height,sha256,created_at) VALUES(:id,:kind,:bucket,:key,:mime,:size,:width,:height,:sha,:created) '+suffix),values)

    def get(self,identity,kind):
        with self.engine.connect() as db:
            row=db.execute(text('SELECT bucket,object_key FROM phase2_media_objects WHERE id=:id AND kind=:kind'),dict(id=identity,kind=kind)).first()
            return {'bucket':row[0],'key':row[1]} if row else None

    def delete(self,identity,kind):
        with self.engine.begin() as db:
            db.execute(text('DELETE FROM phase2_media_objects WHERE id=:id AND kind=:kind'),
                       dict(id=identity,kind=kind))

    def expired_objects(self, cutoff, limit=200):
        with self.engine.connect() as db:
            rows = db.execute(text(
                'SELECT id,kind,bucket,object_key FROM phase2_media_objects '
                'WHERE created_at<:cutoff ORDER BY created_at LIMIT :limit'),
                dict(cutoff=cutoff, limit=limit)).mappings()
            return [dict(row) for row in rows]
