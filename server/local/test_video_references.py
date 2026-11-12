"""Actual phase-two video task boundaries, isolated from cloud and database services."""
import ast
import json
import logging
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from flask import Flask
from common.durable_generation import Generation,install
from common.task_journal import TaskJournal
from common.video_references import build_references, build_prompt
from common import video_provider

class VideoReferences(unittest.TestCase):
    def test_default_video_model_is_seedance_fast(self):
        self.assertEqual(video_provider.VOLC_MODEL, 'doubao-seedance-2-0-fast-260128')

    def test_reference_asset_delete_uses_project_scoped_action(self):
        response = Mock(status_code=200)
        with patch.object(video_provider, 'require_cloud_enabled'), patch.object(
                video_provider, 'request_api', return_value=response) as request, patch(
                'common.service_log.event'):
            video_provider.delete_asset('asset-example_1')
        body = json.loads(request.call_args.args[6])
        self.assertEqual(body, {'Id': 'asset-example_1', 'ProjectName': video_provider.ASSET_PROJECT_NAME})
        self.assertEqual(request.call_args.kwargs['action'], 'DeleteAsset')

    def test_missing_asset_group_fails_without_retrying_paid_video(self):
        response = Mock(status_code=404)
        response.json.return_value = {
            'ResponseMetadata': {'Error': {'Code': 'NotFound.group_id'}}}
        with patch.object(video_provider, 'require_cloud_enabled'), patch.object(
                video_provider, 'validate_media_url'), patch.object(
                video_provider, 'request_api', return_value=response), patch(
                'common.service_log.event'):
            with self.assertRaisesRegex(ValueError, '视频素材组不存在'):
                video_provider.create_asset('https://example.test/image.jpg')

    def test_one_two_three_roles_and_no_display_images(self):
        for count in (1,2,3):
            ids=[str(i) for i in range(count)]
            mapping=[dict(character_id=i,user_face_url='face'+i,three_view_url='view'+i,portrait_url='DO-NOT-SEND') for i in ids]
            plan=build_references(ids,list(reversed(mapping)),['scene'],'storyboard')
            self.assertEqual([item['url'] for item in plan],['face'+i for i in ids]+['view'+i for i in ids]+['storyboard','scene'])
            self.assertEqual([item['index'] for item in plan],list(range(1,2*count+3)))
            self.assertIn('镜头调度参考', build_prompt(plan, ids))

    def test_prompt_uses_official_image_n_notation(self):
        ids=['男主角A','女主角A','女主角B']
        mapping=[dict(character_id=c,source='user_face',user_face_url='f'+c,three_view_url='v'+c) for c in ids]
        plan=build_references(ids,mapping,['scene'],'storyboard')
        text=build_prompt(plan,ids)
        self.assertIn('[男主角A]整体形象、身高体型参考图片4', text)
        self.assertIn('人脸长相参考图片1', text)
        self.assertIn('镜头调度参考图片7', text)
        self.assertIn('场景参考图片8', text)
        self.assertNotIn('用户1人物设定三视图', text)
        self.assertNotIn('模板人物设定图', text)
        self.assertNotIn('（', text)
        self.assertNotIn('@', text)

    def test_missing_or_duplicate_roles_fail_before_api_calls(self):
        for mapping in ([dict(character_id='a',user_face_url='face')],[dict(character_id='a'),dict(character_id='a')]):
            with self.assertRaises(ValueError): build_references(['a'],mapping,['scene'],'storyboard')

    def test_video_worker_only_registers_allowed_references(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=TaskJournal('sqlite:///'+str(Path(folder)/'tasks.db')); self.addCleanup(journal.engine.dispose)
            api=SimpleNamespace(logger=logging.getLogger('test'),fetch_template_prompt=Mock(return_value=('action','',['hero'],[],[],['scene'],'storyboard')),
                create_asset=Mock(side_effect=['a1','a2','a3','a4']),wait_asset_active=Mock(return_value="active-url"),call_volcano_video=Mock(return_value='cloud-id'),
                poll_volcano_task=Mock(return_value='cloud-video'),VIDEO_GENERATE_AUDIO=True,VIDEO_DURATION=15,VIDEO_WATERMARK=True)
            worker=Generation('video',api,journal); self.addCleanup(worker.pool.shutdown)
            p,_=journal.create('video','run',dict(template_code='P2S-test',character_task_id='portrait',role_mapping=[dict(character_id='hero',user_face_url='face',three_view_url='view',portrait_url='DO-NOT-SEND')]))
            target=Path(folder)/'result.mp4'; target.write_bytes(b'result')
            with patch('local.results.save_local_video',return_value=str(target)),patch('common.storage_client.upload_result',return_value='saved-video'):
                worker.tick(p['task_id'])
            self.assertEqual(journal.get(p['task_id'])['status'],'completed')
            self.assertEqual([c.args[0] for c in api.create_asset.call_args_list],['face','view','storyboard','scene'])
            args=api.call_volcano_video.call_args.kwargs
            self.assertEqual(args['ratio'],'9:16')
            self.assertEqual([x['image_url']['url'] for x in args['content'][1:]],['asset://a1','asset://a2','asset://a3','asset://a4'])
            self.assertNotIn('DO-NOT-SEND',json.dumps(args))
            worker.pool.shutdown(); journal.engine.dispose()

    def test_video_sdk_receives_vertical_ratio(self):
        source=Path(__file__).parents[1]/'common/video_provider.py'
        fn=next(x for x in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(x,ast.FunctionDef) and x.name=='call_volcano_video')
        sdk=Mock(); sdk.content_generation.tasks.create.return_value.id='cloud-id'
        env=dict(time=time,logger=Mock(),hashlib=__import__('hashlib'),SEEDANCE_API_KEY='test',
                 VOLC_MODEL='test-model',Ark=Mock(return_value=sdk))
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),env)
        env['call_volcano_video']([dict(type='text',text='test')])
        self.assertEqual(sdk.content_generation.tasks.create.call_args.kwargs['ratio'],'9:16')
        self.assertEqual(env['Ark'].call_args.kwargs['max_retries'],0)

    def test_only_phase_two_generation_routes_exist(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=TaskJournal('sqlite:///'+str(Path(folder)/'tasks.db')); self.addCleanup(journal.engine.dispose)
            api=SimpleNamespace(app=Flask(__name__),logger=logging.getLogger('test'))
            with patch('common.durable_generation.TaskJournal',return_value=journal),patch.object(Generation,'start'):
                worker=install(api,'video'); self.addCleanup(worker.pool.shutdown)
            client=api.app.test_client()
            body=dict(wx_openid='run',template_code='P2S-test',character_task_id='portrait',role_mapping=[dict(character_id='a')])
            self.assertEqual(client.post('/api/v1/video/generate',json=body).status_code,202)
            for extra in ({'storyboard_image':'old'},{'portrait_url':'display'}):
                self.assertEqual(client.post('/api/v1/video/generate',json=dict(body,**extra)).status_code,400)
            self.assertEqual(client.get('/api/v1/video/list').status_code,404)
            self.assertEqual(client.post('/api/v1/image/generatescene',json={}).status_code,404)
            self.assertEqual(client.post('/api/v1/video/generate',json=dict(body,template_code='phase-one')).status_code,400)
            worker.pool.shutdown(); journal.engine.dispose()

if __name__=='__main__': unittest.main()
