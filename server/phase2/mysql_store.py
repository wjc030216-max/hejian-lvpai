"""Central phase-two records with transactional projections and indexed scheduling."""
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import text
from phase2.store import Store
from common.media_refs import transform


class Row(dict):
    def __getitem__(self,key):
        return list(self.values())[key] if isinstance(key,int) else super().__getitem__(key)


class Result:
    def __init__(self,result): self.result=result
    @property
    def rowcount(self): return getattr(self.result,'rowcount',0)
    def __iter__(self): return (Row(r) for r in self.result.mappings())
    def fetchall(self): return list(self)
    def fetchone(self):
        row=self.result.mappings().first()
        return Row(row) if row else None


class Connection:
    def __init__(self,db): self.db=db
    def execute(self,sql,params=()):
        for name in ('projects','events','leases'):
            sql=re.sub(r'\b'+name+r'\b','phase2_'+name,sql)
        sql=sql.replace('INSERT OR IGNORE','INSERT IGNORE').replace('INSERT OR REPLACE','REPLACE')
        values={}
        for i,value in enumerate(params):
            sql=sql.replace('?',':p'+str(i),1); values['p'+str(i)]=value
        return Result(self.db.execute(text(sql),values))


class MySQLStore(Store):
    def __init__(self,url,legacy_path=None):
        from common.database_schema import engine_for, ensure_schema
        self.engine=engine_for(
            url,
            pool_size=max(5, int(os.getenv('PHASE2_PROJECT_DB_POOL_SIZE', '20'))),
            max_overflow=max(0, int(os.getenv('PHASE2_PROJECT_DB_MAX_OVERFLOW', '20'))),
        )
        ensure_schema(self.engine)
        with self.engine.begin() as db:
            for sql in (
                '''CREATE TABLE IF NOT EXISTS phase2_projects (
                    id VARCHAR(64) PRIMARY KEY, owner_id VARCHAR(128) NOT NULL,
                    status VARCHAR(32) NOT NULL, runnable TINYINT NOT NULL, due DOUBLE NOT NULL,
                    paid_at DOUBLE NOT NULL, order_no VARCHAR(64) UNIQUE, payload LONGTEXT NOT NULL,
                    INDEX idx_due(runnable,due), INDEX idx_owner_paid(owner_id,paid_at))''',
                '''CREATE TABLE IF NOT EXISTS phase2_events (id VARCHAR(100) PRIMARY KEY,project_id VARCHAR(64) NOT NULL,
                    kind VARCHAR(32) NOT NULL,title TEXT NOT NULL,created DOUBLE NOT NULL,seen INT NOT NULL DEFAULT 0,
                    INDEX idx_event_seen(project_id,seen), FOREIGN KEY(project_id) REFERENCES phase2_projects(id))''',
                '''CREATE TABLE IF NOT EXISTS phase2_leases (id VARCHAR(64) PRIMARY KEY,owner VARCHAR(64) NOT NULL,expires DOUBLE NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS phase2_orders (order_no VARCHAR(64) PRIMARY KEY,project_id VARCHAR(64) NOT NULL UNIQUE,
                    owner_id VARCHAR(128) NOT NULL,amount INT NOT NULL,payment_mode VARCHAR(24) NOT NULL,paid_at DOUBLE NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES phase2_projects(id))''',
                '''CREATE TABLE IF NOT EXISTS phase2_roles (project_id VARCHAR(64) NOT NULL,role_id VARCHAR(128) NOT NULL,
                    source VARCHAR(24) NOT NULL,photo_asset_id VARCHAR(64),check_status VARCHAR(24) NOT NULL,payload TEXT NOT NULL,
                    PRIMARY KEY(project_id,role_id),FOREIGN KEY(project_id) REFERENCES phase2_projects(id))''',
                '''CREATE TABLE IF NOT EXISTS phase2_assets (id VARCHAR(64) PRIMARY KEY,project_id VARCHAR(64) NOT NULL,
                    kind VARCHAR(24) NOT NULL,reference TEXT NOT NULL,INDEX idx_asset_project(project_id),
                    FOREIGN KEY(project_id) REFERENCES phase2_projects(id))''',
                '''CREATE TABLE IF NOT EXISTS phase2_migrations (name VARCHAR(128) PRIMARY KEY,applied_at DOUBLE NOT NULL)'''):
                db.execute(text(sql))
        from common.management_schema import SCHEMA,ensure_indexes
        with self.engine.begin() as db:
            for sql in SCHEMA: db.execute(text(sql))
            ensure_indexes(db)
        if legacy_path: self.import_sqlite(legacy_path)

    @contextmanager
    def connection(self):
        with self.engine.begin() as db: yield Connection(db)
    transaction=connection

    @contextmanager
    def creation_lock(self,pid):
        with self.engine.connect() as db:
            name='draft:'+pid
            if db.execute(text('SELECT GET_LOCK(:name,5)'),{'name':name}).scalar()!=1:
                raise RuntimeError('草稿正在创建，请稍后查询')
            try: yield
            finally: db.execute(text('SELECT RELEASE_LOCK(:name)'),{'name':name})

    def get(self,pid,db=None):
        if db is None:
            with self.connection() as conn:
                row=conn.execute('SELECT payload FROM projects WHERE id=?',(pid,)).fetchone()
        else:
            row=db.execute('SELECT payload FROM projects WHERE id=? FOR UPDATE',(pid,)).fetchone()
        return transform(json.loads(row[0])) if row else None

    def put(self,p,db=None):
        if db is None:
            with self.transaction() as conn: return self.put(p,conn)
        from phase2.participation import user_roles
        users={r['person_uid'] for r in user_roles(p)}
        check=any(r.get('check_status')=='checking' for r in user_roles(p))
        runnable=p['status'] not in ('pending_payment','completed','terminated','refund_pending',
                                     'refund_failed','refunded','archived','expired') and (
            p['status'] not in ('draft','uploading') or check)
        owner=p.get('wx_openid') or 'local-user'
        values=(owner,p['status'],int(runnable),max(p.get('next_run_at',0),p.get('last_tick_at',0)),
                p.get('paid_at',0),p.get('order_no'),json.dumps(transform(p,True),ensure_ascii=False))
        exists=db.execute('SELECT payload FROM projects WHERE id=? FOR UPDATE',(p['project_id'],)).fetchone()
        old=transform(json.loads(exists[0])) if exists else {}
        old_roles={r['character_id']:r for r in old.get('roles',[])}
        old_users={r['person_uid'] for r in user_roles(old)} if old else set()
        if exists:
            db.execute('UPDATE projects SET owner_id=?,status=?,runnable=?,due=?,paid_at=?,order_no=?,payload=? WHERE id=?',values+(p['project_id'],))
        else:
            db.execute('INSERT INTO projects(owner_id,status,runnable,due,paid_at,order_no,payload,id) VALUES(?,?,?,?,?,?,?,?)',values+(p['project_id'],))
        if p.get('order_no') and any(old.get(k)!=p.get(k) for k in ('order_no','payment_mode','paid_at','amount_paid')):
            db.execute('''INSERT INTO phase2_orders VALUES(?,?,?,?,?,?) ON DUPLICATE KEY UPDATE
                payment_mode=VALUES(payment_mode),amount=VALUES(amount),paid_at=VALUES(paid_at)''',
                (p['order_no'],p['project_id'],owner,p.get('amount_paid',0),p.get('payment_mode','unconfigured'),p.get('paid_at',0)))
        for r in p.get('roles',[]):
            if r==old_roles.get(r['character_id']) and (r['person_uid'] in users)==(r['person_uid'] in old_users):
                continue
            db.execute('''INSERT INTO phase2_roles VALUES(?,?,?,?,?,?) ON DUPLICATE KEY UPDATE source=VALUES(source),
                photo_asset_id=VALUES(photo_asset_id),check_status=VALUES(check_status),payload=VALUES(payload)''',
                (p['project_id'],r['character_id'],'user_face' if r['person_uid'] in users else 'template',r.get('photo_asset_id'),
                 r.get('check_status','empty'),json.dumps(transform(r,True),ensure_ascii=False)))
            if r.get('photo_asset_id'): self.asset(db,r['photo_asset_id'],p['project_id'],'upload',r['photo_url'])
        for kind,key,id_key in [('portrait','portrait_url','portrait_asset_id'),('video','video_url','video_task_id')]:
            if p.get(key) and p.get(id_key) and (p.get(key)!=old.get(key) or p.get(id_key)!=old.get(id_key)):
                self.asset(db,p[id_key],p['project_id'],kind,p[key])

    def update(self, project_id, patch):
        if set(patch)=={'last_tick_at'}:
            with self.transaction() as db:
                db.execute("""UPDATE projects SET payload=JSON_SET(payload,'$.last_tick_at',?,'$.updated_at',?),
                    due=GREATEST(COALESCE(JSON_EXTRACT(payload,'$.next_run_at')+0,0),?)
                    WHERE id=? AND status NOT IN ('completed','terminated','refund_pending','refund_failed','refunded','archived','expired')""",
                    (patch['last_tick_at'],time.time(),patch['last_tick_at'],project_id))
            return self.get(project_id)
        return super().update(project_id,patch)

    def asset(self,db,aid,pid,kind,reference):
        db.execute('''INSERT INTO phase2_assets VALUES(?,?,?,?) ON DUPLICATE KEY UPDATE reference=VALUES(reference)''',
            (aid,pid,kind,transform(reference,True)))

    def all(self):
        with self.connection() as db:
            return [transform(json.loads(r[0])) for r in db.execute('SELECT payload FROM projects')]

    def due(self,limit=8,photos=None):
        clause='' if photos is None else (" AND status IN ('draft','uploading')" if photos else " AND status NOT IN ('draft','uploading')")
        with self.connection() as db:
            return [transform(json.loads(r[0])) for r in db.execute(
                'SELECT payload FROM projects WHERE runnable=1 AND due<=?'+clause+' ORDER BY due,id LIMIT ?',(time.time(),limit))]

    def list_works(self,owner,limit=30,offset=0):
        with self.connection() as db:
            total=db.execute('SELECT COUNT(*) FROM projects WHERE owner_id=? AND order_no IS NOT NULL',(owner,)).fetchone()[0]
            items=[transform(json.loads(r[0])) for r in db.execute('''SELECT payload FROM projects WHERE owner_id=?
                AND order_no IS NOT NULL ORDER BY paid_at DESC,id LIMIT ? OFFSET ?''',(owner,limit,offset))]
        return items,total

    def list_watch_states(self,owner,limit=100):
        with self.connection() as db:
            total=db.execute(
                'SELECT COUNT(*) FROM projects WHERE owner_id=? AND order_no IS NOT NULL',
                (owner,),
            ).fetchone()[0]
            rows=db.execute(
                "SELECT id,"
                "COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.status')),''),"
                "COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.portrait_url')),''),"
                "COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.video_url')),''),"
                "COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.template.video_name')),''),"
                "COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.error')),'') "
                "FROM projects WHERE owner_id=? AND order_no IS NOT NULL "
                "ORDER BY paid_at DESC,id LIMIT ?",
                (owner,limit),
            ).fetchall()
        return [{
            'project_id':row[0],'status':row[1] or '',
            'portrait_url':bool(row[2]),'video_url':bool(row[3]),
            'video_name':row[4] or '','error':row[5] or '',
        } for row in rows],total

    def claim(self,pid,owner):
        with self.transaction() as db:
            db.execute('INSERT INTO leases VALUES(?,?,?) ON DUPLICATE KEY UPDATE id=VALUES(id)',(pid,owner,0))
            row=db.execute('SELECT * FROM leases WHERE id=? FOR UPDATE',(pid,)).fetchone()
            if row['expires']>time.time(): return False
            db.execute('UPDATE leases SET owner=?,expires=? WHERE id=?',(owner,time.time()+600,pid))
            return True

    def works_by_ids(self,owner,ids):
        if not ids: return []
        with self.connection() as db:
            return [transform(json.loads(r[0])) for r in db.execute('SELECT payload FROM projects WHERE owner_id=? AND order_no IS NOT NULL AND id IN ('+','.join('?' for _ in ids)+')',(owner,*ids))]

    def unread(self,owner,limit=100):
        with self.connection() as db:
            return [dict(r) for r in db.execute('''SELECT e.* FROM events e JOIN projects p ON p.id=e.project_id
                WHERE p.owner_id=? AND e.seen=0 ORDER BY e.created DESC LIMIT ?''',(owner,limit))]

    def asset_project(self,aid):
        with self.connection() as db:
            row=db.execute('SELECT project_id FROM phase2_assets WHERE id=?',(aid,)).fetchone()
            return row[0] if row else None

    def projects_awaiting_refund(self,limit=20):
        with self.connection() as db:
            rows=list(db.execute('''SELECT p.payload FROM phase2_projects p
                LEFT JOIN phase2_refunds r ON r.project_id=p.id
                WHERE p.status='refund_pending' AND r.project_id IS NULL
                LIMIT ?''',(limit,)))
            return [transform(json.loads(row[0])) for row in rows]

    def pending_media_cleanup(self,limit=10):
        with self.connection() as db:
            rows=list(db.execute('''SELECT payload FROM projects
                WHERE status='refunded'
                AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.media_cleanup')),'')<>'done'
                AND COALESCE(CAST(JSON_EXTRACT(payload,'$.media_cleanup_next_at') AS DECIMAL(20,3)),0)<=?
                ORDER BY COALESCE(CAST(JSON_EXTRACT(payload,'$.refunded_at') AS DECIMAL(20,3)),0)
                LIMIT ?''',(time.time(),limit)))
            return [transform(json.loads(row[0])) for row in rows]

    def pending_subscriptions(self,limit=20):
        with self.connection() as db:
            rows=list(db.execute('''SELECT payload FROM projects
                WHERE status='completed'
                AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.subscribe_openid')),'')<>''
                AND COALESCE(CAST(JSON_EXTRACT(payload,'$.subscribe_sent') AS UNSIGNED),0)=0
                AND COALESCE(CAST(JSON_EXTRACT(payload,'$.subscribe_terminal') AS UNSIGNED),0)=0
                AND COALESCE(CAST(JSON_EXTRACT(payload,'$.subscribe_next_at') AS DECIMAL(20,3)),0)<=?
                ORDER BY COALESCE(CAST(JSON_EXTRACT(payload,'$.completed_at') AS DECIMAL(20,3)),0)
                LIMIT ?''',(time.time(),limit)))
            return [transform(json.loads(row[0])) for row in rows]

    def projects_due_retention(self,limit=20):
        from phase2.retention_service import RETENTION_SECONDS
        cutoff=time.time()
        active=("'portrait_submit','portrait_unknown','portrait_processing','portrait_saving',"
                "'portrait_retry','video_submit','video_unknown','video_processing',"
                "'video_retry','needs_review','refund_pending','refund_failed'")
        with self.connection() as db:
            rows=list(db.execute('''SELECT payload FROM projects
                WHERE status NOT IN ('''+active+''')
                AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.retention_cleanup')),'')<>'done'
                AND COALESCE(CAST(JSON_EXTRACT(payload,'$.retention_next_at') AS DECIMAL(20,3)),0)<=?
                AND (
                    COALESCE(CAST(JSON_EXTRACT(payload,'$.retention_expires_at') AS DECIMAL(20,3)),0)>0
                    AND CAST(JSON_EXTRACT(payload,'$.retention_expires_at') AS DECIMAL(20,3))<=?
                    OR COALESCE(CAST(JSON_EXTRACT(payload,'$.retention_expires_at') AS DECIMAL(20,3)),0)=0
                    AND COALESCE(CAST(JSON_EXTRACT(payload,'$.created_at') AS DECIMAL(20,3)),0)<=?
                )
                ORDER BY COALESCE(CAST(JSON_EXTRACT(payload,'$.retention_expires_at') AS DECIMAL(20,3)),created_at)
                LIMIT ?''',(cutoff,cutoff,cutoff-RETENTION_SECONDS,limit)))
            return [transform(json.loads(row[0])) for row in rows]

    def task_records_for_project(self,project_id,runtime_code=''):
        with self.connection() as db:
            rows=list(db.execute('''SELECT payload FROM phase2_tasks
                WHERE run_id LIKE ? OR payload LIKE ?''',
                (project_id+'%','%'+(runtime_code or '__none__')+'%')))
        records=[]
        for row in rows:
            try: records.append(transform(json.loads(row[0])))
            except (TypeError,ValueError): continue
        return records

    def delete_project_process(self,project_id,runtime_code,tasks,db):
        db.execute('DELETE FROM phase2_roles WHERE project_id=?',(project_id,))
        db.execute('DELETE FROM phase2_assets WHERE project_id=?',(project_id,))
        db.execute('DELETE FROM phase2_events WHERE project_id=?',(project_id,))
        params=(project_id+'%','%'+(runtime_code or '__none__')+'%')
        db.execute('''DELETE FROM phase2_task_events WHERE task_id IN
            (SELECT id FROM phase2_tasks WHERE run_id LIKE ? OR payload LIKE ?)''',params)
        db.execute('DELETE FROM phase2_tasks WHERE run_id LIKE ? OR payload LIKE ?',params)

    def import_sqlite(self,path):
        if not Path(path).is_file(): return
        with self.engine.connect() as lock:
            if lock.execute(text("SELECT GET_LOCK('phase2-sqlite-migration',30)")).scalar()!=1:
                raise RuntimeError('Waiting for database migration')
            try:
                with self.transaction() as db:
                    if db.execute("SELECT name FROM phase2_migrations WHERE name='sqlite-v1'").fetchone(): return
                    source=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
                    try:
                        for raw, in source.execute('SELECT payload FROM projects'):
                            p=json.loads(raw)
                            if not self.get(p['project_id'],db): self.put(p,db)
                        for row in source.execute('SELECT * FROM events'):
                            if self.get(row[1],db): db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)',row)
                        db.execute('INSERT INTO phase2_migrations VALUES(?,?)',('sqlite-v1',time.time()))
                    finally: source.close()
            finally: lock.execute(text("SELECT RELEASE_LOCK('phase2-sqlite-migration')"))
