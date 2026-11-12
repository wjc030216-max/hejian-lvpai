"""Durable local phase-two records. SQLite lives in the mounted upload volume."""
import copy
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
                    title TEXT NOT NULL, created REAL NOT NULL, seen INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS leases(id TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_order_no
                    ON projects(json_extract(payload, '$.order_no'))
                    WHERE json_extract(payload, '$.order_no') IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_events_project_seen ON events(project_id, seen);
            """)

        from common.management_schema import SCHEMA,INDEXES
        with self.connection() as db:
            for sql in SCHEMA: db.execute(sql)
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table,name,columns in INDEXES:
                if table in tables:
                    db.execute("CREATE INDEX IF NOT EXISTS "+name+" ON "+table+"("+columns+")")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    @contextmanager
    def creation_lock(self, project_id):
        yield  # SQLite BEGIN IMMEDIATE already serializes draft creation.

    def get(self, project_id, db=None):
        if db is None:
            with self.connection() as conn:
                return self.get(project_id, conn)
        row = db.execute('SELECT payload FROM projects WHERE id=?', (project_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def find_by_order_no(self, order_no):
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM projects WHERE json_extract(payload, '$.order_no')=?",
                (order_no,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def project_for_media(self, kind, result_id):
        field = 'video_task_id' if kind == 'video' else 'portrait_asset_id'
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM projects WHERE json_extract(payload, '$."
                + field + "')=? LIMIT 1",
                (result_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def all(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM projects')]

    def due(self, limit=8, photos=None):
        from phase2.participation import user_roles
        return sorted([p for p in self.all() if p.get('next_run_at',0)<=time.time()
            and (photos is None or (p['status'] in ('draft','uploading')) == photos)
            and p['status'] not in ('pending_payment','completed','terminated','refund_pending',
                                    'refund_failed','refunded','archived','expired')
            and (p['status'] not in ('draft','uploading') or any(r.get('check_status')=='checking' for r in user_roles(p)))],
            key=lambda p:p.get('last_tick_at',0))[:limit]

    def list_works(self, owner, limit=30, offset=0):
        rows=sorted([p for p in self.all() if (p.get('wx_openid') or 'local-user')==owner and p.get('order_no')],
                    key=lambda p:p.get('paid_at',0),reverse=True)
        return rows[offset:offset+limit],len(rows)

    def list_watch_states(self, owner, limit=100):
        """Return only the fields needed by the foreground transition watcher."""
        with self.connection() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM projects "
                "WHERE COALESCE(json_extract(payload,'$.wx_openid'),'local-user')=? "
                "AND json_extract(payload,'$.order_no') IS NOT NULL",
                (owner,),
            ).fetchone()[0]
            rows = db.execute(
                "SELECT id,"
                "COALESCE(json_extract(payload,'$.status'),''),"
                "COALESCE(json_extract(payload,'$.portrait_url'),''),"
                "COALESCE(json_extract(payload,'$.video_url'),''),"
                "COALESCE(json_extract(payload,'$.template.video_name'),''),"
                "COALESCE(json_extract(payload,'$.error'),'') "
                "FROM projects "
                "WHERE COALESCE(json_extract(payload,'$.wx_openid'),'local-user')=? "
                "AND json_extract(payload,'$.order_no') IS NOT NULL "
                "ORDER BY COALESCE(json_extract(payload,'$.paid_at'),0) DESC,id LIMIT ?",
                (owner, limit),
            ).fetchall()
        return [{
            'project_id': row[0], 'status': row[1] or '',
            'portrait_url': bool(row[2]), 'video_url': bool(row[3]),
            'video_name': row[4] or '', 'error': row[5] or '',
        } for row in rows], total

    def unread(self, owner, limit=100):
        with self.connection() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM events WHERE seen=0 ORDER BY created DESC')]
        return [r for r in rows if (self.get(r['project_id']) or {}).get('wx_openid','local-user')==owner][:limit]

    def works_by_ids(self, owner, ids):
        records=[self.get(pid) for pid in set(ids)]
        return [p for p in records if p and p.get('wx_openid','local-user')==owner and p.get('order_no')]

    def put(self, record, db=None):
        if db is None:
            with self.transaction() as conn:
                return self.put(record, conn)
        db.execute('INSERT INTO projects VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                   (record['project_id'], json.dumps(record, ensure_ascii=False)))

    def update(self, project_id, patch):
        with self.transaction() as db:
            record = self.get(project_id, db)
            if not record:
                return None
            # A terminal order cannot be resurrected by a late response.
            if record.get('status') in ('completed', 'terminated', 'refund_pending', 'refund_failed', 'refunded', 'archived', 'expired'):
                return record
            record.update(copy.deepcopy(patch), updated_at=time.time())
            self.put(record, db)
            return record

    def event(self, record, kind, title, db):
        db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,0)',
                   (record['project_id'] + ':' + kind + ':' + uuid.uuid4().hex[:12],
                    record['project_id'], kind,
                    '《' + record['template']['video_name'] + '》' + title, time.time()))

    def portrait_saved(self, project_id, patch):
        with self.transaction() as db:
            record = self.get(project_id, db)
            if record.get('status') in ('terminated', 'refund_pending', 'refund_failed', 'refunded', 'completed', 'archived', 'expired'):
                return
            # Duplicate storage acknowledgements must not rewind a video already submitted.
            if record.get('stage') == 'video':
                return
            record.update(patch, status='video_submit', stage='video', title='照片已完成，视频准备中',
                          portrait_attempts=record.get('attempt', 1), attempt=1, next_run_at=0,
                          updated_at=time.time())
            self.put(record, db)
            self.event(record, 'portrait', '定妆照已完成，视频正在生成', db)

    def complete(self, project_id, url):
        with self.transaction() as db:
            record = self.get(project_id, db)
            if record.get('status') == 'completed':
                return
            if record.get('status') in ('terminated', 'refund_pending', 'refund_failed', 'refunded', 'archived', 'expired'):
                record['late_video_url'] = url
                self.put(record, db)
                return
            record.update(status='completed', title='作品已完成', video_url=url,
                          completed_at=time.time(), error='', progress=100)
            self.put(record, db)
            self.event(record, 'video', '视频已完成，整包作品已保存', db)

    def mark_subscribed(self, project_id):
        self.mark_subscribe_result(project_id, True)

    def mark_subscribe_result(self, project_id, success, error=''):
        with self.transaction() as db:
            record = self.get(project_id, db)
            if not record:
                return
            attempts = int(record.get('subscribe_attempts') or 0)
            if success:
                record.update(subscribe_sent=True, subscribe_error='', subscribe_next_at=0,
                              subscribe_terminal=False)
            else:
                attempts += 1
                terminal = attempts >= max(1, int(os.getenv('PHASE2_SUBSCRIBE_MAX_ATTEMPTS', '8')))
                record.update(subscribe_sent=False, subscribe_attempts=attempts,
                              subscribe_error=str(error or '')[:500],
                              subscribe_terminal=terminal,
                              subscribe_next_at=0 if terminal else
                              time.time() + min(3600, 30 * (2 ** min(attempts, 7))))
            self.put(record, db)

    def pending_subscriptions(self, limit=20):
        with self.connection() as db:
            rows = db.execute(
                "SELECT payload FROM projects "
                "WHERE json_extract(payload, '$.status')='completed' "
                "AND COALESCE(json_extract(payload, '$.subscribe_openid'), '')<>'' "
                "AND COALESCE(json_extract(payload, '$.subscribe_sent'), 0)=0 "
                "AND COALESCE(json_extract(payload, '$.subscribe_terminal'), 0)=0 "
                "AND COALESCE(json_extract(payload, '$.subscribe_next_at'), 0)<=? "
                "ORDER BY json_extract(payload, '$.completed_at') LIMIT ?",
                (time.time(), limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def projects_due_retention(self, limit=20):
        from phase2.retention_service import expired
        blocked = {
            'portrait_submit', 'portrait_unknown', 'portrait_processing',
            'portrait_saving', 'portrait_retry', 'video_submit', 'video_unknown',
            'video_processing', 'video_retry', 'needs_review',
            'refund_pending', 'refund_failed',
        }
        now = time.time()
        return [record for record in self.all()
                if record.get('status') not in blocked
                and record.get('retention_cleanup') != 'done'
                and record.get('retention_next_at', 0) <= now
                and expired(record, now)][:limit]

    def task_records_for_project(self, project_id, runtime_code=''):
        with self.connection() as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='phase2_tasks'"
            ).fetchone()
            if not exists:
                return []
            rows = db.execute(
                "SELECT payload FROM phase2_tasks WHERE run_id LIKE ? OR payload LIKE ?",
                (project_id + '%', '%' + (runtime_code or '__none__') + '%'),
            ).fetchall()
        records = []
        for row in rows:
            try:
                records.append(json.loads(row[0]))
            except (TypeError, ValueError):
                continue
        return records

    def delete_project_process(self, project_id, runtime_code, tasks, db):
        db.execute('DELETE FROM events WHERE project_id=?', (project_id,))
        if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='phase2_tasks'").fetchone():
            return
        params = (project_id + '%', '%' + (runtime_code or '__none__') + '%')
        db.execute('DELETE FROM phase2_task_events WHERE task_id IN '
                   '(SELECT id FROM phase2_tasks WHERE run_id LIKE ? OR payload LIKE ?)', params)
        db.execute('DELETE FROM phase2_tasks WHERE run_id LIKE ? OR payload LIKE ?', params)

    def mark_retention_result(self, project_id, cleaned, error=''):
        with self.transaction() as db:
            record = self.get(project_id, db)
            if not record:
                return
            attempts = int(record.get('retention_cleanup_attempts') or 0) + 1
            record.update(
                retention_cleanup='done' if cleaned else 'failed',
                retention_cleanup_attempts=attempts,
                retention_cleanup_error='' if cleaned else str(error or '')[:500],
                retention_next_at=0 if cleaned else time.time() + min(3600, 60 * (2 ** min(attempts, 6))),
                updated_at=time.time(),
            )
            self.put(record, db)

    def failed_attempt(self, project_id, error):
        with self.transaction() as db:
            record = self.get(project_id, db)
            if record.get('status') in ('completed', 'terminated', 'refund_pending', 'refund_failed', 'refunded', 'archived', 'expired'):
                return record
            record['error'] = error
            record[record['stage'] + '_attempts'] = record.get('attempt', 1)
            if record.get('attempt', 1) >= 3:
                record.update(status='terminated',
                              title='制作未完成',
                              refund_status='not_charged' if record.get('payment_mode') == 'dev-bypass' else 'available',
                              description='开发订单已终止，无实际扣款'
                              if record.get('payment_mode') == 'dev-bypass'
                              else '生成失败，可选择重新生成或申请全额退款',
                              terminated_at=time.time())
                self.event(record, 'failure', record['description'], db)
            else:
                record.update(status=record['stage'] + '_retry', attempt=record.get('attempt', 1) + 1,
                              title='正在自动重试', next_run_at=time.time()+5)
            self.put(record, db)
            return record

    def review_timeout(self, project_id, error):
        with self.transaction() as db:
            record = self.get(project_id, db)
            if not record or record.get('status') in (
                    'completed', 'terminated', 'refund_pending', 'refund_failed',
                    'refunded', 'archived', 'expired'):
                return record
            record.update(
                status='terminated',
                title='制作未完成',
                error=str(error or '制作超过处理时限'),
                description='制作超过处理时限，已停止自动恢复，可选择重新生成或申请全额退款',
                refund_status=('not_charged' if record.get('payment_mode') == 'dev-bypass'
                               else 'available'),
                terminated_at=time.time(),
                next_run_at=0,
                updated_at=time.time(),
            )
            self.put(record, db)
            self.event(record, 'failure', record['description'], db)
            return record

    def refund_for_project(self, project_id, db=None):
        if db is None:
            with self.connection() as conn:
                return self.refund_for_project(project_id, conn)
        row = db.execute('SELECT * FROM phase2_refunds WHERE project_id=?', (project_id,)).fetchone()
        return dict(row) if row else None

    def get_refund(self, refund_no, db=None):
        if db is None:
            with self.connection() as conn:
                return self.get_refund(refund_no, conn)
        row = db.execute('SELECT * FROM phase2_refunds WHERE refund_no=?', (refund_no,)).fetchone()
        return dict(row) if row else None

    def put_refund(self, record, db=None):
        if db is None:
            with self.transaction() as conn:
                return self.put_refund(record, conn)
        existing = self.refund_for_project(record['project_id'], db)
        fields = ('refund_no', 'project_id', 'order_no', 'amount_fen', 'status', 'attempts',
                  'next_run', 'provider_order_no', 'provider_trxid', 'provider_trxstatus',
                  'error_code', 'error_message', 'request_payload', 'response_payload',
                  'requested_at', 'updated_at', 'finished_at', 'operator')
        values = tuple(record.get(field) for field in fields)
        if existing:
            db.execute('UPDATE phase2_refunds SET refund_no=?,order_no=?,amount_fen=?,status=?,attempts=?,'
                       'next_run=?,provider_order_no=?,provider_trxid=?,provider_trxstatus=?,'
                       'error_code=?,error_message=?,request_payload=?,response_payload=?,'
                       'requested_at=?,updated_at=?,finished_at=?,operator=? WHERE project_id=?',
                       values[0:1] + values[2:] + (record['project_id'],))
        else:
            db.execute('INSERT INTO phase2_refunds(refund_no,project_id,order_no,amount_fen,status,attempts,'
                       'next_run,provider_order_no,provider_trxid,provider_trxstatus,error_code,error_message,'
                       'request_payload,response_payload,requested_at,updated_at,finished_at,operator) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', values)
        return record

    def due_refunds(self, limit=4):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM phase2_refunds WHERE status IN ('pending','retry','processing') "
                              "AND next_run<=? ORDER BY next_run,requested_at LIMIT ?",
                              (time.time(), limit)).fetchall()
            return [dict(row) for row in rows]

    def pending_media_cleanup(self, limit=10):
        with self.connection() as db:
            rows = db.execute(
                "SELECT payload FROM projects WHERE json_extract(payload, '$.status')='refunded' "
                "AND COALESCE(json_extract(payload, '$.media_cleanup'), '')<>'done' "
                "AND COALESCE(json_extract(payload, '$.media_cleanup_next_at'), 0)<=? "
                "ORDER BY json_extract(payload, '$.refunded_at') LIMIT ?",
                (time.time(), limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def mark_media_cleanup(self, project_id, cleaned, error=''):
        with self.transaction() as db:
            record = self.get(project_id, db)
            if not record:
                return
            attempts = int(record.get('media_cleanup_attempts') or 0) + 1
            record.update(
                media_cleanup='done' if cleaned else 'failed',
                media_cleanup_attempts=attempts,
                media_cleanup_error='' if cleaned else str(error or '')[:500],
                media_cleanup_next_at=0 if cleaned else time.time() + min(3600, 30 * (2 ** min(attempts, 7))),
                media_cleaned_at=time.time() if cleaned else record.get('media_cleaned_at', 0),
                updated_at=time.time(),
            )
            self.put(record, db)

    def recover_stale_refunds(self, age=600):
        cutoff = time.time() - age
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM phase2_refunds WHERE status='submitting' "
                              "AND updated_at<?", (cutoff,)).fetchall()
            for row in rows:
                record = dict(row)
                record.update(status='needs_review',
                              error_message='退款请求结果未确认，已停止自动重发',
                              updated_at=time.time())
                project = self.get(record['project_id'], db)
                if project:
                    project.update(status='refund_pending', refund_status='needs_review',
                                   title='退款状态待核实',
                                   description='退款请求结果未确认，请勿重复提交')
                    self.put(project, db)
                self.put_refund(record, db)
            return len(rows)

    def projects_awaiting_refund(self, limit=20):
        with self.connection() as db:
            rows = db.execute(
                "SELECT payload FROM projects WHERE json_extract(payload, '$.status')='refund_pending' "
                "AND NOT EXISTS (SELECT 1 FROM phase2_refunds r WHERE r.project_id="
                "json_extract(projects.payload, '$.project_id')) LIMIT ?",
                (limit,),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

    def claim(self, project_id, owner):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM leases WHERE id=?', (project_id,)).fetchone()
            if row and row['expires'] > time.time():
                return False
            db.execute('INSERT OR REPLACE INTO leases VALUES (?,?,?)', (project_id, owner, time.time()+600))
            return True

    def release(self, project_id, owner):
        with self.connection() as db:
            db.execute('DELETE FROM leases WHERE id=? AND owner=?', (project_id, owner))
