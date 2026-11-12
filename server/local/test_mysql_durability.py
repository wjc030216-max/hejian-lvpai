"""Runs only against an explicitly named disposable local MySQL test database."""
import json
import os
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from phase2.mysql_store import MySQLStore
from phase2.store import Store
from common.task_journal import TaskJournal
from template_fixture import managed_templates


@unittest.skipUnless(os.getenv('MYSQL_TEST_URL'),'requires disposable MySQL test schema')
class MySQLDurability(unittest.TestCase):
    def setUp(self):
        url=os.environ['MYSQL_TEST_URL']
        if '/aitravel_hardening_test' not in url: raise RuntimeError('Refusing non-test schema')
        self.s=MySQLStore(url); self.addCleanup(self.s.engine.dispose)
        self.p=dict(project_id='p2_'+uuid.uuid4().hex,wx_openid='test-'+uuid.uuid4().hex,status='draft',stage='portrait',
            template={'video_name':'test'},roles=[dict(character_id='a',person_uid='1',check_status='empty')])
        self.s.put(self.p)

    def test_transaction_rollback_and_normalized_records(self):
        with self.assertRaises(RuntimeError):
            with self.s.transaction() as db:
                p=self.s.get(self.p['project_id'],db); p['status']='bad'; self.s.put(p,db)
                raise RuntimeError('rollback')
        self.assertEqual(self.s.get(self.p['project_id'])['status'],'draft')
        p=dict(self.p,order_no=uuid.uuid4().hex,paid_at=1,payment_mode='dev-bypass',status='portrait_submit')
        self.s.put(p)
        with self.s.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM phase2_orders WHERE project_id=?',(p['project_id'],)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM phase2_roles WHERE project_id=?',(p['project_id'],)).fetchone()[0],1)
        self.assertEqual(self.s.list_works(p['wx_openid'])[1],1)
        self.assertEqual(self.s.list_works('not-owner')[1],0)

    def test_duplicate_order_and_concurrent_leases(self):
        p=dict(self.p,order_no=uuid.uuid4().hex); self.s.put(p)
        with self.assertRaises(IntegrityError): self.s.put(dict(p,project_id='p2_'+uuid.uuid4().hex))
        with ThreadPoolExecutor(4) as pool:
            results=list(pool.map(lambda i:self.s.claim(p['project_id'],str(i)),range(4)))
        self.assertEqual(sum(results),1)

    def test_due_work_does_not_return_completed_or_idle_drafts(self):
        self.assertNotIn(self.p['project_id'],[p['project_id'] for p in self.s.due(1000)])
        self.s.update(self.p['project_id'],{'status':'portrait_submit'})
        self.assertIn(self.p['project_id'],[p['project_id'] for p in self.s.due(1000)])
        self.s.complete(self.p['project_id'],'saved')
        self.assertNotIn(self.p['project_id'],[p['project_id'] for p in self.s.due(1000)])

    def test_pending_payment_never_enters_generation_queue(self):
        p=self.s.get(self.p['project_id'])
        p.update(status='pending_payment',order_no=uuid.uuid4().hex)
        self.s.put(p)
        self.assertNotIn(p['project_id'],[item['project_id'] for item in self.s.due(1000)])

    def test_multiple_process_connections_share_idempotency_and_locks(self):
        j1=TaskJournal(os.environ['MYSQL_TEST_URL']); j2=TaskJournal(os.environ['MYSQL_TEST_URL'])
        self.addCleanup(j1.engine.dispose); self.addCleanup(j2.engine.dispose)
        run=uuid.uuid4().hex
        with ThreadPoolExecutor(4) as pool:
            result=list(pool.map(lambda i:(j1 if i%2 else j2).create('video',run,{'template_code':'same'}),range(4)))
        self.assertEqual(sum(new for _,new in result),1)
        self.assertEqual(len({p['task_id'] for p,_ in result}),1)
        with j1.lock('exclusive') as first:
            with j2.lock('exclusive') as second:
                self.assertTrue(first); self.assertFalse(second)

    def test_sqlite_migration_is_repeatable_and_keeps_original(self):
        with self.s.connection() as db:
            db.execute("DELETE FROM phase2_migrations WHERE name='sqlite-v1'")
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old.sqlite3'; old=Store(path)
            p=dict(self.p,project_id='p2_'+uuid.uuid4().hex); old.put(p)
            self.s.import_sqlite(path); self.s.import_sqlite(path)
            self.assertEqual(self.s.get(p['project_id']),p)
            self.assertEqual(old.get(p['project_id']),p)

    def test_concurrent_api_drafts_reuse_one_record(self):
        item = managed_templates()[0]
        with self.s.engine.begin() as db:
            db.execute(text(
                "INSERT IGNORE INTO phase2_templates "
                "(template_code,location_association,video_genre,actor_count,status,sort_order,payload_json) "
                "VALUES(:code,:scene,:genre,:count,1,0,:payload)"
            ), dict(code=item['template_code'], scene=item['location_association'],
                    genre=item['video_genre'], count=item['actor_count'],
                    payload=json.dumps(item, ensure_ascii=False)))
        with patch.dict(os.environ,{'APP_ENV':'test','PHASE2_STATE_BACKEND':'sqlite','PHASE2_DB_HOST':'',
                                   'PHASE2_AUTH_MODE':'local','PHASE2_STATE_DB':'/tmp/mysql-api-unit.sqlite3'}):
            from phase2 import phase2_app as api
            with patch.object(api,'store',self.s):
                template=api.app.test_client().get('/api/v2/templates').json['data']['items'][0]
                request_id=uuid.uuid4().hex
                def create(_):
                    with api.app.test_client() as client:
                        response=client.post('/api/v2/projects',json={'request_id':request_id,'template_code':template['template_code']})
                        self.assertIn(response.status_code,(200,201),response.data)
                        return response.json['data']['project_id']
                with ThreadPoolExecutor(4) as pool: ids=list(pool.map(create,range(4)))
                self.assertEqual(len(set(ids)),1)


if __name__=='__main__': unittest.main()
