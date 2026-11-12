"""Credential separation and TOS contract tests; no cloud network calls."""
import io
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

import tos
from PIL import Image
from common.internal_auth import local_headers
from common.provider_credentials import credential
from local.tos_app import create_app
from local.tos_storage import StorageConfig, ResultStore


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'APP_ENV': 'local-real', 'LOCAL_CLOUD_ENABLED': 'true',
            'AITRAVEL_JWT_SECRET': 'unit-test-secret-' * 4,
            'TOS_ACCESS_KEY_ID': 'tos-test-ak', 'TOS_ACCESS_KEY_SECRET': 'tos-test-sk',
            'TOS_SECURITY_TOKEN': '', 'TOS_BUCKET': 'aitravel-local-unittest',
            'TOS_ENDPOINT': 'https://tos-cn-beijing.volces.com', 'TOS_REGION': 'cn-beijing',
            'TOS_KEY_PREFIX': 'local-real/', 'TOS_SIGNED_URL_TTL_SECONDS': '3600',
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_generic_keys_are_never_used_locally(self):
        with patch.dict(os.environ, {'ARK_API_KEY': 'old', 'VOLC_ACCESS_KEY': 'old'}):
            self.assertEqual(credential('SEEDREAM_API_KEY', 'ARK_API_KEY'), '')
            self.assertEqual(credential('SEEDANCE_API_KEY', 'ARK_API_KEY'), '')
            self.assertEqual(credential('SEEDANCE_ASSET_ACCESS_KEY_ID', 'VOLC_ACCESS_KEY'), '')
        with patch.dict(os.environ, {'SEEDREAM_API_KEY': 'image-only', 'SEEDANCE_API_KEY': 'video-only'}):
            self.assertEqual(credential('SEEDREAM_API_KEY'), 'image-only')
            self.assertEqual(credential('SEEDANCE_API_KEY'), 'video-only')

    def test_storage_configuration_validates_required_settings(self):
        for values in [{'TOS_BUCKET': ''}, {'TOS_ENDPOINT': 'http://tos-cn-beijing.volces.com'},
                       {'TOS_KEY_PREFIX': 'production/'}, {'TOS_KEY_PREFIX': 'local-real/../prod/'},
                       {'TOS_REGION': 'cn-shanghai'}, {'TOS_ACCESS_KEY_ID': ''}]:
            with patch.dict(os.environ, values):
                with self.assertRaises(ValueError):
                    StorageConfig.from_env()

    def test_existing_bucket_name_requires_no_project_prefix(self):
        with patch.dict(os.environ, {'TOS_BUCKET': 'existing-video-bucket'}):
            config = StorageConfig.from_env()
            self.assertEqual(config.bucket, 'existing-video-bucket')
            self.assertEqual(ResultStore(config).object_key('f' * 32),
                             'local-real/videos/' + 'f' * 32 + '.mp4')

    def test_production_uses_production_storage_namespace(self):
        with patch.dict(os.environ, {
            'APP_ENV': 'production',
            'TOS_KEY_PREFIX': 'phase2-prod/',
        }):
            config = StorageConfig.from_env()
            self.assertEqual(ResultStore(config).object_key('d' * 32),
                             'phase2-prod/videos/' + 'd' * 32 + '.mp4')

    def test_official_sdk_uses_tos_credentials_and_private_upload(self):
        config = StorageConfig.from_env()
        with patch('local.tos_storage.tos.TosClientV2') as sdk:
            sdk.return_value.pre_signed_url.return_value.signed_url = 'https://signed.example/video'
            store = ResultStore(config)
            result_id = 'a' * 32
            store.put(result_id, Path('local.mp4'))
            sdk.assert_called_once_with('tos-test-ak', 'tos-test-sk', config.endpoint, config.region,
                                        security_token=None, connection_time=10, socket_timeout=120, max_retry_count=2)
            sdk.return_value.put_object_from_file.assert_called_once_with(
                config.bucket, 'local-real/videos/'+result_id+'.mp4', 'local.mp4',
                content_type='video/mp4', acl=tos.ACLType.ACL_Private)
            self.assertEqual(store.sign(result_id), 'https://signed.example/video')
            self.assertEqual(sdk.return_value.pre_signed_url.call_args.kwargs['expires'], 3600)

    def test_different_account_signing_uses_only_storage_identity(self):
        # Real SDK signing is local computation: no cloud request is made.
        with patch.dict(os.environ, {
            'SEEDANCE_API_KEY': 'video-account-api-key',
            'SEEDANCE_ASSET_ACCESS_KEY_ID': 'video-account-ak',
            'SEEDANCE_ASSET_ACCESS_KEY_SECRET': 'video-account-sk',
        }):
            store = ResultStore(StorageConfig.from_env())
            signed = store.sign('e' * 32)
            credential_scope = parse_qs(urlparse(signed).query)['X-Tos-Credential'][0]
            self.assertTrue(credential_scope.startswith('tos-test-ak/'))
            self.assertNotIn('video-account', signed)

    def test_upload_auth_and_signed_link_refresh(self):
        config = StorageConfig.from_env()
        store = Mock(config=config)
        result_id = 'b' * 32
        key = 'local-real/videos/'+result_id+'.mp4'
        store.put.return_value = key
        store.object_key.return_value = key
        store.sign.side_effect = ['https://signed.example/first', 'https://signed.example/refreshed']
        with tempfile.TemporaryDirectory() as temp:
            client = create_app(store, temp).test_client()
            url = '/internal/results/'+result_id
            self.assertEqual(client.post(url).status_code, 401)
            self.assertEqual(client.post(url, headers=local_headers('local-phase2-storage')).status_code, 401)
            response = client.post(url, headers=local_headers('local-video-storage'),
                                   data={'file': (io.BytesIO(b'\x00\x00\x00\x18ftypisom' + b'0'*20), 'final.mp4')})
            self.assertEqual(response.status_code, 201)
            # Records survive recreation and URLs are signed on each access.
            client = create_app(store, temp).test_client()
            headers = local_headers('local-phase2-storage')
            self.assertEqual(client.get(url+'/url', headers=headers).json['url'], 'https://signed.example/first')
            self.assertEqual(client.get(url+'/url', headers=headers).json['url'], 'https://signed.example/refreshed')
            self.assertEqual(client.get('/internal/results/'+'c'*32+'/url', headers=headers).status_code, 404)

    def test_refund_cleanup_requires_lifecycle_identity_and_deletes_record(self):
        config = StorageConfig.from_env()
        store = Mock(config=config)
        result_id = 'f' * 32
        key = 'local-real/videos/' + result_id + '.mp4'
        store.put.return_value = key
        store.object_key.return_value = key
        with tempfile.TemporaryDirectory() as temp:
            client = create_app(store, temp).test_client()
            url = '/internal/results/' + result_id
            uploaded = client.post(url, headers=local_headers('local-video-storage'),
                                   data={'file': (io.BytesIO(b'\x00\x00\x00\x18ftypisom' + b'0'*20), 'final.mp4')})
            self.assertEqual(uploaded.status_code, 201)
            self.assertEqual(client.delete(url, headers=local_headers('local-video-storage')).status_code, 401)
            self.assertEqual(client.delete(url, headers=local_headers('local-profile-lifecycle')).status_code, 204)
            store.delete.assert_called_once_with(result_id)
            self.assertEqual(client.get(url + '/url', headers=local_headers('local-phase2-storage')).status_code, 404)

    def test_offline_gate_prevents_storage_writes(self):
        store = Mock()
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'LOCAL_CLOUD_ENABLED': 'false'}):
            client = create_app(store, temp).test_client()
            response = client.post('/internal/results/'+'d'*32, headers=local_headers('local-video-storage'))
            self.assertEqual(response.status_code, 503)
            store.put.assert_not_called()

    def test_portrait_is_separate_private_asset_and_rejects_non_images(self):
        store = Mock(config=StorageConfig.from_env())
        result_id = 'c' * 32
        key = 'local-real/portraits/' + result_id + '.jpg'
        store.put_portrait.return_value = key
        store.portrait_key.return_value = key
        store.sign_portrait.return_value = 'https://signed.example/portrait'
        photo = io.BytesIO()
        Image.new('RGB', (90, 160)).save(photo, 'PNG')
        with tempfile.TemporaryDirectory() as temp:
            client = create_app(store, temp).test_client()
            path = '/internal/portraits/' + result_id
            self.assertEqual(client.post(path, headers=local_headers('local-video-storage')).status_code, 401)
            response = client.post(path, headers=local_headers('local-phase2-storage'), data={'file': (io.BytesIO(b'not-image'), 'test')})
            self.assertEqual(response.status_code, 422)
            response = client.post(path, headers=local_headers('local-phase2-storage'), data={'file': (io.BytesIO(photo.getvalue()), 'test.png')})
            self.assertEqual(response.status_code, 201)
            store.put_portrait.assert_called_once()
            store.put.assert_not_called()
            client = create_app(store, temp).test_client()
            self.assertEqual(client.get(path+'/url', headers=local_headers('local-phase2-storage')).json['url'], 'https://signed.example/portrait')

    def test_portrait_sdk_key_and_content_type(self):
        with patch('local.tos_storage.tos.TosClientV2') as sdk:
            store = ResultStore(StorageConfig.from_env())
            key = store.put_portrait('e'*32, 'image.jpg')
            self.assertEqual(key, 'local-real/portraits/'+'e'*32+'.jpg')
            self.assertEqual(sdk.return_value.put_object_from_file.call_args.kwargs['content_type'], 'image/jpeg')

    def test_generic_asset_upload_sign_and_delete(self):
        config = StorageConfig.from_env()
        store = Mock(config=config)
        asset_id = 'asset_' + 'a' * 32
        key = 'local-real/assets/' + asset_id + '.webp'
        store.put_asset.return_value = key
        store.sign_key.return_value = 'https://signed.example/asset'
        image = io.BytesIO()
        Image.new('RGB', (90, 160), 'white').save(image, 'WEBP')
        with tempfile.TemporaryDirectory() as temp:
            client = create_app(store, temp).test_client()
            upload = '/internal/assets/' + asset_id
            self.assertEqual(client.post(
                upload, headers=local_headers('local-video-storage'),
                data={'file': (io.BytesIO(image.getvalue()), 'asset.webp')}).status_code, 401)
            response = client.post(
                upload, headers=local_headers('local-phase2-storage'),
                data={'file': (io.BytesIO(image.getvalue()), 'asset.webp')})
            self.assertEqual(response.status_code, 201)
            store.put_asset.assert_called_once()
            self.assertEqual(store.put_asset.call_args.args[:2], (asset_id, '.webp'))
            signed = client.get(
                upload + '/url', headers=local_headers('phase2-file'))
            self.assertEqual(signed.status_code, 200)
            self.assertEqual(signed.json['url'], 'https://signed.example/asset')
            client = create_app(store, temp).test_client()
            self.assertEqual(client.delete(
                upload, headers=local_headers('local-profile-lifecycle')).status_code, 204)
            store.delete_key.assert_called_once_with(key)


if __name__ == '__main__':
    unittest.main(verbosity=2)
