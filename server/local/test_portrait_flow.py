"""Automatic pipeline integration tests; no cloud requests."""
import io
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from phase2.store import Store
from phase2.orchestrator import Phase2Orchestrator
from template_fixture import write_managed_catalog

class AutomaticPipeline(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        catalog_path = write_managed_catalog(self.root / "catalog.json")
        env = patch.dict(os.environ, {'APP_ENV':'test','PHASE2_DB_HOST':'','REDIS_URL':'',
            'PHASE2_PROJECTS_PER_HOUR':'1000','PHASE2_UPLOADS_PER_10_MINUTES':'1000',
            'PHASE2_STATE_DB':str(self.root/'initial.sqlite3'),
            'PHASE2_DEV_PAYMENT_BYPASS':'true',
            'PHASE2_AI_ORCHESTRATION_ENABLED':'false'})
        env.start(); self.addCleanup(env.stop)
        from phase2 import phase2_app as api
        self.api=api
        self.store=Store(self.root/'test.sqlite3')
        self.worker=Phase2Orchestrator(self.store)
        self.addCleanup(self.worker.pool.shutdown)
        for name,value in [('store',self.store),('orchestrator',self.worker),
                           ('UPLOAD_ROOT',self.root/'photos'),('DATA_FILE',catalog_path)]:
            p=patch.object(api,name,value); p.start(); self.addCleanup(p.stop)
        self.client=api.app.test_client()

    def ready(self,count=1):
        catalog=self.client.get('/api/v2/templates').json['data']['items']
        item=next(t for t in catalog if t['actor_count']==count)
        body={'template_code':item['template_code'],'template_version':item['version'],'request_id':uuid.uuid4().hex}
        response=self.client.post('/api/v2/projects',json=body)
        self.assertEqual(response.status_code,201,response.data)
        p=response.json['data']
        self.assertEqual(self.client.post('/api/v2/projects',json=body).json['data']['project_id'],p['project_id'])
        for role in p['roles']:
            photo=io.BytesIO(); Image.new('RGB',(600,800)).save(photo,'JPEG'); photo.seek(0)
            r=self.client.post('/api/v2/projects/'+p['project_id']+'/assets',data={
                'person_uid':role['person_uid'],'photo_consent':'true','file':(photo,'photo.jpg')})
            self.assertEqual(r.status_code,201,r.data)
            self.worker.tick(p['project_id'])
        return self.store.get(p['project_id'])

    def pay(self,p):
        return self.client.post('/api/v2/projects/'+p['project_id']+'/order',
            json={'agreement':True,'agreement_version':'travel-photo-v1'})

    def test_local_order_is_rejected_when_ai_is_disabled(self):
        p=self.ready()
        with patch.dict(os.environ,{'APP_ENV':'local'}):
            result=self.pay(p)
        self.assertEqual(result.status_code,503,result.data)
        self.assertFalse(self.store.get(p['project_id']).get('order_no'))

    def test_disabled_payment_blocks_order_and_prepay(self):
        p=self.ready()
        with patch.dict(os.environ, {
                'PHASE2_PAYMENT_MODE':'disabled',
                'PHASE2_DEV_PAYMENT_BYPASS':'false'}):
            order=self.pay(p)
            prepay=self.client.post('/api/v2/projects/'+p['project_id']+'/prepay')
        self.assertEqual(order.status_code,503,order.data)
        self.assertIn('支付功能已关闭',order.json['msg'])
        self.assertEqual(prepay.status_code,503,prepay.data)
        self.assertFalse(self.store.get(p['project_id']).get('order_no'))

    def test_dev_payment_bypass_starts_generation_without_charging(self):
        p=self.ready()
        with patch.dict(os.environ, {
                'PHASE2_PAYMENT_MODE':'dev-bypass',
                'PHASE2_DEV_PAYMENT_BYPASS':'false'}):
            config=self.client.get('/api/v2/config').json['data']
            order=self.pay(p)
        self.assertTrue(config['payment_enabled'])
        self.assertEqual(config['payment_mode'],'dev-bypass')
        self.assertEqual(order.status_code,201,order.data)
        self.assertEqual(order.json['data']['payment_mode'],'dev-bypass')
        self.assertEqual(order.json['data']['status'],'portrait_submit')
        self.assertEqual(order.json['data']['amount_paid'],0)

    def test_uploaded_photo_is_moved_to_tos_and_local_cache_is_removed(self):
        catalog=self.client.get('/api/v2/templates').json['data']['items']
        item=catalog[0]
        project=self.client.post('/api/v2/projects',json={
            'template_code':item['template_code'],
            'template_version':item['version'],
            'request_id':uuid.uuid4().hex,
        }).json['data']
        role=project['roles'][0]
        photo=io.BytesIO()
        Image.new('RGB',(600,800)).save(photo,'JPEG')
        photo.seek(0)
        with patch.object(self.api,'LOCAL_REAL',True), \
                patch('common.storage_client.upload_asset') as upload, \
                patch('common.storage_client.asset_url',
                      return_value='https://tos.example/signed-photo') as asset_url:
            response=self.client.post(
                '/api/v2/projects/'+project['project_id']+'/assets',
                data={
                    'person_uid':role['person_uid'],
                    'photo_consent':'true',
                    'file':(photo,'photo.jpg'),
                })
            self.assertEqual(response.status_code,201,response.data)
            upload.assert_called_once()
            stored=self.store.get(project['project_id'])
            self.assertEqual(stored['roles'][0]['photo_storage'],'tos')
            self.assertEqual(stored['roles'][0]['photo_path'],'')
            cache=Path(self.api.UPLOAD_ROOT)/project['project_id']
            self.assertEqual(list(cache.glob('*.jpg')),[])
            fetched=self.client.get('/api/v2/assets/'+stored['roles'][0]['photo_asset_id'])
            self.assertEqual(fetched.status_code,302)
            self.assertEqual(fetched.headers['Location'],'https://tos.example/signed-photo')
            asset_url.assert_called_once_with(stored['roles'][0]['photo_asset_id'])

    def test_share_thumbnail_is_stored_in_tos_without_local_cache(self):
        p=self.ready()
        p.update(
            status='completed',
            portrait_url='https://example/api/v2/portraits/'+'a'*32,
            retention_expires_at=time.time()+86400,
        )
        self.store.put(p)
        source=io.BytesIO()
        Image.new('RGB',(360,640),'white').save(source,'JPEG')
        response=Mock()
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        response.content=source.getvalue()
        response.raise_for_status=Mock()
        with patch('common.storage_client.asset_url',
                   side_effect=[RuntimeError('missing'),'https://tos.example/share']) as signed, \
                patch('common.storage_client.portrait_url',
                      return_value='https://tos.example/portrait'), \
                patch('common.storage_client.upload_asset_bytes',
                      return_value='https://api.example/share') as upload, \
                patch('phase2.phase2_app.requests.get',return_value=response):
            result=self.client.get('/api/v2/share-image/'+p['project_id']+'.jpg')
        self.assertEqual(result.status_code,302)
        self.assertEqual(result.headers['Location'],'https://tos.example/share')
        upload.assert_called_once()
        self.assertEqual(signed.call_count,2)

    def test_paid_order_terminates_instead_of_spinning_when_ai_is_disabled(self):
        p=self.ready(); self.assertEqual(self.pay(p).status_code,201)
        self.worker.tick(p['project_id'])
        stopped=self.store.get(p['project_id'])
        self.assertEqual(stopped['status'],'terminated')
        self.assertEqual(stopped['error'],'AI制作服务未启用，本次未开始生成')
        self.assertEqual(stopped['refund_status'],'not_charged')
        with patch.dict(os.environ,{'APP_ENV':'local'}):
            retry=self.client.post('/api/v2/projects/'+p['project_id']+'/retry')
        self.assertEqual(retry.status_code,503,retry.data)
        self.assertEqual(self.store.get(p['project_id'])['status'],'terminated')

    def test_one_two_three_roles_payment_and_snapshot(self):
        for count in (1,2,3):
            p=self.ready(count); result=self.pay(p)
            self.assertEqual(result.status_code,201,result.data)
            self.assertEqual(result.json['data']['payment_mode'],'dev-bypass')
            self.assertEqual(result.json['data']['amount_paid'],0)
            self.assertEqual(self.pay(p).json['data']['order_no'],result.json['data']['order_no'])
            runtime=self.client.get('/api/v2/templates/'+p['runtime_code']+'/runtime').json['data']
            self.assertEqual(len(runtime['costume_url']),count)
            self.assertTrue(runtime['portrait_prompt'])

    def test_single_experience_second_and_third_slots_keep_other_drafts_out_of_generation(self):
        for count, slot in ((2, 1), (3, 2)):
            p = self.ready(count); pid = p['project_id']; chosen = p['roles'][slot]
            before = [r['photo_url'] for r in p['roles']]
            # Non-participating drafts need not pass face checks.
            for i, role in enumerate(p['roles']):
                if i != slot: role['check_status'] = 'rejected'
            self.store.put(p)
            response = self.client.post('/api/v2/projects/'+pid+'/participation', json={'mode':'single','person_uid':chosen['person_uid']})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(sum(not r['uses_template'] for r in response.json['data']['roles']), 1)
            self.assertEqual(self.pay(p).status_code, 201)
            with patch.object(self.worker, '_json', return_value={'task_id':'solo-character'}) as post:
                self.worker._submit(self.store.get(pid), 'portrait', False)
            actors = post.call_args.args[2]['actors']
            self.assertEqual([a['character_id'] for a in actors], [r['character_id'] for r in p['roles']])
            self.assertEqual([a['image'] for a in actors if a['source']=='user_face'], [before[slot]])
            self.assertFalse(set(before[:slot]+before[slot+1:]) & {a['image'] for a in actors})
            self.assertEqual([r['photo_url'] for r in self.store.get(pid)['roles']], before)
            self.assertEqual(self.client.post('/api/v2/projects/'+pid+'/participation',json={'mode':'multi'}).status_code,409)

    def test_mode_switch_requires_explicit_slot_and_restores_draft_photos(self):
        p=self.ready(3); pid=p['project_id']; path='/api/v2/projects/'+pid+'/participation'
        self.assertEqual(self.client.post(path,json={'mode':'single','person_uid':'invalid'}).status_code,422)
        self.assertEqual(self.client.post(path,json={'mode':'single'}).status_code,200)
        self.assertEqual(self.pay(p).status_code,422)
        restored=self.client.post(path,json={'mode':'multi'}).json['data']
        self.assertEqual([r['photo_url'] for r in restored['roles']],[r['photo_url'] for r in p['roles']])
        self.assertEqual(self.pay(p).status_code,201)

    def test_single_experience_cannot_use_missing_template_assets(self):
        p=self.ready(3)
        runtime=self.api.build_runtime_template(p['template']); runtime['actor_url'] = []
        with patch.object(self.api,'build_runtime_template',return_value=runtime):
            result=self.client.post('/api/v2/projects/'+p['project_id']+'/participation',json={'mode':'single','person_uid':'2'})
        self.assertEqual(result.status_code,422)
        self.assertEqual(self.store.get(p['project_id'])['participation_mode'],'multi')

    def test_only_selected_visitor_is_face_checked_and_must_pass(self):
        p=self.ready(3); pid=p['project_id']
        self.client.post('/api/v2/projects/'+pid+'/participation',json={'mode':'single','person_uid':'3'})
        p=self.store.get(pid)
        for role in p['roles']: role['check_status']='checking'
        self.store.put(p); self.worker.enabled=True
        with patch.object(self.worker,'_check_face',side_effect=RuntimeError('bad photo')) as check:
            self.worker._photos(p)
        check.assert_called_once_with(pid,p['roles'][2]['photo_url'])
        self.assertEqual(self.store.get(pid)['roles'][0]['check_status'],'checking')
        self.assertEqual(self.pay(p).status_code,422)

    def test_real_metadata_filters_include_unrestricted_templates(self):
        catalog=self.client.get('/api/v2/templates').json['data']
        self.assertEqual(set(catalog['facets']),{'scene','actor_count','gender','relationship'})
        self.assertNotIn('男',catalog['facets']['relationship'])
        self.assertEqual(self.client.get('/api/v2/templates?lead_gender=男&age=青年').json['data']['total'],3)
        self.assertEqual(self.client.get('/api/v2/templates?lead_gender=女').json['data']['total'],3)

    def test_automatic_portrait_storage_video_without_lottery(self):
        p=self.ready(); self.pay(p); pid=p['project_id']; self.worker.enabled=True
        with patch.object(self.worker,'_json',side_effect=[
            {'task_id':'character'},{'status':'completed','result':'generated-portrait','role_mapping':[{'role_index':1,'character_id':'a','user_face_url':'face','three_view_url':'view'}]},
            {'task_id':'video'},{'status':'completed','result':'stored-video'}]) as api, patch(
                'common.storage_client.save_portrait',return_value=('asset','stored-portrait')):
            self.worker.tick(pid); self.worker.tick(pid)
            self.assertEqual(self.store.get(pid)['status'],'portrait_saving')
            self.worker.tick(pid)
            self.assertEqual(self.store.get(pid)['status'],'video_submit')
            self.worker.tick(pid); self.worker.tick(pid)
            self.assertEqual(self.store.get(pid)['status'],'completed')
            payload=api.call_args_list[2].args[2]
            self.assertEqual(payload,{'wx_openid':pid,'template_code':p['runtime_code'],'character_task_id':'character',
                                      'role_mapping':[{'role_index':1,'character_id':'a','user_face_url':'face','three_view_url':'view'}]})
        self.store.complete(pid,'stored-video')
        self.assertEqual(self.client.get('/api/v2/messages').json['data']['total'],2)
        self.assertEqual(self.client.get('/api/v2/works').json['data']['total'],1)
        watched=self.client.get('/api/v2/works/watch').json['data']['items']
        self.assertEqual(watched[0]['project_id'],pid)
        self.assertTrue(watched[0]['portrait_url'])
        self.assertTrue(watched[0]['video_url'])
        self.client.post('/api/v2/projects/'+pid+'/read')
        self.assertEqual(self.client.get('/api/v2/messages').json['data']['total'],0)
        self.assertEqual(self.client.post('/api/v2/benefits/draw',json={'request_id':'draw_test_001'}).status_code,404)

    def test_storage_failure_and_restart_reuse_existing_task(self):
        p=self.ready(); self.pay(p); pid=p['project_id']
        self.store.update(pid,{'status':'portrait_saving','character_task_id':'original','portrait_source_url':'photo'})
        self.worker.enabled=True
        with patch('common.storage_client.save_portrait',side_effect=RuntimeError('storage unavailable')), patch.object(self.worker,'_json') as post:
            self.worker.tick(pid); post.assert_not_called()
        self.assertEqual(Store(self.store.path).get(pid)['status'],'portrait_saving')
        self.store.update(pid,{'next_run_at':0})
        with patch('common.storage_client.save_portrait',return_value=('asset','saved')), patch.object(self.worker,'_json') as post:
            self.worker.tick(pid); post.assert_not_called()
        self.assertEqual(self.store.get(pid)['status'],'video_submit')

    def test_removed_lottery_routes_and_no_new_lottery_tables(self):
        self.assertEqual(self.client.get('/api/v2/benefits').status_code,404)
        self.assertEqual(self.client.post('/api/v2/benefits/draw',json={}).status_code,404)
        self.assertNotIn('lottery_configured',self.client.get('/api/v2/config').json['data'])
        with self.store.connection() as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse({'chances','draws'} & tables)

    def test_completed_video_preserves_old_lottery_data_without_awarding(self):
        # Model an existing installation. Initialization must not drop old data;
        # completing/reloading a video must not add or consume an opportunity.
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE chances(project_id TEXT PRIMARY KEY, used_by TEXT UNIQUE);
                CREATE TABLE draws(id TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE,
                    result TEXT NOT NULL, created REAL NOT NULL);
                INSERT INTO chances VALUES ('historical', NULL);
                INSERT INTO draws VALUES ('past-draw','past-project','retained',1);
            """)
        p=self.ready(); self.pay(p)
        self.store.complete(p['project_id'],'saved-video')
        reopened=Store(self.store.path)
        reopened.complete(p['project_id'],'saved-video')
        with reopened.connection() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM chances')],[('historical',None)])
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM draws')],[('past-draw','past-project','retained',1.0)])
        self.assertEqual(reopened.get(p['project_id'])['status'],'completed')

    def test_response_loss_recovers_without_second_post(self):
        p=self.ready(); self.pay(p); pid=p['project_id']; self.worker.enabled=True
        with patch.object(self.worker,'_json',side_effect=TimeoutError): self.worker.tick(pid)
        self.assertEqual(self.store.get(pid)['status'],'portrait_unknown')
        self.store.update(pid,{'next_run_at':0})
        with patch.object(self.worker,'_json',return_value={'exists':True,'task_id':'recovered','status':'running'}) as api:
            self.worker.tick(pid)
            self.assertEqual(api.call_args.args[0],'GET')
        self.assertEqual(self.store.get(pid)['character_task_id'],'recovered')

    def test_three_failed_attempts_terminate_without_refund(self):
        p=self.ready(); self.pay(p); pid=p['project_id']
        for _ in range(3): self.store.failed_attempt(pid,'failed')
        self.assertEqual(self.store.get(pid)['status'],'terminated')
        self.assertEqual(self.store.get(pid)['refund_status'],'not_charged')
        self.store.complete(pid,'late-result')
        self.assertEqual(self.store.get(pid)['status'],'terminated')

    def test_project_runtime_deadline_requires_user_choice(self):
        p=self.ready(); self.pay(p); pid=p['project_id']; self.worker.enabled=True
        self.store.update(pid,{'status':'portrait_processing','submission_at':time.time()-9000,
                               'payment_mode':'allinpay','payment_status':'paid','amount_paid':1990})
        with patch.object(self.worker,'_json') as paid:
            self.worker.tick(pid); paid.assert_not_called()
        saved=self.store.get(pid)
        self.assertEqual(saved['status'],'terminated')
        self.assertEqual(saved['refund_status'],'available')

    def test_refunded_work_hides_media_and_public_share(self):
        project=self.ready(1); self.pay(project)
        project=self.store.get(project['project_id'])
        project.update(status='refunded',refund_status='succeeded',
                       portrait_url='http://127.0.0.1:8080/api/v2/portraits/'+'a'*32,
                       video_url='http://127.0.0.1:8080/api/v2/results/'+'b'*32,
                       portrait_asset_id='a'*32,video_task_id='b'*32)
        project['roles'][0]['photo_url']='http://127.0.0.1:8080/api/v2/assets/asset_'+'c'*32
        self.store.put(project)
        view=self.api._view(self.store.get(project['project_id']))
        self.assertEqual((view['portrait_url'],view['video_url'],view['share_image_url']),
                         ('','',''))
        self.assertEqual(view['roles'][0]['photo_url'],'')
        self.assertEqual(self.client.get('/api/v2/share/'+project['project_id']).status_code,404)
        self.assertTrue(self.api._media_archived('portrait','a'*32))
        self.assertTrue(self.api._media_archived('video','b'*32))

    def test_expired_work_hides_media_and_share(self):
        project=self.ready(1); self.pay(project)
        project=self.store.get(project['project_id'])
        project.update(status='completed',completed_at=time.time()-91*86400,
                       retention_expires_at=time.time()-86400,
                       portrait_url='http://127.0.0.1:8080/api/v2/portraits/'+'a'*32,
                       video_url='http://127.0.0.1:8080/api/v2/results/'+'b'*32,
                       portrait_asset_id='a'*32,video_task_id='b'*32)
        self.store.put(project)
        view=self.client.get('/api/v2/projects/'+project['project_id']).json['data']
        self.assertEqual(view['status'],'expired')
        self.assertEqual((view['portrait_url'],view['video_url'],view['roles']),('','',[]))
        self.assertEqual(self.client.get('/api/v2/share/'+project['project_id']).status_code,410)

    def test_failed_paid_order_requires_user_choice_and_locks_after_refund(self):
        refund_project=self.ready(1); self.pay(refund_project)
        refund_project=self.store.get(refund_project['project_id'])
        refund_project.update(status='terminated',payment_mode='allinpay',
                              payment_status='paid',amount_fen=1990,amount_paid=1990,
                              refund_status='available')
        self.store.put(refund_project)
        result=self.client.post('/api/v2/projects/'+refund_project['project_id']+'/refund')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(self.store.get(refund_project['project_id'])['status'],'refund_pending')
        self.assertIsNotNone(self.store.refund_for_project(refund_project['project_id']))
        self.assertEqual(self.client.post('/api/v2/projects/'+refund_project['project_id']+'/retry').status_code,409)

        retry_project=self.ready(2); self.pay(retry_project)
        retry_project=self.store.get(retry_project['project_id'])
        retry_project.update(status='terminated',payment_mode='allinpay',
                             payment_status='paid',amount_fen=1990,amount_paid=1990,
                             refund_status='available',stage='portrait')
        self.store.put(retry_project)
        result=self.client.post('/api/v2/projects/'+retry_project['project_id']+'/retry')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(self.store.get(retry_project['project_id'])['status'],'portrait_submit')

    def test_missing_submission_can_resume_only_after_authoritative_database_query(self):
        p=self.ready(); self.pay(p); pid=p['project_id']
        self.store.update(pid,{'status':'portrait_unknown','run_id':pid})
        with patch.object(self.worker,'_json',return_value={'exists':False,'authoritative':True}):
            self.worker._reconcile(self.store.get(pid),'portrait')
        self.assertEqual(self.store.get(pid)['status'],'portrait_submit')

    def test_lost_retry_acknowledgement_matches_persisted_attempt(self):
        p=self.ready(); self.pay(p); pid=p['project_id']
        self.store.update(pid,{'status':'portrait_unknown','run_id':pid,'submission_retry':True,'attempt':2})
        with patch.object(self.worker,'_json',return_value={'exists':True,'task_id':'same-task','status':'failed',
                'attempt':2,'authoritative':True}):
            self.worker._reconcile(self.store.get(pid),'portrait')
        self.assertEqual(self.store.get(pid)['status'],'portrait_processing')

    def test_invalid_face_prevents_order(self):
        p=self.ready(); pid=p['project_id']
        p['roles'][0]['check_status']='checking'; self.store.put(p); self.worker.enabled=True
        with patch.object(self.worker,'_check_face',side_effect=RuntimeError('multiple faces')), patch.object(self.worker,'_json') as paid:
            self.worker.tick(pid); paid.assert_not_called()
        self.assertEqual(self.store.get(pid)['roles'][0]['check_status'],'rejected')
        self.assertEqual(self.pay(p).status_code,422)

    def test_display_heat_never_changes_template_version_or_real_unique_counts(self):
        p=self.ready(); code=p['template_code']
        before=self.client.get('/api/v2/templates/'+code).json['data']['version']
        with self.store.connection() as db:
            db.execute('INSERT INTO phase2_template_settings(template_code,heat_base,show_heat,revision) VALUES(?,?,?,?)',(code,32000,1,1))
        self.assertEqual(self.client.get('/api/v2/templates/'+code).json['data']['version'],before)
        self.assertEqual(self.pay(p).status_code,201)
        self.assertEqual(self.pay(p).status_code,200)
        second=self.ready(); self.assertEqual(second['template_code'],code)
        self.assertEqual(self.pay(second).status_code,201)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM phase2_template_selections').fetchone()[0],1)
        displayed=next(x for x in self.client.get('/api/v2/templates').json['data']['items'] if x['template_code']==code)
        self.assertEqual(displayed['heat_label'],'3.2万人选择')

    def test_production_blocks_bypass_and_payment_callback_is_reserved(self):
        p=self.ready()
        with patch.dict(os.environ,{'APP_ENV':'production'}): self.assertEqual(self.pay(p).status_code,503)
        self.assertEqual(self.client.post('/api/v2/payments/callback',json={}).status_code,501)
        self.assertEqual(self.client.post('/api/v2/projects/unknown/confirm-portrait').status_code,404)

    def test_parallel_orders_and_worker_leases(self):
        p=self.ready(); pid=p['project_id']
        def order(_):
            with self.api.app.test_client() as client:
                return client.post('/api/v2/projects/'+pid+'/order',json={'agreement':True,'agreement_version':'travel-photo-v1'}).json['data']['order_no']
        with ThreadPoolExecutor(max_workers=4) as pool: numbers=list(pool.map(order,range(4)))
        self.assertEqual(len(set(numbers)),1)
        self.assertTrue(self.store.claim(pid,'one'))
        self.assertFalse(self.store.claim(pid,'two'))
        self.store.release(pid,'one')

    def test_database_rejects_duplicate_order_number(self):
        import sqlite3
        first=self.ready(); self.pay(first)
        second=self.ready(); second['order_no']=self.store.get(first['project_id'])['order_no']
        with self.assertRaises(sqlite3.IntegrityError): self.store.put(second)
        self.assertFalse(self.store.get(second['project_id']).get('order_no'))

    def test_completed_result_and_time_cannot_be_overwritten(self):
        p=self.ready(); self.pay(p); pid=p['project_id']
        self.store.complete(pid,'original')
        original=self.store.get(pid)
        self.store.complete(pid,'late-conflicting-result')
        self.assertEqual(self.store.get(pid),original)

    def test_video_has_independent_retry_budget_and_duplicate_portrait_ack_is_ignored(self):
        p=self.ready(); self.pay(p); pid=p['project_id']
        self.store.update(pid,{'attempt':3,'status':'portrait_saving'})
        self.store.portrait_saved(pid,{'portrait_url':'saved','portrait_saved':True})
        self.assertEqual(self.store.get(pid)['attempt'],1)
        self.assertEqual(self.store.get(pid)['portrait_attempts'],3)
        self.store.update(pid,{'status':'video_processing','video_task_id':'running'})
        before=self.store.get(pid)
        self.store.portrait_saved(pid,{'portrait_url':'late-result'})
        self.assertEqual(self.store.get(pid),before)
        self.store.failed_attempt(pid,'video failure')
        self.assertEqual(self.store.get(pid)['status'],'video_retry')
        self.assertEqual(self.store.get(pid)['attempt'],2)

    def test_unselected_draft_photos_do_not_keep_scheduler_busy(self):
        p=self.ready(3)
        p.update(participation_mode='single',selected_person_uid='2')
        p['roles'][0]['check_status']='checking'
        self.assertFalse(self.worker._needs_photo_check(p))
        p['roles'][1]['check_status']='checking'
        self.assertTrue(self.worker._needs_photo_check(p))

if __name__=='__main__': unittest.main()

