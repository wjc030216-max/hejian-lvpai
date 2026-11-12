"""Background template media optimization.

Upload returns as soon as the archive is validated and stored; this worker then
re-encodes the sample video/cover and publishes the template when it finishes.
Jobs live in the database so a restart re-queues whatever was interrupted.
"""
import os
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy import text

from common import service_log

_lock = threading.Lock()
_started = False


def ensure_table(engine):
    with engine.begin() as db:
        db.execute(text('''CREATE TABLE IF NOT EXISTS phase2_media_jobs (
            id VARCHAR(32) PRIMARY KEY, template_code VARCHAR(128) NOT NULL,
            revision VARCHAR(32) NOT NULL, status VARCHAR(16) NOT NULL,
            error TEXT, created DOUBLE NOT NULL, updated DOUBLE NOT NULL)'''))


def enqueue(store, code, revision):
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    with store.engine.begin() as db:
        db.execute(text('INSERT INTO phase2_media_jobs(id,template_code,revision,status,created,updated) '
                        'VALUES(:id,:code,:rev,:status,:now,:now)'),
                   dict(id=job_id, code=code, rev=revision, status='pending', now=now))
    return job_id


def _claim(store):
    with store.engine.begin() as db:
        row = db.execute(text("SELECT id,template_code,revision FROM phase2_media_jobs "
                              "WHERE status='pending' ORDER BY created LIMIT 1")).first()
        if not row:
            return None
        changed = db.execute(text("UPDATE phase2_media_jobs SET status='running',updated=:now "
                                  "WHERE id=:id AND status='pending'"),
                             dict(now=time.time(), id=row[0]))
        if changed.rowcount != 1:
            return None
        return dict(id=row[0], code=row[1], revision=row[2])


def _finish(store, job_id, error=''):
    with store.engine.begin() as db:
        db.execute(text("UPDATE phase2_media_jobs SET status=:status,error=:error,updated=:now WHERE id=:id"),
                   dict(status='failed' if error else 'done', error=error[:500], now=time.time(), id=job_id))


def _find(folder, prefix, extensions):
    for path in folder.iterdir():
        if path.name.startswith(prefix) and path.suffix.lower() in extensions:
            return path
    return None


def process(store, root, job):
    from admin.optimize_template_previews import optimize_cover
    folder = Path(root) / job['revision']
    if not folder.is_dir():
        raise RuntimeError('模板文件目录不存在')
    cover = _find(folder, '6-', {'.webp', '.jpg', '.jpeg', '.png'})
    if cover:
        optimize_cover(cover)
    store.batch_status([job['code']], 1, 'media-worker')
    with store.engine.begin() as db:
        db.execute(text('UPDATE phase2_template_settings SET '
                        'heat_base=CASE WHEN heat_base=0 THEN 10000 ELSE heat_base END, '
                        'show_heat=1, revision=revision+1 WHERE template_code=:code'),
                   dict(code=job['code']))
    service_log.event('system', '模板媒体压缩完成', context='template=' + job['code'])


def _loop(store, root):
    while True:
        try:
            job = _claim(store)
            if not job:
                time.sleep(3)
                continue
            try:
                process(store, root, job)
                _finish(store, job['id'])
            except Exception as error:  # keep the queue alive on any single failure
                _finish(store, job['id'], str(error))
                service_log.event('system', '模板媒体压缩失败',
                                  context='template=' + job['code'] + ' ' + str(error)[:200])
        except Exception:
            time.sleep(5)


def start(store, root):
    """Start the single worker thread once per process."""
    global _started
    if os.getenv('PHASE2_MEDIA_WORKER', '1') == '0':
        return False
    with _lock:
        if _started:
            return False
        _started = True
    try:
        ensure_table(store.engine)
        with store.engine.begin() as db:
            db.execute(text("UPDATE phase2_media_jobs SET status='pending' WHERE status='running'"))
    except Exception as error:
        service_log.event('system', '媒体任务表初始化失败', context=str(error)[:200])
        return False
    threading.Thread(target=_loop, args=(store, root), name='media-worker', daemon=True).start()
    return True
