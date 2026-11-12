"""Exercise phase-two role mapping and persisted portrait worker."""
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from common.portrait_provider import _build_role_bindings
from common.durable_generation import Generation
from common.task_journal import TaskJournal
from common.video_references import build_references

class SingleExperience(unittest.TestCase):
    def test_selected_last_role_survives_portrait_to_video_mapping(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=TaskJournal('sqlite:///'+str(Path(folder)/'tasks.db')); self.addCleanup(journal.engine.dispose)
            api=SimpleNamespace(logger=logging.getLogger('test'),_build_role_bindings=_build_role_bindings,PROMPT_STEP1='outfit',
                fetch_template_by_code=Mock(return_value=(['dress-a','dress-b','dress-c'],'scene',['default-a','default-b','default-c'],'',list('abc'),'three people')),
                call_internal_api=Mock(side_effect=['visitor-view','portrait']))
            worker=Generation('character',api,journal); self.addCleanup(worker.pool.shutdown)
            actors=[dict(character_id='c',image='visitor',source='user_face'),dict(character_id='b',image='default-b',source='template'),dict(character_id='a',image='default-a',source='template')]
            p,_=journal.create('character','run',dict(template_code='P2S-test',actors=actors)); worker.tick(p['task_id'])
            result=journal.get(p['task_id']); self.assertEqual(result['status'],'completed')
            calls=api.call_internal_api.call_args_list
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[0].kwargs['reference_urls'],['visitor','dress-c'])
            self.assertEqual(calls[1].kwargs['aspect_ratio'],'9:16')
            self.assertEqual(calls[1].kwargs['reference_urls'],['default-a','default-b','visitor','default-a','default-b','visitor-view','scene'])
            references=result['portrait_process']['reference_images']
            self.assertEqual([item['index'] for item in references],list(range(1,8)))
            self.assertEqual([item['kind'] for item in references],
                             ['face','face','face','three_view','three_view','three_view','scene'])
            self.assertEqual([item['character_id'] for item in references].count('c'),2)
            plan=build_references(list('abc'),result['role_mapping'],['scene'],'storyboard')
            urls=[item['url'] for item in plan]
            self.assertEqual([item['kind'] for item in plan if item['character_id']=='c'],['face','three_view'])
            self.assertEqual([item['kind'] for item in plan if item['character_id'] in ('a','b')],['template_character','template_character'])
            self.assertEqual(urls[:2],['visitor','visitor-view'])
            self.assertEqual(urls[2:4],['default-a','default-b'])
            self.assertEqual(urls[4:],['storyboard','scene'])
            self.assertNotIn('portrait',urls)
            worker.pool.shutdown(); journal.engine.dispose()

    def test_missing_photos_and_forged_defaults_are_rejected(self):
        for actors in ([dict(character_id='c',image='visitor')],
            [dict(character_id=c,image='default-'+c,source='template') for c in 'abc'],
            [dict(character_id='a',image='wrong',source='template'),dict(character_id='b',image='default-b',source='template'),dict(character_id='c',image='visitor')]):
            with self.assertRaises(RuntimeError): _build_role_bindings(actors,list('abc'),['dress-a','dress-b','dress-c'],['default-a','default-b','default-c'])

if __name__=='__main__': unittest.main()
