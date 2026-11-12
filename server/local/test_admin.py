"""Operator login, immutable real-template imports, heat edits, and audit trail."""
import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import jwt
from sqlalchemy import text
from common.admin_password import password_hash
from common.database_schema import ensure_service_log_table
from admin import admin_app as admin_module
from admin.admin_app import create_app, monitor_task
from admin.store import AdminStore
from admin.importer import import_archive
from phase2.runtime_templates import build_runtime_template,normalize_template
from common.task_journal import TaskJournal
from template_fixture import archive_template, create_multi_segment_template_folder, create_template_folder

ROOT=Path(__file__).resolve().parents[2]
class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name); self.url='sqlite:///'+str(self.folder/'admin.db')
        self.env=patch.dict(os.environ,dict(APP_ENV='test',ADMIN_USERNAME='admin',ADMIN_PASSWORD_HASH=password_hash('test-password'),
            ADMIN_JWT_SECRET='admin-secret-'*4,AITRAVEL_JWT_SECRET='service-secret-'*4,PUBLIC_BASE_URL='https://test.example.com/media/'+'a'*64))
        self.env.start(); self.addCleanup(self.env.stop)
        self.store=AdminStore(self.url); self.addCleanup(self.store.engine.dispose)
        self.app=create_app(self.store,self.folder/'assets'); self.client=self.app.test_client()
        self.csrf=self.client.post('/admin/api/login',json=dict(username='admin',password='test-password')).json['csrf']
        self.headers={'X-CSRF-Token':self.csrf}

    def archive(self):
        source=create_template_folder(self.folder/'fixture','测试模板',actor_count=3)
        archive=archive_template(source)
        return io.BytesIO(archive.read_bytes())

    def upload(self,code='',publish=True):
        data={'file':(self.archive(),'template.zip')}
        if code: data['template_code']=code
        if publish: data['publish']='1'
        result=self.client.post('/admin/api/templates/upload',data=data,headers=self.headers)
        self.assertEqual(result.status_code,201,result.data)
        return result.json['template_code']

    def ensure_projects_table(self):
        with self.store.engine.begin() as db:
            db.execute(text('''CREATE TABLE IF NOT EXISTS phase2_projects (
                id VARCHAR(64) PRIMARY KEY, owner_id VARCHAR(128) NOT NULL,
                status VARCHAR(32) NOT NULL, runnable TINYINT NOT NULL, due DOUBLE NOT NULL,
                paid_at DOUBLE NOT NULL, order_no VARCHAR(64) UNIQUE, payload LONGTEXT NOT NULL)'''))

    def test_login_session_and_csrf_are_separate_from_user_tokens(self):
        anon=self.app.test_client(); self.assertEqual(anon.get('/admin/api/templates').status_code,401)
        self.assertEqual(anon.post('/admin/api/login',json=dict(username='admin',password='wrong')).status_code,401)
        self.assertEqual(self.client.get('/admin/api/session').status_code,200)
        self.assertEqual(self.client.patch('/admin/api/templates/fake',json={}).status_code,401)
        self.assertEqual(self.client.post('/admin/api/logout',headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/admin/api/session').status_code,401)

    def test_persisted_local_media_urls_follow_the_current_gateway(self):
        with patch.object(admin_module, 'PUBLIC_API_BASE_URL', 'http://127.0.0.1:8092'):
            self.assertEqual(
                admin_module.transform(
                    'http://127.0.0.1:8080/api/v2/results/' + 'a' * 32),
                'http://127.0.0.1:8092/api/v2/results/' + 'a' * 32,
            )

    def test_refund_workbench_marks_success_and_archives_project(self):
        project_id='p2_'+'c'*32; refund_no='RF-ADMIN-TEST'
        payload=dict(project_id=project_id,status='refund_pending',refund_status='needs_review',
                     order_no='P2PAY',amount_paid=1990,portrait_url='portrait',video_url='video')
        with self.store.engine.begin() as db:
            db.execute(text('''CREATE TABLE IF NOT EXISTS phase2_projects (
                id VARCHAR(64) PRIMARY KEY, owner_id VARCHAR(128) NOT NULL,
                status VARCHAR(32) NOT NULL, runnable TINYINT NOT NULL, due DOUBLE NOT NULL,
                paid_at DOUBLE NOT NULL, order_no VARCHAR(64) UNIQUE, payload LONGTEXT NOT NULL)'''))
            db.execute(text('''INSERT INTO phase2_projects
                (owner_id,status,runnable,due,paid_at,order_no,payload,id)
                VALUES(:owner,:status,0,0,1,:order_no,:payload,:id)'''),
                dict(owner='wx_user',status='refund_pending',order_no='P2PAY',
                     payload=json.dumps(payload,ensure_ascii=False),id=project_id))
            db.execute(text('''INSERT INTO phase2_refunds
                (refund_no,project_id,order_no,amount_fen,status,attempts,next_run,
                 requested_at,updated_at,operator)
                VALUES(:no,:pid,:order_no,1990,'needs_review',1,0,1,1,'automatic')'''),
                dict(no=refund_no,pid=project_id,order_no='P2PAY'))
        listed=self.client.get('/admin/api/refunds',headers=self.headers)
        self.assertEqual(listed.status_code,200)
        self.assertEqual(listed.json['items'][0]['refund_no'],refund_no)
        result=self.client.patch('/admin/api/refunds/'+refund_no,
            json=dict(action='succeeded',note='通联流水已核对'),headers=self.headers)
        self.assertEqual(result.status_code,200,result.data)
        with self.store.engine.connect() as db:
            row=db.execute(text('SELECT status,payload FROM phase2_projects WHERE id=:id'),
                           dict(id=project_id)).first()
            refund=db.execute(text('SELECT status,operator FROM phase2_refunds WHERE refund_no=:no'),
                              dict(no=refund_no)).first()
        self.assertEqual(row[0],'refunded')
        self.assertEqual(json.loads(row[1])['refund_status'],'succeeded')
        self.assertEqual(tuple(refund),('succeeded','admin'))

    def test_transactions_merge_paid_order_and_refund_filters(self):
        self.ensure_projects_table()
        project_id='p2_'+'a'*32
        payload=dict(project_id=project_id,wx_openid='wx_tx',status='refund_failed',
                     template=dict(video_name='交易合并测试',actor_count=2,location_association='漓江'),
                     order_no='P2TXORDER',payment_mode='allinpay',payment_status='paid',
                     amount_fen=1690,amount_paid=1690,refund_no='RFTX1',refund_status='failed',
                     created_at=1,paid_at=2)
        with self.store.engine.begin() as db:
            db.execute(text('''INSERT INTO phase2_projects
                (id,owner_id,status,runnable,due,paid_at,order_no,payload)
                VALUES(:id,'wx_tx','refund_failed',0,0,2,'P2TXORDER',:payload)'''),
                dict(id=project_id,payload=json.dumps(payload,ensure_ascii=False)))
            db.execute(text('''INSERT INTO phase2_refunds
                (refund_no,project_id,order_no,amount_fen,status,attempts,next_run,
                 requested_at,updated_at,operator)
                VALUES('RFTX1',:pid,'P2TXORDER',1690,'failed',2,0,3,4,'automatic')'''),
                dict(pid=project_id))
        result=self.client.get('/admin/api/transactions?bucket=refund_failed&action=1&q=P2TXORDER')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.json['total'],1)
        item=result.json['items'][0]
        self.assertEqual(item['order_no'],'P2TXORDER')
        self.assertEqual(item['refund_no'],'RFTX1')
        self.assertTrue(item['needs_action'])
        self.assertEqual(result.json['summary']['needs_action'],1)

    def test_attach_video_uses_group_scoped_step_for_dual_segment_task(self):
        journal=TaskJournal(self.url)
        self.addCleanup(journal.engine.dispose)
        task,_=journal.create('video','dual-admin',{
            'template_code':'P2S-dual-admin',
            'character_task_id':'portrait',
            'role_mapping':[],
        })
        payload=journal.get(task['task_id'])
        payload.update(
            status='needs_review',
            steps={'video_submit:A':{'status':'done','result':'cloud-a'}},
            video_process=dict(
                phase='preparing',completed_segments=1,total_segments=2,
                groups={
                    'A':{'status':'completed','cloud_task_id':'cloud-a'},
                    'B':{'status':'submitting','cloud_task_id':''},
                },
            ),
        )
        journal.put(payload)
        response=self.client.post(
            '/admin/api/tasks/'+task['task_id']+'/resolve',
            json=dict(action='attach_video',group_code='B',cloud_task_id='cloud-b'),
            headers=self.headers,
        )
        self.assertEqual(response.status_code,200,response.data)
        updated=journal.get(task['task_id'])
        self.assertEqual(updated['steps']['video_submit:A']['result'],'cloud-a')
        self.assertEqual(updated['steps']['video_submit:B']['result'],'cloud-b')
        self.assertEqual(updated['status'],'pending')

    def test_program_logs_combine_progress_and_task_events(self):
        with self.store.engine.begin() as db:
            db.execute(text('''CREATE TABLE IF NOT EXISTS phase2_events (
                id VARCHAR(100) PRIMARY KEY,project_id VARCHAR(64) NOT NULL,
                kind VARCHAR(32) NOT NULL,title TEXT NOT NULL,created DOUBLE NOT NULL,
                seen INT NOT NULL DEFAULT 0)'''))
            db.execute(text("INSERT INTO phase2_events VALUES('event-1','p2_log','progress','作品已完成',10,0)"))
            db.execute(text('''INSERT INTO phase2_task_events
                (id,task_id,kind,status,message,created)
                VALUES('task-event-1','task-log','video','failed','视频提交失败',11)'''))
        result=self.client.get('/admin/api/logs?kind=program&q=失败')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.json['total'],1)
        self.assertEqual(result.json['items'][0]['source'],'制作任务')

    def test_task_status_is_not_overwritten_by_project_status(self):
        self.ensure_projects_table()
        pid='p2_'+'b'*32; task_id='task-status-test'
        project=dict(project_id=pid,wx_openid='wx_task',status='archived',
                     template=dict(video_name='归档任务',actor_count=1,location_association='漓江'),
                     order_no='P2TASK',created_at=1,paid_at=2)
        task=dict(task_id=task_id,type='video',status='completed',created_at=3,
                  template_code='P2S-'+pid,steps={})
        with self.store.engine.begin() as db:
            db.execute(text('''INSERT INTO phase2_projects
                (id,owner_id,status,runnable,due,paid_at,order_no,payload)
                VALUES(:id,'wx_task','archived',0,0,2,'P2TASK',:payload)'''),
                dict(id=pid,payload=json.dumps(project,ensure_ascii=False)))
            db.execute(text('''INSERT INTO phase2_tasks
                (id,kind,run_id,status,next_run,payload)
                VALUES(:id,'video',:run,'completed',0,:payload)'''),
                dict(id=task_id,run=pid,payload=json.dumps(task,ensure_ascii=False)))
        result=self.client.get('/admin/api/tasks?q='+task_id)
        self.assertEqual(result.status_code,200,result.data)
        item=result.json['items'][0]
        self.assertEqual(item['status'],'completed')
        self.assertEqual(item['project_status'],'archived')

    def test_new_template_is_published_on_upload(self):
        data={'file':(self.archive(),'publish-template.zip'),'batch_upload':'1'}
        result=self.client.post('/admin/api/templates/upload',data=data,headers=self.headers)
        self.assertEqual(result.status_code,201,result.data)
        self.assertTrue(result.json['published'])
        item=self.client.get('/admin/api/templates').json['items'][0]
        self.assertEqual((item['status'],item['heat_base'],item['show_heat']),(1,10000,True))
        self.assertEqual(item['standard_price_fen'],1990)

    def test_project_order_search_and_detail(self):
        self.ensure_projects_table()
        pid='p2_'+'d'*32; payload=dict(project_id=pid,wx_openid='wx_customer',
            status='completed',stage='video',template_code='P2T-test',
            template=dict(video_name='漓江双人成片',actor_count=2,location_association='漓江'),
            roles=[dict(character_id='a',display_name='游客',person_uid='1',check_status='passed')],
            order_no='P2ORDER',payment_mode='allinpay',payment_status='paid',
            amount_fen=10,amount_paid=10,progress=100,created_at=1,paid_at=2)
        with self.store.engine.begin() as db:
            db.execute(text('''INSERT INTO phase2_projects
                (id,owner_id,status,runnable,due,paid_at,order_no,payload)
                VALUES(:id,:owner,'completed',0,0,2,:order,:payload)'''),
                dict(id=pid,owner='wx_customer',order='P2ORDER',
                     payload=json.dumps(payload,ensure_ascii=False)))
        result=self.client.get('/admin/api/projects?q=P2ORDER&status=completed&payment=allinpay')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.json['total'],1)
        self.assertEqual(result.json['items'][0]['template_name'],'漓江双人成片')
        detail=self.client.get('/admin/api/projects/'+pid)
        self.assertEqual(detail.status_code,200,detail.data)
        self.assertEqual(detail.json['order_no'],'P2ORDER')

    def test_user_monitor_exposes_prompts_and_input_output_images(self):
        self.ensure_projects_table()
        pid='p2_'+'e'*32; task_id='f'*32; now=time.time()
        project=dict(project_id=pid,wx_openid='wx_monitor',status='completed',stage='video',
                     template_code='P2T-monitor',
                     template=dict(video_name='过程监控样片',actor_count=1,location_association='漓江'),
                     roles=[dict(character_id='a',display_name='游客',person_uid='1',check_status='passed',
                                 photo_url='local-media:api/v2/assets/asset_'+'1'*32)],
                     portrait_url='local-media:api/v2/portraits/'+'2'*32,
                     video_url='local-media:api/v2/results/'+'3'*32,
                     order_no='P2MONITOR',amount_paid=10,created_at=now-10,
                     retention_expires_at=now+86400)
        task=dict(task_id=task_id,type='video',status='completed',created_at=now-5,
                  video_process=dict(prompt='测试视频提示词',
                                     inputs=[{'kind':'face','url':'local-media:aitravel/api/v1/file/download/wx/a.jpg'}],
                                     outputs=[{'kind':'video','url':'local-media:api/v2/results/'+'3'*32}]),
                  steps={'video_submit':{'status':'done','result':'provider-task'}})
        with self.store.engine.begin() as db:
            db.execute(text('INSERT INTO phase2_projects(id,owner_id,status,runnable,due,paid_at,order_no,payload) '
                            'VALUES(:id,:owner,\'completed\',0,0,:paid,:order,:payload)'),
                       dict(id=pid,owner='wx_monitor',paid=now,order='P2MONITOR',
                            payload=json.dumps(project,ensure_ascii=False)))
            db.execute(text('INSERT INTO phase2_tasks(id,kind,run_id,status,next_run,payload) '
                            'VALUES(:id,\'video\',:run,\'completed\',0,:payload)'),
                       dict(id=task_id,run=pid,payload=json.dumps(task,ensure_ascii=False)))
            db.execute(text('INSERT INTO phase2_task_events(id,task_id,kind,status,message,created) '
                            'VALUES(:id,:task,\'video\',\'completed\',\'生成完成\',:created)'),
                       dict(id='1'*32,task=task_id,created=now))
        users=self.client.get('/admin/api/user-monitor?q=wx_monitor')
        self.assertEqual(users.status_code,200,users.data)
        self.assertEqual(users.json['items'][0]['owner_id'],'wx_monitor')
        detail=self.client.get('/admin/api/user-monitor/wx_monitor')
        self.assertEqual(detail.status_code,200,detail.data)
        item=detail.json['projects'][0]
        self.assertTrue(item['portrait_url'].startswith('https://test.example.com/media/'))
        self.assertEqual(item['tasks'][0]['prompt'],'测试视频提示词')
        self.assertTrue(item['tasks'][0]['inputs'])
        self.assertTrue(item['tasks'][0]['outputs'])
        self.assertEqual(item['tasks'][0]['events'][0]['message'],'生成完成')
        lazy=self.client.get('/admin/api/user-monitor/wx_monitor?tasks=0')
        self.assertEqual(lazy.status_code,200,lazy.data)
        self.assertEqual(lazy.json['projects'][0]['tasks'],[])
        project_monitor=self.client.get('/admin/api/projects/'+pid+'/monitor')
        self.assertEqual(project_monitor.status_code,200,project_monitor.data)
        self.assertEqual(project_monitor.json['tasks'][0]['prompt'],'测试视频提示词')
        task_detail=self.client.get('/admin/api/tasks/'+task_id)
        self.assertEqual(task_detail.status_code,200,task_detail.data)
        self.assertEqual(task_detail.json['prompt'],'测试视频提示词')
        self.assertTrue(task_detail.json['inputs'])
        self.assertEqual(task_detail.json['events'][0]['message'],'生成完成')

    def test_service_logs_redact_access_tokens(self):
        ensure_service_log_table(self.store.engine)
        with self.store.engine.begin() as db:
            db.execute(text('''INSERT INTO phase2_service_logs
                (service,category,level,message,context,created)
                VALUES('phase2','video','info','failed',
                'https://example.test/a.mp4?access=secret-token&x=1',:created)'''),
                dict(created=time.time()))
        result=self.client.get('/admin/api/service-logs')
        self.assertNotIn('secret-token',result.get_data(as_text=True))
        self.assertIn('redacted',result.get_data(as_text=True))

    def test_real_template_upload_edit_preview_and_audit(self):
        code=self.upload(); item=self.client.get('/admin/api/templates').json['items'][0]
        self.assertEqual(item['template_code'],code); self.assertEqual(item['status'],1)
        self.assertEqual(item['real_selections'],0); self.assertEqual(item['heat_base'],10000)
        self.assertTrue(item['show_heat']); self.assertEqual(item['display_selections'],10000)
        self.assertNotIn('managed_runtime',item)
        body=dict(status=1,heat_base=32000,show_heat=True,edit_revision=item['edit_revision'])
        self.assertEqual(self.client.patch('/admin/api/templates/'+code,json=body,headers=self.headers).status_code,200)
        updated=self.client.get('/admin/api/templates').json['items'][0]
        self.assertEqual(updated['display_selections'],32000); self.assertEqual(updated['real_selections'],0)
        self.assertEqual(self.client.patch('/admin/api/templates/'+code,json=body,headers=self.headers).status_code,422)
        self.assertGreaterEqual(self.client.get('/admin/api/logs').json['total'],3)
        relative=updated['cover'].split('/assets/managed/')[1]
        media=self.client.get('/media-assets/'+relative)
        self.assertEqual(media.status_code,200)
        self.assertEqual(media.mimetype,'image/webp')
        self.assertEqual(self.client.get('/media-assets/'+relative.rsplit('/',1)[0]+'/1-基础元数据.txt').status_code,404)
        overview=self.client.get('/admin/api/overview').json
        self.assertEqual(overview['real_selections'],0);self.assertEqual(overview['display_baseline'],32000)

    def test_admin_lists_30_second_segment_and_outfit_summary(self):
        source=create_multi_segment_template_folder(self.folder/'multi','30秒双段模板')
        archive=archive_template(source,'30秒双段模板.zip')
        result=self.client.post('/admin/api/templates/upload',
            data={'file':(io.BytesIO(archive.read_bytes()),archive.name),'batch_upload':'1'},
            headers=self.headers)
        self.assertEqual(result.status_code,201,result.data)
        self.assertEqual(result.json['duration_seconds'],30)
        self.assertEqual(result.json['segment_count'],2)
        listing=self.client.get('/admin/api/templates?duration=30').json
        self.assertEqual(listing['total'],1)
        item=listing['items'][0]
        self.assertEqual((item['duration_seconds'],item['segment_count'],item['portrait_group']),(30,2,'B'))
        self.assertEqual([group['code'] for group in item['segments']],['A','B'])
        self.assertEqual([group['character_count'] for group in item['segments']],[2,2])
        self.assertEqual([character['character_id'] for character in item['segments'][0]['characters']],
                         ['男主角A','女主角A'])
        self.assertTrue(all(character['outfit_configured'] for group in item['segments'] for character in group['characters']))
        self.assertTrue(all(group['has_storyboard'] for group in item['segments']))
        self.assertEqual(self.client.get('/admin/api/templates?duration=15').json['total'],0)

    def test_batch_heat_updates_every_selected_template(self):
        first=self.upload()
        second='P2B-BATCH-HEAT-2'
        with self.store.engine.begin() as db:
            item=json.loads(db.execute(text('SELECT payload_json FROM phase2_templates WHERE template_code=:code'),dict(code=first)).scalar())
            item.update(template_code=second,video_name='批量热度测试二')
            db.execute(text('''INSERT INTO phase2_templates
                (template_code,location_association,video_genre,actor_count,payload_json,
                 identity_hash,status,sort_order,created_at,updated_at)
                VALUES(:code,:scene,:genre,:count,:payload,:identity,1,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)'''),
                dict(code=second,scene=item['location_association'],genre=item['video_genre'],
                     count=item['actor_count'],payload=json.dumps(item,ensure_ascii=False),
                     identity='batch-heat-second'))
            db.execute(text('''INSERT INTO phase2_template_settings
                (template_code,heat_base,show_heat,revision) VALUES(:code,10000,1,1)'''),
                dict(code=second))
        response=self.client.post('/admin/api/templates/batch-heat',
            json=dict(codes=[first,second],heat_base=88000),headers=self.headers)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.json['updated'],2)
        items={item['template_code']:item for item in self.client.get('/admin/api/templates').json['items']}
        self.assertEqual(items[first]['heat_base'],88000)
        self.assertEqual(items[second]['heat_base'],88000)

    def test_replacement_keeps_old_assets_and_snapshot(self):
        code=self.upload()
        with self.store.engine.connect() as db: old=json.loads(db.execute(text('SELECT payload_json FROM phase2_templates')).scalar())
        item=self.client.get('/admin/api/templates').json['items'][0]
        self.store.edit(code,dict(status=0,heat_base=23456,show_heat=False,edit_revision=item['edit_revision']),'admin')
        runtime=build_runtime_template(old); self.upload(code,publish=False)
        with self.store.engine.connect() as db: new=json.loads(db.execute(text('SELECT payload_json FROM phase2_templates')).scalar())
        self.assertNotEqual(old['asset_revision'],new['asset_revision'])
        self.assertTrue((self.folder/'assets'/old['asset_revision']).is_dir())
        self.assertEqual(runtime,build_runtime_template(old))
        self.assertEqual(normalize_template(new)['actor_count'],len(new['character_settings']))
        updated=self.client.get('/admin/api/templates').json['items'][0]
        self.assertEqual((updated['status'],updated['heat_base'],updated['show_heat']), (1,23456,True))
        self.assertEqual(updated['edit_revision'],item['edit_revision']+2)

    def test_browser_batch_upload_skips_repeat_without_replacing(self):
        filename='漓江-批量测试模板.zip'
        data={'file':(self.archive(),filename),'batch_upload':'1','publish':'1'}
        first=self.client.post('/admin/api/templates/upload',data=data,headers=self.headers)
        self.assertEqual(first.status_code,201,first.data)
        self.assertTrue(first.json['template_code'].startswith('P2B-'))
        item=self.client.get('/admin/api/templates').json['items'][0]
        self.assertEqual((item['status'],item['heat_base'],item['show_heat']), (1,10000,True))
        with self.store.engine.connect() as db:
            old=json.loads(db.execute(text('SELECT payload_json FROM phase2_templates')).scalar())
        second=self.client.post('/admin/api/templates/upload',data={'file':(self.archive(),filename),'batch_upload':'1','publish':'1'},headers=self.headers)
        self.assertEqual(second.status_code,200,second.data)
        self.assertEqual(second.json['status'],'skipped')
        with self.store.engine.connect() as db:
            self.assertEqual(db.execute(text('SELECT COUNT(*) FROM phase2_templates')).scalar(),1)
            current=json.loads(db.execute(text('SELECT payload_json FROM phase2_templates')).scalar())
        self.assertEqual(current['asset_revision'],old['asset_revision'])

    def test_browser_batch_upload_skips_same_name_from_manual_upload(self):
        manual=self.upload()
        response=self.client.post('/admin/api/templates/upload',data={'file':(self.archive(),'another-name.zip'),'batch_upload':'1'},headers=self.headers)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.json['status'],'skipped')
        self.assertEqual(response.json['template_code'],manual)
        self.assertEqual(len(list((self.folder/'assets').iterdir())),1)

    def test_zip_traversal_and_role_mismatch_are_rejected(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z: z.writestr('../escape.txt','bad')
        out.seek(0)
        with self.assertRaises(ValueError): import_archive(out,self.folder/'assets')
        source=self.archive(); bad=io.BytesIO()
        with zipfile.ZipFile(source) as original,zipfile.ZipFile(bad,'w') as z:
            for info in original.infolist():
                value=original.read(info)
                if info.filename.endswith('1-基础元数据.txt'):
                    data=json.loads(value);data['module_1']['actor_count']=99;value=json.dumps(data).encode()
                z.writestr(info.filename,value)
        bad.seek(0)
        with self.assertRaises(ValueError): import_archive(bad,self.folder/'assets')
        self.assertEqual(list((self.folder/'assets').iterdir()),[])

    def test_task_status_changes_are_logged_without_reference_urls(self):
        journal=TaskJournal(self.url);self.addCleanup(journal.engine.dispose)
        p,_=journal.create('video','project',dict(template_code='P2S-test',role_mapping=[dict(user_face_url='private-photo')]))
        p['status']='needs_review';p['error']='云端提交结果待核实'
        p['steps']['video_submit']={'status':'submitting','started_at':1}
        journal.put(p)
        logs=self.client.get('/admin/api/logs?kind=tasks').json['items']
        self.assertEqual(len(logs),2); self.assertNotIn('private-photo',json.dumps(logs))
        tasks=self.client.get('/admin/api/tasks?status=needs_review').json
        self.assertEqual(tasks['total'],1);self.assertNotIn('private-photo',json.dumps(tasks))
        resolved=self.client.post('/admin/api/tasks/'+p['task_id']+'/resolve',
            json=dict(action='not_submitted'),headers=self.headers)
        self.assertEqual(resolved.status_code,200,resolved.data)
        self.assertEqual(journal.get(p['task_id'])['status'],'pending')

    def test_portrait_monitor_reconstructs_final_seedream_order(self):
        prefix='https://test.example.com/media/aitravel/api/v1/file/download/test/'
        payload={
            'status':'completed',
            'role_mapping':[
                dict(role_index=1,character_id='女主角A',source='template',
                     face_index=1,three_view_index=3,
                     template_face_url=prefix+'female.jpg',
                     three_view_url=prefix+'female.jpg'),
                dict(role_index=2,character_id='男主角A',source='user_face',
                     face_index=2,three_view_index=4,
                     user_face_url=prefix+'male-face.jpg',
                     three_view_url=prefix+'male-view.jpg'),
            ],
            'portrait_process':{
                'prompt':'测试定妆照提示词',
                'inputs':[
                    dict(character_id='女主角A',source='template',url=prefix+'female.jpg',
                         costume_url=prefix+'female-costume.jpg'),
                    dict(character_id='男主角A',source='user_face',url=prefix+'male-face.jpg',
                         costume_url=prefix+'male-costume.jpg'),
                    dict(character_id=None,source='template',url=prefix+'scene.jpg'),
                ],
            },
        }
        result=monitor_task('portrait-task','character',payload)
        self.assertEqual([item['index'] for item in result['reference_images']],[1,2,3,4,5])
        self.assertEqual([item['kind'] for item in result['reference_images']],
                         ['face','face','three_view','three_view','scene'])
        self.assertEqual(result['inputs'],[item['url'] for item in result['reference_images']])
        self.assertEqual(len(result['reference_images']),5)
        self.assertEqual(len(result['audit_inputs']),5)
        self.assertTrue(result['reference_images'][0]['url'].endswith('female.jpg'))
        self.assertTrue(result['reference_images'][1]['url'].endswith('male-face.jpg'))
        self.assertTrue(result['reference_images'][3]['url'].endswith('male-view.jpg'))

    def test_admin_media_urls_use_twelve_hour_default_ttl(self):
        output='local-media:api/v2/results/'+'a'*32
        direct='https://test.example.com/api/v2/results/'+'b'*32
        payload={
            'status':'completed',
            'portrait_process':{'outputs':[{'kind':'video','url':output},{'kind':'video','url':direct}]},
            'result':output,
        }
        with patch.dict(os.environ,{
                'PHASE2_AUTH_MODE':'wechat',
                'PUBLIC_BASE_URL':'https://test.example.com/media/install',
                'MEDIA_TOKEN_TTL_SECONDS':'43200'}):
            result=monitor_task('media-ttl','video',payload)
        url=next(item for item in result['outputs'] if '/'+'b'*32 in item)
        self.assertEqual(urlsplit(url).path,'/api/v2/results/'+'b'*32)
        token=parse_qs(urlsplit(url).query)['access'][0]
        claims=jwt.decode(token,os.environ['AITRAVEL_JWT_SECRET'],algorithms=['HS256'],audience='phase2-media')
        self.assertEqual(claims['exp']-claims['iat'],43200)

    def test_dual_segment_prompt_and_grouped_reference_records(self):
        prefix='https://test.example.com/media/install/assets/'
        payload={
            'status':'completed',
            'video_process':{
                'prompt':[
                    {'group':'A','text':'【A】模板分镜图'},
                    {'group':'B','text':'【B】模板分镜图'},
                ],
                'inputs':{
                    'A':[{'index':1,'kind':'face','character_id':'男主角A',
                          'url':prefix+'face.jpg','note':'用户自拍照'}],
                    'B':[{'index':1,'kind':'face','character_id':'男主角A',
                          'url':prefix+'face.jpg','note':'用户自拍照'},
                         {'index':2,'kind':'scene','url':prefix+'scene.jpg'}],
                },
            },
        }
        result=monitor_task('video-task','video',payload)
        self.assertIsInstance(result['prompt'],list)
        self.assertEqual([item['group'] for item in result['input_records']],['A','B','B'])
        self.assertEqual([item.get('kind') for item in result['input_records']],
                         ['face','face','scene'])

    def test_admin_csp_allows_the_private_tos_bucket(self):
        with patch.dict(os.environ,{
                'TOS_BUCKET':'unit-test-bucket',
                'TOS_REGION':'cn-beijing'}):
            response=self.client.get('/admin/')
        self.assertEqual(response.status_code,200)
        policy=response.headers['Content-Security-Policy']
        self.assertIn('https://unit-test-bucket.tos-cn-beijing.volces.com',policy)

if __name__=='__main__': unittest.main()
