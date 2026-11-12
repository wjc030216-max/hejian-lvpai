"""Regression coverage for schema upgrades, bounded workers and private asset recovery."""
import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from sqlalchemy import create_engine,text,inspect
from common.database_schema import ensure_schema, template_identity
from common.task_journal import TaskJournal, TaskPending
from common.durable_generation import Generation,install
from common.asset_registry import AssetRegistry
from flask import Flask


class RefactorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.url='sqlite:///'+str(self.root/'test.db')

    def test_upgrade_keeps_records_and_is_repeatable(self):
        engine=create_engine(self.url); self.addCleanup(engine.dispose)
        with engine.begin() as db:
            db.execute(text('CREATE TABLE phase2_templates (id INTEGER PRIMARY KEY,template_code VARCHAR(128),location_association VARCHAR(128),video_genre VARCHAR(128),actor_count INT,status INT,sort_order INT,payload_json TEXT,identity_hash VARCHAR(64))'))
            db.execute(text('CREATE UNIQUE INDEX uq_template_identity ON phase2_templates(identity_hash)'))
            db.execute(text('CREATE TABLE phase2_tasks (id VARCHAR(64) PRIMARY KEY,kind VARCHAR(24) NOT NULL,run_id VARCHAR(128) NOT NULL,status VARCHAR(32) NOT NULL,next_run DOUBLE NOT NULL DEFAULT 0,payload TEXT NOT NULL,UNIQUE(kind,run_id))'))
            for i in (1,2):
                db.execute(text('INSERT INTO phase2_templates(id,template_code,location_association,video_genre,actor_count,status,sort_order,payload_json) VALUES(:id,:code,:scene,:genre,2,1,0,:payload)'),dict(id=i,code='old'+str(i),scene='漓江',genre='旅拍',payload=json.dumps(dict(location_association='漓江',video_name='same'))))
            # Simulate an interrupted earlier migration leaving the duplicate
            # identity on the second row.
            db.execute(text('UPDATE phase2_templates SET identity_hash=:hash WHERE id=2'),
                       {'hash': template_identity('漓江', 'same')})
            db.execute(text("INSERT INTO phase2_tasks VALUES('old-task','video','old-run','pending',0,'{}')"))
        ensure_schema(engine); ensure_schema(engine)
        with engine.connect() as db:
            self.assertEqual(db.execute(text('SELECT COUNT(*) FROM phase2_templates')).scalar(),2)
            self.assertEqual(db.execute(text('SELECT SUM(status) FROM phase2_templates')).scalar(),1)
            self.assertEqual(db.execute(text('SELECT COUNT(*) FROM phase2_schema_versions')).scalar(),1)
            self.assertIn('identity_hash',{c['name'] for c in inspect(db).get_columns('phase2_templates')})
            self.assertIn('lease_until',{c['name'] for c in inspect(db).get_columns('phase2_tasks')})
            self.assertEqual(db.execute(text('SELECT COUNT(*) FROM phase2_tasks')).scalar(),1)

    def test_pending_poll_releases_worker_without_resubmit(self):
        journal=TaskJournal(self.url); self.addCleanup(journal.engine.dispose)
        api=SimpleNamespace(logger=logging.getLogger('test'),fetch_template_prompt=Mock(return_value=('prompt','',['a'],[],[],['scene'],'storyboard')),
            create_asset=Mock(side_effect=['a','b','c','d']),wait_asset_active=Mock(return_value=True),
            call_volcano_video=Mock(return_value='cloud-id'),poll_volcano_task=Mock(side_effect=[TaskPending(15),'video']),
            VIDEO_GENERATE_AUDIO=True,VIDEO_DURATION=15,VIDEO_WATERMARK=True)
        worker=Generation('video',api,journal); self.addCleanup(worker.pool.shutdown)
        p,_=journal.create('video','run',dict(template_code='P2S-test',role_mapping=[dict(character_id='a',user_face_url='face',three_view_url='view')]))
        worker.tick(p['task_id'])
        waiting=journal.get(p['task_id']);self.assertEqual(waiting['status'],'running');self.assertNotIn('recovery_count',waiting)
        with journal.lock(p['task_id']) as acquired: self.assertTrue(acquired)
        target=self.root/'video.mp4';target.write_bytes(b'video')
        with patch('local.results.save_local_video',return_value=str(target)),patch('common.storage_client.upload_result',return_value='saved'):
            worker.tick(p['task_id'])
        api.call_volcano_video.assert_called_once();self.assertEqual(journal.get(p['task_id'])['status'],'completed')

    def test_task_lease_prevents_duplicate_workers(self):
        journal=TaskJournal(self.url); self.addCleanup(journal.engine.dispose)
        p,_=journal.create('video','lease-run',dict(template_code='P2S-test'))
        first=journal.claim(p['task_id'],'worker-a')
        second=journal.claim(p['task_id'],'worker-b')
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        journal.release(p['task_id'],'worker-a')
        self.assertIsNotNone(journal.claim(p['task_id'],'worker-b'))

    def test_portrait_retry_http_path_matches_orchestrator(self):
        journal=TaskJournal(self.url);self.addCleanup(journal.engine.dispose)
        api=SimpleNamespace(app=Flask(__name__),logger=logging.getLogger('test'))
        with patch('common.durable_generation.TaskJournal',return_value=journal),patch.object(Generation,'start'):
            worker=install(api,'character');self.addCleanup(worker.pool.shutdown)
        p,_=journal.create('character','run',dict(template_code='P2S-test',actors=[{}]));p.update(status='failed',cloud_failed=True);journal.put(p)
        from phase2.orchestrator import Phase2Orchestrator
        store=Mock();o=Phase2Orchestrator(store);self.addCleanup(o.pool.shutdown);self.addCleanup(o.photo_pool.shutdown)
        def send(method,url,body):
            response=api.app.test_client().post('/api'+url.split('/api',1)[1],json=body)
            self.assertEqual(response.status_code,202)
            return response.json['data']
        o._json=send
        o._submit(dict(project_id='p2_test',run_id='run',character_task_id=p['task_id'],attempt=2),'portrait',True)
        self.assertEqual(journal.get(p['task_id'])['attempt'],2)

    def test_registry_reopens_without_local_mapping_file(self):
        registry=AssetRegistry(self.url);self.addCleanup(registry.engine.dispose)
        image=self.root/'portrait.jpg';image.write_bytes(b'saved-image')
        registry.save('a'*32,'portrait','test-bucket','phase2-test/portraits/a.jpg',image,800,1424)
        reopened=AssetRegistry(self.url);self.addCleanup(reopened.engine.dispose)
        self.assertEqual(reopened.get('a'*32,'portrait'),dict(bucket='test-bucket',key='phase2-test/portraits/a.jpg'))
        with reopened.engine.connect() as db:
            row=db.execute(text('SELECT size_bytes,width,height FROM phase2_media_objects')).first()
            self.assertEqual(tuple(row),(11,800,1424))

if __name__=='__main__':unittest.main()
