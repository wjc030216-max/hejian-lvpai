"""Durable task/step journal. Redis is never authoritative for phase-two jobs."""
import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


class UnknownSubmission(RuntimeError):
    pass


class CloudFailed(RuntimeError):
    pass


class TaskPending(RuntimeError):
    """One successful status read; release the worker until the next check."""
    def __init__(self, seconds=15):
        self.seconds = seconds


class TaskJournal:
    def __init__(self, url=None):
        from common.database_schema import engine_for, ensure_schema
        database_url = url or os.environ['AITRAVEL_DATABASE_URL']
        options = {}
        if database_url.startswith('mysql'):
            options = {
                'pool_size': max(5, int(os.getenv('PHASE2_TASK_DB_POOL_SIZE', '24'))),
                'max_overflow': max(0, int(os.getenv('PHASE2_TASK_DB_MAX_OVERFLOW', '16'))),
            }
        self.engine = engine_for(database_url, **options)
        ensure_schema(self.engine)
        self.mysql = self.engine.dialect.name == 'mysql'
        self._locks = {}
        self._mutex = threading.Lock()

    @contextmanager
    def lock(self, key, wait=0):
        name = 'p2:' + hashlib.sha256(key.encode()).hexdigest()[:56]
        if self.mysql:
            with self.engine.connect() as db:
                acquired = db.execute(text('SELECT GET_LOCK(:key,:wait)'), {'key':name,'wait':wait}).scalar() == 1
                try:
                    yield acquired
                finally:
                    if acquired:
                        db.execute(text('SELECT RELEASE_LOCK(:key)'), {'key':name})
        else:
            with self._mutex:
                lock = self._locks.setdefault(name, threading.Lock())
            acquired = lock.acquire(timeout=wait)
            try:
                yield acquired
            finally:
                if acquired:
                    lock.release()

    def get(self, task_id):
        with self.engine.connect() as db:
            row = db.execute(text('SELECT payload FROM phase2_tasks WHERE id=:id'), {'id':task_id}).first()
            return json.loads(row[0]) if row else None

    def latest(self, kind, run_id):
        with self.engine.connect() as db:
            row = db.execute(text('SELECT payload FROM phase2_tasks WHERE kind=:kind AND run_id=:run'), {'kind':kind,'run':run_id}).first()
            return json.loads(row[0]) if row else None

    def put(self, record):
        params = dict(id=record['task_id'], kind=record['type'], run=record['wx_openid'],
                      status=record['status'], due=record.get('next_run',0), payload=json.dumps(record,ensure_ascii=False))
        suffix = ('ON DUPLICATE KEY UPDATE status=VALUES(status),next_run=VALUES(next_run),payload=VALUES(payload)' if self.mysql else
                  'ON CONFLICT(id) DO UPDATE SET status=excluded.status,next_run=excluded.next_run,payload=excluded.payload')
        with self.engine.begin() as db:
            old=db.execute(text('SELECT status FROM phase2_tasks WHERE id=:id'),{'id':params['id']}).scalar()
            if old!=record['status']:
                db.execute(text('INSERT INTO phase2_task_events(id,task_id,kind,status,message,created) VALUES(:id,:task,:kind,:status,:message,:created)'),
                    dict(id=uuid.uuid4().hex,task=params['id'],kind=record['type'],status=record['status'],message=record.get('error','')[:400],created=time.time()))
            db.execute(text('INSERT INTO phase2_tasks(id,kind,run_id,status,next_run,payload) VALUES(:id,:kind,:run,:status,:due,:payload) '+suffix),params)

    def create(self, kind, run_id, data):
        def existing():
            old = self.latest(kind,run_id)
            if old and old['template_code'] != data['template_code']:
                raise ValueError('同次制作不能更换模板')
            return old
        old = existing()
        if old:
            return old, False
        task_id = hashlib.sha256((kind+':'+run_id).encode()).hexdigest()[:32]
        now = time.time()
        record = dict(data,task_id=task_id,type=kind,wx_openid=run_id,status='pending',
                      steps={},attempt=1,created_at=now,
                      deadline_at=now+max(60, int(os.getenv('PHASE2_TASK_MAX_SECONDS', '7200'))))
        try:
            with self.engine.begin() as db:
                db.execute(text(
                    'INSERT INTO phase2_tasks(id,kind,run_id,status,next_run,payload) '
                    'VALUES(:id,:kind,:run,:status,:due,:payload)'
                ), dict(id=record['task_id'], kind=kind, run=run_id, status='pending',
                        due=0, payload=json.dumps(record, ensure_ascii=False)))
                db.execute(text(
                    'INSERT INTO phase2_task_events(id,task_id,kind,status,message,created) '
                    'VALUES(:id,:task,:kind,:status,:message,:created)'
                ), dict(id=uuid.uuid4().hex, task=record['task_id'], kind=kind,
                        status='pending', message='', created=now))
            return record, True
        except IntegrityError:
            old = existing()
            if old:
                return old, False
            raise

    def due(self, kind, limit=4):
        with self.engine.connect() as db:
            return [json.loads(row[0]) for row in db.execute(text('''SELECT payload FROM phase2_tasks
                WHERE kind=:kind AND status IN ('pending','running','recovering') AND next_run<=:now
                AND CASE WHEN lease_until IS NULL THEN 1 ELSE lease_until<=:now END
                ORDER BY next_run,id LIMIT :limit'''),dict(kind=kind,now=time.time(),limit=limit))]

    def claim(self, task_id, owner=None):
        """Claim one active task without holding a database connection during cloud I/O."""
        owner = owner or uuid.uuid4().hex
        now = time.time()
        lease_seconds = max(60, int(os.getenv('PHASE2_TASK_LEASE_SECONDS', '3600')))
        local_lock = None
        if not self.mysql:
            with self._mutex:
                local_lock = self._locks.setdefault('claim:' + task_id, threading.Lock())
            if not local_lock.acquire(blocking=False):
                return None
        try:
            with self.engine.begin() as db:
                suffix = ' FOR UPDATE' if self.mysql else ''
                row = db.execute(text(
                    'SELECT status,lease_until,payload FROM phase2_tasks '
                    'WHERE id=:id' + suffix
                ), {'id': task_id}).first()
                if not row or row[0] not in ('pending', 'running', 'recovering'):
                    return None
                if float(row[1] or 0) > now:
                    return None
                db.execute(text(
                    'UPDATE phase2_tasks SET lease_owner=:owner,lease_until=:until WHERE id=:id'
                ), {'owner': owner, 'until': now + lease_seconds, 'id': task_id})
                return json.loads(row[2])
        finally:
            if local_lock is not None:
                local_lock.release()

    def release(self, task_id, owner):
        with self.engine.begin() as db:
            db.execute(text(
                'UPDATE phase2_tasks SET lease_owner=NULL,lease_until=0 '
                'WHERE id=:id AND lease_owner=:owner'
            ), {'id': task_id, 'owner': owner})

    def video_inflight(self, excluding=None):
        with self.engine.connect() as db:
            sql="SELECT payload FROM phase2_tasks WHERE kind='video' AND status IN ('running','recovering','needs_review')"
            params={}
            if excluding:
                sql += ' AND id<>:id'
                params['id']=excluding
            rows=db.execute(text(sql),params)
            steps=[json.loads(row[0]).get('steps',{}) for row in rows]
            count=0
            for steps_for_task in steps:
                # Legacy single-group task.
                if (steps_for_task.get('video_submit',{}).get('status') in ('done','submitting')
                        and steps_for_task.get('video_poll',{}).get('status')!='done'):
                    count += 1
                # Group-scoped dual-video task; every provider request consumes one slot.
                for name,step in steps_for_task.items():
                    if not name.startswith('video_submit:') or step.get('status') not in ('done','submitting'):
                        continue
                    code=name.split(':',1)[1]
                    if steps_for_task.get('video_poll:'+code,{}).get('status')!='done':
                        count += 1
            return count

    def step(self, record, name, operation, billable=False):
        checkpoint = record.setdefault('steps',{}).get(name,{})
        if checkpoint.get('status') == 'done':
            return checkpoint['result']
        if billable and checkpoint.get('status') == 'submitting':
            raise UnknownSubmission('云端提交结果待核实，已停止自动重发')
        record['steps'][name] = {'status':'submitting' if billable else 'running','started_at':time.time()}
        self.put(record)  # Must succeed BEFORE the network request.
        result = operation()
        record['steps'][name] = {'status':'done','result':result,'finished_at':time.time()}
        self.put(record)
        return result

    def complete_reserved_submission(self, record, name, operation):
        """Complete a billable step reserved by the caller before this call."""
        checkpoint = record.setdefault('steps', {}).get(name, {})
        if checkpoint.get('status') == 'done':
            return checkpoint['result']
        if checkpoint.get('status') != 'submitting':
            raise UnknownSubmission('云端提交预留状态无效，已停止自动重发')
        result = operation()
        record['steps'][name] = {
            'status': 'done', 'result': result, 'finished_at': time.time(),
        }
        self.put(record)
        return result

    def retry(self, task_id, run_id, requested_attempt=None):
        with self.lock(task_id, 0) as acquired:
            if not acquired:
                raise ValueError('原任务仍在处理')
            record = self.get(task_id)
            if not record or record['wx_openid'] != run_id:
                raise ValueError('任务不存在')
            if requested_attempt is not None:
                if not isinstance(requested_attempt,int) or isinstance(requested_attempt,bool) or not 2<=requested_attempt<=3:
                    raise ValueError('重试次数无效')
                if record.get('attempt',1)>=requested_attempt:
                    return record
                if requested_attempt!=record.get('attempt',1)+1:
                    raise ValueError('重试顺序无效')
            if record['status'] != 'failed':
                return record
            # Only an explicit terminal cloud failure permits a fresh billable step.
            if record.get('cloud_failed'):
                failed_group=str(record.get('failed_group') or '')
                for name in list(record['steps']):
                    group_match=(
                        failed_group and name in ('video_submit:'+failed_group,
                                                  'video_poll:'+failed_group)
                    )
                    legacy_match=not failed_group and name in ('video_submit','video_poll')
                    if group_match or legacy_match or name in ('compose','upload'):
                        record['steps'].pop(name,None)
                record.pop('failed_group',None)
                process=record.get('video_process') or {}
                for code,state in (process.get('groups') or {}).items():
                    if not failed_group or code==failed_group:
                        state.update(status='pending',error='',provider_video_url='',cloud_task_id='')
                process['phase']='preparing'
                process['completed_segments']=sum(
                    state.get('status')=='completed' for state in (process.get('groups') or {}).values()
                )
                if process:
                    record['video_process']=process
            record.update(status='pending',next_run=0,error='',cloud_failed=False,
                          attempt=record.get('attempt',1)+1,
                          deadline_at=time.time()+max(60, int(os.getenv('PHASE2_TASK_MAX_SECONDS', '7200'))))
            self.put(record)
            return record
