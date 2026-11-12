import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from phase2.store import Store
from template_fixture import write_managed_catalog


class Ownership(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,{'APP_ENV':'test','PHASE2_AUTH_MODE':'wechat','AITRAVEL_JWT_SECRET':'x'*64,
            'PHASE2_DB_HOST':'','PHASE2_STATE_BACKEND':'sqlite'})
        env.start(); self.addCleanup(env.stop)
        from phase2 import phase2_app as api
        from phase2.auth import issue
        self.api=api; self.store=Store(Path(self.tmp.name)/'state.db')
        catalog=write_managed_catalog(Path(self.tmp.name)/'catalog.json')
        p=patch.object(api,'store',self.store); p.start(); self.addCleanup(p.stop)
        p=patch.object(api,'DATA_FILE',catalog); p.start(); self.addCleanup(p.stop)
        self.a={'Authorization':'Bearer '+issue('alice')}; self.b={'Authorization':'Bearer '+issue('bob')}
        self.client=api.app.test_client()

    def test_other_users_cannot_read_change_order_or_mark_project(self):
        item=self.client.get('/api/v2/templates').json['data']['items'][0]
        body={'template_code':item['template_code'],'request_id':'same-request'}
        pa=self.client.post('/api/v2/projects',headers=self.a,json=body).json['data']
        pb=self.client.post('/api/v2/projects',headers=self.b,json=body).json['data']
        self.assertNotEqual(pa['project_id'],pb['project_id'])
        for suffix,method in [('', 'get'),('/status','get'),('/order','post'),('/assets','post'),('/read','post'),('/participation','post')]:
            self.assertEqual(getattr(self.client,method)('/api/v2/projects/'+pa['project_id']+suffix,headers=self.b).status_code,404)
        self.assertEqual(self.client.get('/api/v2/projects/'+pa['project_id']).status_code,401)
        self.assertEqual(self.client.get('/api/v2/templates/P2S-'+pa['project_id']+'/runtime',headers=self.b).status_code,403)
        self.assertEqual(self.client.get('/api/v2/templates/'+item['template_code']+'/runtime').status_code,403)
        with self.store.transaction() as db:
            record=self.store.get(pa['project_id'],db);record['order_no']='test-order';self.store.put(record,db)
        self.assertEqual(self.client.post('/api/v2/works/status',json={'ids':[pa['project_id']]},headers=self.b).json['data']['items'],[])
        self.assertEqual(len(self.client.post('/api/v2/works/status',json={'ids':[pa['project_id']]},headers=self.a).json['data']['items']),1)

    def test_login_uses_server_exchange_not_client_user_id(self):
        with patch.dict(os.environ,{'WECHAT_APP_ID':'test-app','WECHAT_APP_SECRET':'test-secret'}),patch(
                'phase2.auth.requests.get',return_value=Mock(json=lambda:{'openid':'verified-wechat-user'},raise_for_status=lambda:None)) as exchange:
            response=self.client.post('/api/v2/auth/login',json={'code':'wx-code','openid':'attacker'})
            self.assertEqual(response.status_code,200)
            from phase2.auth import verify
            claims=verify(response.json['data']['token'],'phase2-user')
            self.assertNotEqual(claims['sub'],'attacker')
            self.assertEqual(exchange.call_args.kwargs['params']['js_code'],'wx-code')

    def test_media_token_is_bound_to_one_file_and_expires(self):
        from phase2.auth import issue
        token=issue('alice','phase2-media',path='/api/v2/results/'+'a'*32)
        self.assertEqual(self.client.get('/api/v2/results/'+'b'*32+'?access='+token).status_code,401)
        expired=issue('alice','phase2-media',ttl=-1,path='/api/v2/results/'+'a'*32)
        self.assertEqual(self.client.get('/api/v2/results/'+'a'*32+'?access='+expired).status_code,401)

    def test_media_stream_forwards_range_and_returns_partial_content(self):
        upstream=Mock(status_code=206,headers={
            'Content-Length':'3','Content-Range':'bytes 0-2/12',
            'Accept-Ranges':'bytes'})
        upstream.iter_content.return_value=[b'abc']
        with self.api.app.test_request_context(
                '/api/v2/results/'+'a'*32+'?download=1',
                headers={'Range':'bytes=0-2'}), patch.object(
                    self.api.requests,'get',return_value=upstream) as fetch:
            response=self.api._stream_remote(
                'https://storage.example/video.mp4','video/mp4')
            self.assertEqual(response.status_code,206)
            self.assertEqual(response.get_data(),b'abc')
            self.assertEqual(response.headers['Content-Range'],'bytes 0-2/12')
            self.assertEqual(response.headers['Accept-Ranges'],'bytes')
            fetch.assert_called_once()
            self.assertEqual(fetch.call_args.kwargs['headers'],{'Range':'bytes=0-2'})
            upstream.close.assert_called_once()

    def test_watch_feed_returns_only_lightweight_state_fields(self):
        item=self.client.get('/api/v2/templates').json['data']['items'][0]
        project=self.client.post('/api/v2/projects',headers=self.a,json={
            'template_code':item['template_code'],'request_id':'watch-feed-request'}).json['data']
        with self.store.transaction() as db:
            record=self.store.get(project['project_id'],db)
            record.update(order_no='watch-order',status='completed',portrait_url='private/path',
                          video_url='private/video',error='')
            self.store.put(record,db)
        result=self.client.get('/api/v2/works/watch',headers=self.a)
        self.assertEqual(result.status_code,200)
        state=result.json['data']['items'][0]
        self.assertEqual(state,{
            'project_id':project['project_id'],'status':'completed',
            'portrait_url':True,'video_url':True,
            'video_name':item['video_name'],'error':''})


if __name__=='__main__': unittest.main()
