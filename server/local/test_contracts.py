"""Provider-boundary tests. All billable HTTP calls are replaced; no cloud charges."""
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import jwt
from phase2.orchestrator import Phase2Orchestrator
from phase2 import runtime_templates as templates
from common import http_client
from common.aliyun_face_provider import detect_face, FaceProviderError, _image_bytes
from common.local_guard import require_cloud_enabled, validate_media_url
from local.results import save_local_video
from template_fixture import create_template_folder
from phase2.runtime_templates import normalize_template

FACE_SDK_AVAILABLE = importlib.util.find_spec('alibabacloud_facebody20191230') is not None


class Contracts(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'APP_ENV': 'local-real', 'LOCAL_CLOUD_ENABLED': 'true', 'LOCAL_RESULT_STORAGE': 'local',
            'AITRAVEL_JWT_SECRET': 'test-secret-' * 8,
            'EXTERNAL_DOMAIN': 'https://local.example/media/test',
            'PUBLIC_API_BASE_URL': 'https://api.local.example',
            'ALIBABA_CLOUD_ACCESS_KEY_ID': 'unit-test',
            'ALIBABA_CLOUD_ACCESS_KEY_SECRET': 'unit-test',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.storage = patch('common.storage_client.save_portrait', side_effect=lambda project, task, source: (task, 'http://127.0.0.1:8080/api/v2/portraits/' + task))
        self.storage.start()
        self.addCleanup(self.storage.stop)

    def test_short_lived_signed_auth(self):
        worker = Phase2Orchestrator(Mock())
        token = worker._headers()['Authorization'].split()[1]
        payload = jwt.decode(token, os.environ['AITRAVEL_JWT_SECRET'], algorithms=['HS256'])
        self.assertEqual(payload['sub'], 'local-phase2')
        self.assertEqual(payload['exp'] - payload['iat'], 300)

    def test_cloud_gate_and_media_origin(self):
        with patch.dict(os.environ, {'LOCAL_CLOUD_ENABLED': 'false'}):
            with self.assertRaises(RuntimeError):
                require_cloud_enabled()
        for url in ['https://api.guizhilv.com.cn/a.png', 'http://file:10004/a.png',
                    'https://local.example/media/test.evil/a.png']:
            with self.assertRaises(ValueError):
                validate_media_url(url)
        validate_media_url('https://local.example/media/test/api/v2/assets/asset_1')
        validate_media_url(
            'https://api.local.example/api/v2/assets/asset_1?access=signed-token')

    def test_real_template_roles_and_missing_costume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = create_template_folder(root, "测试模板", actor_count=3)
            # The explicit metadata is the source of truth for a folder import.
            metadata = json.loads((folder / "1-基础元数据.txt").read_text(encoding="utf-8"))
            item = normalize_template({
                "template_code": "TEST-THREE",
                "video_name": "测试模板",
                "actor_count": 3,
                "character_settings": metadata["module_1"]["character_settings"],
                "location_association": "漓江",
            })
            runtime = templates.runtime_from_folder(item, folder)
            self.assertEqual(len(runtime["costume_url"]), 3)
            self.assertTrue(runtime["portrait_prompt"])
            self.assertTrue(runtime["config_json"]["groups"][0]["Prompt"])
            missing = folder / "9.2-角色服装图-女主角A.png"
            missing.unlink()
            with self.assertRaises(ValueError):
                templates.runtime_from_folder(item, folder)

    @unittest.skipUnless(FACE_SDK_AVAILABLE, 'requires Alibaba FaceBody SDK')
    def test_aliyun_official_flat_rectangles(self):
        client = Mock()
        client.detect_face_advance.return_value.to_map.return_value = {
            'body': {'Data': {'FaceCount': 1, 'FaceRectangles': [10, 20, 100, 120]}}}
        client.pedestrian_detect_attribute_advance.return_value.to_map.return_value = {
            'body': {'Data': {'Boxes': [], 'Attributes': []}}}
        with patch('alibabacloud_facebody20191230.client.Client', return_value=client), patch('common.aliyun_face_provider._image_bytes', return_value=b'photo'):
            face = detect_face('https://local.example/media/test/face.png')
        self.assertEqual(face['location'], {'x': 10, 'y': 20, 'width': 100, 'height': 120})

    @unittest.skipUnless(FACE_SDK_AVAILABLE, 'requires Alibaba FaceBody SDK')
    def test_body_detection_is_not_face_detection(self):
        client = Mock()
        client.detect_face_advance.return_value.to_map.return_value = {'body': {'Data': {'FaceCount': 0}}}
        client.pedestrian_detect_attribute_advance.return_value.to_map.return_value = {
            'body': {'Data': {'Boxes': [{'TopLeftX': 0, 'TopLeftY': 0, 'BottomRightX': 100, 'BottomRightY': 200}]}}}
        with patch('alibabacloud_facebody20191230.client.Client', return_value=client), patch('common.aliyun_face_provider._image_bytes', return_value=b'photo'):
            face = detect_face('https://local.example/media/test/face.png')
        self.assertIsNone(face['location']['width'])
        client.pedestrian_detect_attribute_advance.assert_not_called()

    @unittest.skipUnless(FACE_SDK_AVAILABLE, 'requires Alibaba FaceBody SDK')
    def test_aliyun_uses_fresh_file_streams_for_non_oss_images(self):
        client = Mock()
        seen = []
        def detect(request, runtime):
            seen.append(request.image_urlobject.read())
            return Mock(to_map=lambda: {'body': {'Data': {'FaceRectangles': [1, 2, 30, 40]}}})
        def attributes(request, runtime):
            seen.append(request.image_urlobject.read())
            return Mock(to_map=lambda: {'body': {'Data': {'Attributes': []}}})
        client.detect_face_advance.side_effect = detect
        client.pedestrian_detect_attribute_advance.side_effect = attributes
        with patch('alibabacloud_facebody20191230.client.Client', return_value=client), patch('common.aliyun_face_provider._image_bytes', return_value=b'photo-bytes'):
            detect_face('https://local.example/media/test/photo.png')
        self.assertEqual(seen, [b'photo-bytes', b'photo-bytes'])
        client.detect_face_with_options.assert_not_called()

    @unittest.skipUnless(FACE_SDK_AVAILABLE, 'requires Alibaba FaceBody SDK')
    def test_aliyun_sdk_errors_are_safe_and_actionable(self):
        client = Mock()
        error = Exception('private image URL and credentials must not be exposed')
        error.code = 'InvalidImage.URL'
        client.detect_face_advance.side_effect = error
        with patch('alibabacloud_facebody20191230.client.Client', return_value=client), patch('common.aliyun_face_provider._image_bytes', return_value=b'photo'):
            with self.assertRaises(FaceProviderError) as raised:
                detect_face('https://local.example/media/test/photo.png')
        self.assertIn('InvalidImage.URL', str(raised.exception))
        self.assertNotIn('private', str(raised.exception))
        client.pedestrian_detect_attribute_advance.assert_not_called()

    def test_photo_download_rejects_redirects_and_empty_content(self):
        for status, chunks in [(302, []), (200, [])]:
            response = Mock(status_code=status)
            response.iter_content.return_value = chunks
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            with patch('common.aliyun_face_provider.requests.get', return_value=response):
                with self.assertRaises((FaceProviderError, ValueError)):
                    _image_bytes('https://local.example/media/test/photo.png')

    def test_face_error_reaches_project_without_internal_url(self):
        worker = Phase2Orchestrator(Mock())
        response = Mock(status_code=502)
        response.json.return_value = {'error': '阿里云人脸服务鉴权失败'}
        with patch.object(http_client.session(), 'post', return_value=response):
            with self.assertRaisesRegex(RuntimeError, '阿里云人脸服务鉴权失败'):
                worker._check_face('test-user', 'https://local.example/media/test/photo.png')

    @unittest.skipUnless(FACE_SDK_AVAILABLE, 'requires Alibaba FaceBody SDK')
    def test_multiple_faces_and_missing_quality_are_not_invented(self):
        client = Mock()
        client.detect_face_advance.return_value.to_map.return_value = {
            'body': {'Data': {'FaceCount': 2, 'FaceRectangles': [1,2,30,40,5,6,30,40],
                              'Qualities': {'ScoreList': [85,90]}}}}
        client.pedestrian_detect_attribute_advance.return_value.to_map.return_value = {'body': {'Data': {}}}
        with patch('alibabacloud_facebody20191230.client.Client',return_value=client), patch('common.aliyun_face_provider._image_bytes',return_value=b'photo'):
            face=detect_face('https://local.example/media/test/photo.png')
        self.assertEqual(face['face_count'],2)
        self.assertTrue(face['quality_passed'])
        worker=Phase2Orchestrator(Mock())
        self.addCleanup(worker.pool.shutdown)
        response=Mock(status_code=200); response.json.return_value=face
        with patch('phase2.orchestrator.requests.post',return_value=response):
            with self.assertRaises(RuntimeError): worker._check_face('run','photo')

    def test_local_video_and_bgm_save_without_cloud_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video, music = root/'source.mp4', root/'music.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=90x160:d=1',
                            '-c:v', 'libx264', str(video)], check=True, timeout=30)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
                            str(music)], check=True, timeout=30)
            def download(url, target):
                shutil.copyfile(music if url.endswith('music') else video, target)
            with patch.dict(os.environ, {'LOCAL_RESULT_ROOT': str(root/'results')}), patch('local.results._download', side_effect=download):
                url = save_local_video('https://provider.example/video', 'https://local.example/media/test/music', 'local-user', 'task1')
            self.assertTrue(url.endswith('/local-user/task1.mp4'))
            output = root/'results/local-user/task1.mp4'
            self.assertGreater(output.stat().st_size, 100)
            probe = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'json', str(output)])
            kinds = {s['codec_type'] for s in json.loads(probe)['streams']}
            self.assertEqual(kinds, {'video', 'audio'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
