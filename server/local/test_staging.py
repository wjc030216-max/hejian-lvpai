"""Deployment contracts: no cloud calls, no production server connection."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from flask import Flask
from common.internal_auth import local_headers
from common import http_client
from common.service_app import protect
from common.staging import validate_staging
from common.storage_client import upload_result

ROOT=Path(__file__).resolve().parents[2]
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'infra/staging'/(name+'.py'))
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

class DeploymentContracts(unittest.TestCase):
    def test_capacity_defaults_match_provider_limits_and_fast_model(self):
        root = ROOT / 'infra' / 'staging'
        main = (root / 'docker-compose.main.yml').read_text(encoding='utf-8')
        ai = (root / 'docker-compose.ai.yml').read_text(encoding='utf-8')
        data = (root / 'docker-compose.data.yml').read_text(encoding='utf-8')
        self.assertIn('SEEDREAM_SERVICE_THREADS:-500', ai)
        self.assertIn('CHARACTER_GENERATION_WORKERS:-500', ai)
        self.assertIn('VIDEO_GENERATION_WORKERS:-40', ai)
        self.assertIn('PHASE2_VIDEO_MAX_INFLIGHT:-40', ai)
        self.assertIn('SEEDANCE_MODEL', ai)
        self.assertIn('doubao-seedance-2-0-fast-260128', ai)
        self.assertIn('--max-connections=512', data)
        self.assertIn('PHASE2_PROJECT_DB_POOL_SIZE:-32', main)

    def test_production_node_validation_accepts_dedicated_schema_and_storage_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            values = load('prepare_configs').generate('test.example.com', 'wx1234567890abcdef', tmp)
            validator = load('start_node')
            for env in values.values():
                env['APP_ENV'] = 'production'
                env['LOCAL_CLOUD_ENABLED'] = 'true'
            values['main'].update(
                WECHAT_APP_SECRET='unit-test-only',
                ALLINPAY_MERCHANT_NO='test-merchant',
                ALLINPAY_SECRET_KEY='test-secret',
                PHASE2_DB_NAME='aitravel_phase2',
                PHASE2_DB_USER='phase2_prod',
            )
            values['main']['AITRAVEL_DATABASE_URL'] = (
                'mysql+pymysql://phase2_prod:' + values['main']['PHASE2_DB_PASSWORD']
                + '@10.0.0.11:13306/aitravel_phase2?charset=utf8mb4')
            values['ai']['AITRAVEL_DATABASE_URL'] = values['main']['AITRAVEL_DATABASE_URL']
            values['ai'].update(
                ALIBABA_CLOUD_ACCESS_KEY_ID='aliyun-ak',
                ALIBABA_CLOUD_ACCESS_KEY_SECRET='aliyun-sk',
                SEEDREAM_API_KEY='seedream-key',
                SEEDANCE_API_KEY='seedance-key',
                SEEDANCE_ASSET_ACCESS_KEY_ID='asset-ak',
                SEEDANCE_ASSET_ACCESS_KEY_SECRET='asset-sk',
                SEEDANCE_ASSET_GROUP_ID='group-test',
            )
            values['data'].update(
                PHASE2_DB_NAME='aitravel_phase2',
                PHASE2_DB_USER='phase2_prod',
                TOS_KEY_PREFIX='phase2-prod/',
                TOS_ACCESS_KEY_ID='tos-ak',
                TOS_ACCESS_KEY_SECRET='tos-sk',
                TOS_BUCKET='production-bucket',
            )
            for node, env in values.items():
                validator.validate(node, env)

    def test_allinpay_configuration_requires_credentials_and_positive_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            values=load('prepare_configs').generate('test.example.com','wx1234567890abcdef',tmp)
            env=values['main']; env.update(WECHAT_APP_SECRET='test-only', PHASE2_PAYMENT_MODE='allinpay')
            validator=load('start_node')
            with self.assertRaises(ValueError): validator.validate('main',env)
            env.update(ALLINPAY_MERCHANT_NO='test-merchant',ALLINPAY_SECRET_KEY='test-secret')
            validator.validate('main',env)
            env['AITRAVEL_AIVIDEO_PRICE_FEN']='0'
            with self.assertRaises(ValueError): validator.validate('main',env)

    def test_generated_files_have_matching_private_connections_and_no_cloud_keys_cross_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            values=load('prepare_configs').generate('test.example.com','wx1234567890abcdef',tmp)
            validator=load('start_node')
            values['main']['WECHAT_APP_SECRET']='unit-test-only'
            values['main']['ALLINPAY_MERCHANT_NO']='test-merchant'
            values['main']['ALLINPAY_SECRET_KEY']='test-secret'
            for node,env in values.items(): validator.validate(node,env)
            self.assertEqual(len({v['AITRAVEL_JWT_SECRET'] for v in values.values()}),1)
            self.assertEqual(values['main']['AITRAVEL_DATABASE_URL'],values['ai']['AITRAVEL_DATABASE_URL'])
            self.assertNotIn('TOS_ACCESS_KEY_ID',values['ai'])
            self.assertNotIn('SEEDANCE_API_KEY',values['data'])
            self.assertNotIn('WECHAT_APP_SECRET',values['ai'])
            with self.assertRaises(ValueError): load('prepare_configs').generate('test.example.com','wx1234567890abcdef',tmp)
            values['ai']['AITRAVEL_DATABASE_URL']='mysql+pymysql://user:password@production.example.com:3306/aitravel'
            with self.assertRaises(ValueError): validator.validate('ai',values['ai'])

    def test_staging_rejects_developer_identity_and_missing_mysql(self):
        valid=dict(APP_ENV='staging',PHASE2_AUTH_MODE='wechat',PHASE2_STATE_BACKEND='mysql',PHASE2_DB_NAME='aitravel_phase2_test',
            WECHAT_APP_ID='wx1234567890abcdef',WECHAT_APP_SECRET='test-only',AITRAVEL_JWT_SECRET='test-only-'*5,
            PUBLIC_API_BASE_URL='https://test.example.com',PUBLIC_BASE_URL='https://test.example.com/media/'+'a'*64)
        with patch.dict(os.environ,valid):
            validate_staging()
            for change in (dict(PHASE2_AUTH_MODE='local'),dict(PHASE2_STATE_BACKEND='sqlite'),dict(PHASE2_DB_NAME='aitravel'),dict(PUBLIC_API_BASE_URL='http://test.example.com')):
                with patch.dict(os.environ,change),self.assertRaises(RuntimeError): validate_staging()

    def test_internal_routes_require_expected_service_not_user_jwt(self):
        import jwt,time
        app=Flask(__name__); protect(app,{'local-phase2'},public={'/health'})
        app.add_url_rule('/health','health',lambda:'ok'); app.add_url_rule('/generate','generate',lambda:'accepted',methods=['POST'])
        with patch.dict(os.environ,{'AITRAVEL_JWT_SECRET':'unit-test-secret-'*4}):
            c=app.test_client()
            self.assertEqual(c.get('/health').status_code,200)
            self.assertEqual(c.post('/generate').status_code,401)
            self.assertEqual(c.post('/generate',headers=local_headers('wrong-service')).status_code,401)
            self.assertEqual(c.post('/generate',headers=local_headers('local-phase2')).status_code,200)
            token=jwt.encode(dict(sub='local-phase2',aud='phase2-user',exp=int(time.time())+60),os.environ['AITRAVEL_JWT_SECRET'],algorithm='HS256')
            self.assertEqual(c.post('/generate',headers={'Authorization':'Bearer '+token}).status_code,401)

    def test_server_result_url_uses_private_storage_for_staging_and_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'result.mp4'; p.write_bytes(b'media')
            for environment in ('staging','production'):
                p.write_bytes(b'media')
                with patch.dict(os.environ,dict(APP_ENV=environment,TOS_SERVICE_URL='http://10.0.0.11:18012',
                        PUBLIC_API_BASE_URL='https://test.example.com',AITRAVEL_JWT_SECRET='unit-test-secret-'*4)),patch.object(http_client.session(),'post',return_value=Mock(status_code=201)) as post:
                    url=upload_result('a'*32,p)
                    self.assertEqual(url,'https://test.example.com/api/v2/results/'+'a'*32)
                    self.assertTrue(post.call_args.args[0].startswith('http://10.0.0.11:18012/'))
                    self.assertFalse(p.exists())

    def test_cloud_disabled_blocks_staging(self):
        from common.local_guard import require_cloud_enabled,validate_media_url
        with patch.dict(os.environ,dict(APP_ENV='staging',LOCAL_CLOUD_ENABLED='false',EXTERNAL_DOMAIN='https://test.example.com/media/'+'a'*64)):
            with self.assertRaises(RuntimeError): require_cloud_enabled()
            with self.assertRaises(ValueError): validate_media_url('https://production.example.com/photo.jpg')
            validate_media_url('https://test.example.com/media/'+'a'*64+'/assets/image.jpg')

if __name__=='__main__': unittest.main()
