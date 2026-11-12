"""Ninety-day user-data retention and admin process monitoring."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from phase2.retention_service import RETENTION_SECONDS, RetentionService
from phase2.store import Store


class _SqlResult:
    def mappings(self):
        return [{'payload': '{}'}, {'payload': '{"id":2}'}]


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.uploads = root / 'uploads'
        self.cache = self.uploads / 'share-cache'
        self.uploads.mkdir()
        self.cache.mkdir()
        self.store = Store(root / 'retention.db')
        self.pid = 'p2_' + 'a' * 32
        self.photo = self.uploads / self.pid / ('asset_' + 'b' * 32 + '.jpg')
        self.photo.parent.mkdir()
        self.photo.write_bytes(b'photo')
        now = time.time()
        self.project = {
            'project_id': self.pid,
            'wx_openid': 'wx_user',
            'template': {
                'video_name': '测试旅拍',
                'location_association': '漓江',
                'managed_runtime': {'portrait_prompt': '不应保留的过程提示词'},
            },
            'template_code': 'P2T-test',
            'runtime_code': 'P2S-' + self.pid,
            'status': 'completed',
            'stage': 'video',
            'roles': [{'character_id': 'a', 'person_uid': '1',
                       'photo_asset_id': 'asset_' + 'b' * 32,
                       'photo_path': str(self.photo),
                       'photo_url': 'https://example/api/v2/assets/asset_' + 'b' * 32}],
            'portrait_url': 'https://example/api/v2/portraits/' + 'c' * 32,
            'portrait_asset_id': 'c' * 32,
            'video_url': 'https://example/api/v2/results/' + 'd' * 32,
            'video_task_id': 'd' * 32,
            'order_no': 'P2PAYTEST',
            'payment_mode': 'allinpay',
            'payment_status': 'paid',
            'amount_paid': 10,
            'refund_status': 'succeeded',
            'created_at': now - RETENTION_SECONDS - 86400,
            'completed_at': now - RETENTION_SECONDS - 3600,
            'retention_expires_at': now - 1,
            'retention_cleanup': 'pending',
            'subscribe_openid': 'openid',
        }
        self.store.put(self.project)
        self.task = {
            'task_id': 'e' * 32,
            'type': 'image',
            'status': 'completed',
            'request': {
                'prompt': '过程提示词',
                'reference_image_urls': [
                    'local-media:aitravel/api/v1/file/download/wx_user/input.jpg',
                ],
            },
            'steps': {'asset:1': {'status': 'done', 'result': 'asset_fixture_1'}},
            'reference_plan': [{'asset_id': 'asset_fixture_1'}],
        }
        with self.store.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS phase2_tasks '
                       '(id TEXT PRIMARY KEY, run_id TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS phase2_task_events '
                       '(id TEXT PRIMARY KEY, task_id TEXT)')
            db.execute('INSERT INTO phase2_tasks VALUES (?,?,?)',
                       (self.task['task_id'], self.pid, json.dumps(self.task)))

    def test_expired_user_process_and_media_are_cleaned_but_financial_record_remains(self):
        due = self.store.projects_due_retention()
        self.assertEqual([item['project_id'] for item in due], [self.pid])
        service = RetentionService(self.store, self.uploads, self.cache)
        with patch('common.storage_client.delete_portrait') as portrait, patch(
                'common.storage_client.delete_result') as video, patch(
                'common.storage_client.delete_local_video') as local_video, patch(
                'common.storage_client.delete_asset') as asset, patch(
                'common.storage_client.delete_file_url') as generated, patch(
                'common.video_asset_client.delete_video_asset') as video_asset:
            self.assertTrue(service.cleanup(self.project))
        portrait.assert_called_once_with('c' * 32)
        video.assert_called_once_with('d' * 32)
        local_video.assert_called_once_with('wx_user', 'd' * 32)
        asset.assert_called_once_with('asset_' + 'b' * 32)
        generated.assert_called_once()
        video_asset.assert_called_once_with('asset_fixture_1')
        updated = self.store.get(self.pid)
        self.assertEqual(updated['status'], 'expired')
        self.assertEqual(updated['roles'], [])
        self.assertEqual(updated['portrait_url'], '')
        self.assertEqual(updated['video_url'], '')
        self.assertEqual(updated['order_no'], 'P2PAYTEST')
        self.assertEqual(updated['amount_paid'], 10)
        self.assertEqual(updated['refund_status'], 'succeeded')
        self.assertNotIn('managed_runtime', updated['template'])
        self.assertNotIn('不应保留的过程提示词', json.dumps(updated, ensure_ascii=False))
        self.assertFalse(self.photo.exists())
        with self.store.connection() as db:
            self.assertIsNone(db.execute('SELECT payload FROM phase2_tasks').fetchone())

    def test_mysql_result_wrapper_supports_fetchall(self):
        from phase2.mysql_store import Result
        rows = Result(_SqlResult()).fetchall()
        self.assertEqual([row['payload'] for row in rows], ['{}', '{"id":2}'])

    def test_retention_drains_multiple_batches(self):
        service = RetentionService(self.store, self.uploads, self.cache)
        first = [self.project] * 100
        with patch.object(self.store, 'projects_due_retention', side_effect=[first, []]), \
                patch.object(service, 'cleanup') as cleanup:
            self.assertEqual(service.run_once(), 100)
            self.assertEqual(cleanup.call_count, 100)

    def test_repeated_same_kind_events_are_preserved(self):
        with self.store.transaction() as db:
            record = self.store.get(self.pid, db)
            self.store.event(record, 'refund', '退款失败', db)
            self.store.event(record, 'refund', '退款成功', db)
        with self.store.connection() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM events WHERE project_id=? AND kind='refund'",
                (self.pid,),
            ).fetchone()[0]
        self.assertEqual(count, 2)


if __name__ == '__main__':
    unittest.main()
