"""Fault injection without cloud calls; persistent restart and billing boundaries."""
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from common.task_journal import TaskJournal,CloudFailed
from common.durable_generation import Generation
from common.media_refs import transform


class DurableGeneration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.url='sqlite:///'+str(Path(self.temp.name)/'tasks.db')
        self.j=TaskJournal(self.url)
        self.addCleanup(self.j.engine.dispose)
        self.api=SimpleNamespace(logger=logging.getLogger('test'),
            fetch_template_prompt=Mock(return_value=('动作','music',['a'],[],[],['scene'],'storyboard')),
            create_asset=Mock(side_effect=['asset1','asset2','asset3','asset4']),wait_asset_active=Mock(return_value=True),
            call_volcano_video=Mock(return_value='cloud-original'),poll_volcano_task=Mock(return_value='source-video'),
            VIDEO_GENERATE_AUDIO=True,VIDEO_DURATION=15,VIDEO_WATERMARK=False)
        self.w=Generation('video',self.api,self.j); self.addCleanup(self.w.pool.shutdown)
        self.p,_=self.j.create('video','run',dict(template_code='P2S-test',character_task_id='char',role_mapping=[
            dict(character_id='a',user_face_url='face',three_view_url='view')]))
        self.target=Path(self.temp.name)/'final.mp4'; self.target.write_bytes(b'composed')

    def test_upload_failure_restart_never_resubmits_or_recomposes(self):
        with patch('local.results.save_local_video',return_value=str(self.target)) as compose, patch(
                'common.storage_client.upload_result',side_effect=[RuntimeError('storage unavailable'),'stored-video']) as upload:
            self.w.tick(self.p['task_id'])
            self.assertEqual(self.j.get(self.p['task_id'])['status'],'recovering')
            restarted=Generation('video',self.api,TaskJournal(self.url)); self.addCleanup(restarted.pool.shutdown)
            self.addCleanup(restarted.journal.engine.dispose)
            restarted.tick(self.p['task_id'])
            self.assertEqual(self.j.get(self.p['task_id'])['status'],'completed')
            self.assertEqual(upload.call_count,2); compose.assert_called_once()
        self.api.call_volcano_video.assert_called_once()
        self.api.poll_volcano_task.assert_called_once_with('cloud-original')
        saved = self.j.get(self.p['task_id'])
        self.assertIn('动作', saved['video_process']['prompt'])
        self.assertTrue(saved['video_process']['inputs'])
        self.assertEqual(saved['video_process']['outputs'][0]['url'], 'stored-video')

    def test_poll_failure_restart_uses_saved_cloud_id(self):
        self.api.poll_volcano_task.side_effect=[TimeoutError(),'source-video']
        with patch('local.results.save_local_video',return_value=str(self.target)),patch('common.storage_client.upload_result',return_value='stored'):
            self.w.tick(self.p['task_id']); self.w.tick(self.p['task_id'])
        self.api.call_volcano_video.assert_called_once()
        self.assertEqual([c.args for c in self.api.poll_volcano_task.call_args_list],[('cloud-original',)]*2)

    def test_response_lost_or_process_killed_after_submit_never_resends(self):
        self.api.call_volcano_video.side_effect=TimeoutError()
        self.w.tick(self.p['task_id']); self.w.tick(self.p['task_id'])
        p=self.j.get(self.p['task_id']); self.assertEqual(p['status'],'needs_review')
        self.api.call_volcano_video.assert_called_once()
        self.j.retry(p['task_id'],'run'); self.w.tick(p['task_id'])
        self.api.call_volcano_video.assert_called_once()
        # Crash before response was committed, status still running.
        p['status']='running'; self.j.put(p); self.w.tick(p['task_id'])
        self.api.call_volcano_video.assert_called_once()

    def test_only_explicit_cloud_failure_allows_new_generation(self):
        self.api.poll_volcano_task.side_effect=[CloudFailed(),'source-video']
        self.w.tick(self.p['task_id'])
        self.assertEqual(self.j.get(self.p['task_id'])['status'],'failed')
        self.j.retry(self.p['task_id'],'run')
        with patch('local.results.save_local_video',return_value=str(self.target)),patch('common.storage_client.upload_result',return_value='stored'):
            self.w.tick(self.p['task_id'])
        self.assertEqual(self.api.call_volcano_video.call_count,2)
        self.assertEqual(self.api.create_asset.call_count,4)

    def test_duplicate_creation_and_completed_tasks_are_reused_without_redis(self):
        for _ in range(5):
            p,new=self.j.create('video','run',dict(template_code='P2S-test'))
            self.assertFalse(new); self.assertEqual(p['task_id'],self.p['task_id'])
        self.assertEqual(self.j.latest('video','run')['task_id'],self.p['task_id'])

    def test_duplicate_retry_receipt_does_not_start_third_attempt(self):
        p=self.p; p.update(status='failed',cloud_failed=True); self.j.put(p)
        accepted=self.j.retry(p['task_id'],'run',2)
        self.assertEqual(accepted['attempt'],2)
        # Attempt two fails before its HTTP acknowledgement reaches the caller.
        accepted.update(status='failed',cloud_failed=True); self.j.put(accepted)
        duplicate=self.j.retry(p['task_id'],'run',2)
        self.assertEqual(duplicate['attempt'],2)
        self.assertEqual(duplicate['status'],'failed')

    def test_role_steps_survive_portrait_failure(self):
        a=SimpleNamespace(logger=logging.getLogger('test'),PROMPT_STEP1='换装',
            fetch_template_by_code=Mock(return_value=(['costume'],['scene'],['default'],'',['a'],'定妆照')),
            _build_role_bindings=Mock(return_value=[{'role_index':1,'character_id':'a','costume_url':'costume',
                'actor':{'image':'face','name':'角色'}}]),call_internal_api=Mock(side_effect=['view',TimeoutError(),'portrait']))
        p,_=self.j.create('character','run',dict(template_code='P2S-test',actors=[{'image':'face'}]))
        w=Generation('character',a,self.j); self.addCleanup(w.pool.shutdown)
        w.tick(p['task_id']); w.tick(p['task_id'])
        self.assertEqual(a.call_internal_api.call_count,3)
        self.assertEqual(self.j.get(p['task_id'])['status'],'completed')
        self.assertEqual(a.call_internal_api.call_args_list[1].kwargs['request_id'],a.call_internal_api.call_args_list[2].kwargs['request_id'])

    def test_tunnel_change_preserves_installation_relative_refs(self):
        with patch.dict(os.environ,{'PUBLIC_BASE_URL':'https://old.example/media/same','PHASE2_AUTH_MODE':'local'}):
            ref=transform('https://old.example/media/same/api/v2/assets/asset_abc',True)
        with patch.dict(os.environ,{'PUBLIC_BASE_URL':'https://new.example/media/same'}):
            self.assertEqual(transform(ref),'https://new.example/media/same/api/v2/assets/asset_abc')
            self.assertEqual(transform('https://evil.example/media/other/a'), 'https://evil.example/media/other/a')

    def test_expired_nonbillable_task_stops_without_retry(self):
        p=self.j.get(self.p['task_id'])
        p['deadline_at']=1
        self.j.put(p)
        self.w.tick(p['task_id'])
        saved=self.j.get(p['task_id'])
        self.assertEqual(saved['status'],'failed')
        self.assertFalse(saved.get('cloud_failed'))
        self.api.call_volcano_video.assert_not_called()


if __name__=='__main__': unittest.main()
