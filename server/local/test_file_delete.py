"""Internal generated-file deletion used by the 90-day retention worker."""
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.internal_auth import local_headers

ROOT = Path(__file__).resolve().parents[2]


class GeneratedFileDeletionTests(unittest.TestCase):
    def test_delete_requires_service_identity_and_removes_only_the_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / 'wx_user' / 'generated.jpg'
            target.parent.mkdir()
            target.write_bytes(b'image')
            with patch.dict(os.environ, {
                'UPLOAD_ROOT': str(root),
                'AITRAVEL_JWT_SECRET': 'unit-test-secret-' * 4,
            }):
                spec = importlib.util.spec_from_file_location(
                    'file_app_retention_test', ROOT / 'server/file/file_app.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                client = module.app.test_client()
                url = '/api/v1/file/delete/wx_user/generated.jpg'
                self.assertEqual(client.delete(url).status_code, 401)
                self.assertEqual(client.delete(
                    url, headers=local_headers('local-phase2')).status_code, 401)
                self.assertEqual(client.delete(
                    url, headers=local_headers('phase2-file')).status_code, 204)
            self.assertFalse(target.exists())

    def test_uploaded_legacy_file_is_moved_to_tos_and_removed_locally(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {
                'UPLOAD_ROOT': str(root),
                'AITRAVEL_JWT_SECRET': 'unit-test-secret-' * 4,
                'LOCAL_CLOUD_ENABLED': 'true',
            }), patch(
                'common.storage_client.upload_asset',
                return_value='https://api.example/api/v2/assets/file_test') as upload:
                spec = importlib.util.spec_from_file_location(
                    'file_app_tos_upload_test', ROOT / 'server/file/file_app.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                client = module.app.test_client()
                response = client.post(
                    '/api/v1/file/upload',
                    headers=local_headers('phase2-file'),
                    data={
                        'wx_openid': 'wx_user',
                        'file': (io.BytesIO(b'generated image'), 'generated.jpg'),
                    },
                )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.json['data']['storage'], 'tos')
            upload.assert_called_once()
            self.assertEqual(list(root.glob('wx_user/*')), [])


if __name__ == '__main__':
    unittest.main()
