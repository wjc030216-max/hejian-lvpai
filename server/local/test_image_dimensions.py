"""Exercise the HTTP image endpoint through the provider with paid calls mocked."""
import base64
import os
import unittest
from unittest.mock import Mock, patch

from common.seedream_service import _upload_generated_image, create_app
from common import http_client


class ImageDimensions(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'AITRAVEL_JWT_SECRET':'unit-test-secret-'*4})
        env.start(); self.addCleanup(env.stop)
        from common.internal_auth import local_headers
        self.client = create_app('image-dimension-test').test_client()
        self.client.environ_base['HTTP_AUTHORIZATION']=local_headers('phase2-image')['Authorization']

    def test_portrait_and_three_view_use_explicit_official_dimensions(self):
        for ratio, size in [('9:16', '800x1424'), ('16:9', '1424x800')]:
            with self.subTest(ratio=ratio):
                response = Mock(status_code=200)
                response.json.return_value = {'data': [{'b64_json': 'dGVzdA=='}]}
                with patch.dict(os.environ, {'APP_ENV': 'test', 'SEEDREAM_MIN_SIZE': '1K', 'SEEDREAM_API_KEY': 'unit-test'}), \
                        patch('common.local_guard.require_cloud_enabled'), \
                        patch('common.local_guard.validate_media_url'), \
                        patch.object(http_client.session(), 'post', return_value=response) as post, \
                        patch('common.seedream_service._upload_generated_image', return_value='stored-image'):
                    result = self.client.post('/api/v1/images/generate', json={
                        'user_id': 'test-user', 'prompt': 'test portrait',
                        'reference_image_urls': ['https://example.test/landscape-reference.png'],
                        'aspect_ratio': ratio, 'size': '768x1024',
                    })
                self.assertEqual(result.status_code, 200)
                payload = post.call_args.kwargs['json']
                self.assertEqual(payload['size'], size)
                self.assertNotIn('aspect_ratio', payload)  # Not an Ark API field.
                self.assertEqual(result.json['data']['output_size'], size)
                self.assertEqual(result.json['data']['size'], '1K')

    def test_invalid_ratio_is_rejected_before_cloud_submission(self):
        with patch.dict(os.environ, {'APP_ENV': 'test', 'SEEDREAM_API_KEY': 'unit-test'}), \
                patch('common.local_guard.require_cloud_enabled'), \
                patch.object(http_client.session(), 'post') as post:
            result = self.client.post('/api/v1/images/generate', json={
                'user_id': 'test-user', 'prompt': 'test', 'aspect_ratio': 'invalid',
            })
        self.assertEqual(result.status_code, 400)
        post.assert_not_called()

    def test_generated_image_is_written_to_tos_without_file_service(self):
        image = b'\x89PNG\r\n\x1a\n' + b'generated-image'
        with patch(
                'common.storage_client.upload_asset_bytes',
                return_value='https://api.example/api/v2/assets/asset_test') as upload:
            url = _upload_generated_image(
                'wx_user', base64.b64encode(image).decode())
        self.assertEqual(url, 'https://api.example/api/v2/assets/asset_test')
        asset_id, content, filename, mime = upload.call_args.args
        self.assertRegex(asset_id, r'^asset_[0-9a-f]{32}$')
        self.assertEqual(content, image)
        self.assertEqual(filename, asset_id + '.png')
        self.assertEqual(mime, 'image/png')


if __name__ == '__main__':
    unittest.main()
