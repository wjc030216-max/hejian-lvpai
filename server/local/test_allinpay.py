"""W06S contract and project lifecycle tests. All gateway calls are stubbed."""
import os
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from phase2.allinpay import Allinpay, AllinpayError, parse_payinfo, sign_params
from phase2.store import Store


class AllinpayIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, dict(
            APP_ENV='test', PHASE2_AUTH_MODE='local', PHASE2_STATE_BACKEND='sqlite',
            PHASE2_DB_HOST='', PHASE2_STATE_DB=str(Path(self.temp.name)/'initial.sqlite3'),
            PHASE2_PAYMENT_MODE='allinpay', PHASE2_AI_ORCHESTRATION_ENABLED='false',
            PHASE2_PROJECTS_PER_HOUR='1000', PHASE2_UPLOADS_PER_10_MINUTES='1000',
            ALLINPAY_API_URL='https://payments.example/unifiedPay', ALLINPAY_MERCHANT_NO='test-merchant',
            ALLINPAY_SECRET_KEY='test-payment-secret', ALLINPAY_CASHIER_APPID='wxef277996acc166c3',
            ALLINPAY_NOTIFY_URL='https://api.example/api/v2/payments/allinpay/notify',
            AITRAVEL_AIVIDEO_PRICE_FEN='1990', AITRAVEL_JWT_SECRET='test-jwt-secret-at-least-32-characters'))
        env.start(); self.addCleanup(env.stop)
        from phase2 import phase2_app as api
        self.api = api
        self.store = self.make_store()
        for name, value in [('store', self.store), ('LOCAL_REAL', False)]:
            patched = patch.object(api, name, value); patched.start(); self.addCleanup(patched.stop)
        patched = patch.object(api.orchestrator, 'enabled', False); patched.start(); self.addCleanup(patched.stop)
        patched = patch.object(api.orchestrator, 'store', self.store); patched.start(); self.addCleanup(patched.stop)
        self.client = api.app.test_client()
        # A validated draft, as produced after photo checks, without paying an upstream provider.
        template = dict(video_name='test', location_association='test', actor_count=1,
                        character_settings=[])
        self.pid = 'p2_' + uuid.uuid4().hex
        self.store.put(dict(project_id=self.pid, wx_openid='local-user', status='draft', stage='portrait',
                            template=template, template_code='test', runtime_code='P2S-'+self.pid,
                            roles=[dict(character_id='a', person_uid='1', check_status='passed')], attempt=1))
        with patch.object(api, 'build_runtime_template', return_value={}), patch.object(api, 'validate_selection'):
            response = self.client.post('/api/v2/projects/'+self.pid+'/order', json={
                'agreement': True, 'agreement_version': 'travel-photo-v1', 'amount_fen': 1})
        self.assertEqual(response.status_code, 201, response.data)
        self.order = self.store.get(self.pid)['order_no']
        self.cashier = dict(cusid='c', appid='a', trxamt='1990', unireqsn=self.order, sign='UNCHANGED')

    def make_store(self):
        return Store(Path(self.temp.name)/'projects.sqlite3')

    def notice(self, **changes):
        data = dict(merchantOrderNo=self.order, merchantNo='test-merchant', trxamt='1990',
                    trxstatus='2', chnltrxid='transaction-test')
        data.update(changes)
        data['sign'] = sign_params(data, 'test-payment-secret')
        return data

    def notify(self, data=None):
        return self.client.post('/api/v2/payments/allinpay/notify', data=data or self.notice())

    def gateway(self):
        response = Mock(status_code=200)
        response.json.return_value = dict(status='0000', orderNo='trade-test', payinfo=self.cashier)
        return response

    def test_server_price_and_no_generation_before_notification(self):
        p = self.store.get(self.pid)
        self.assertEqual((p['amount_fen'], p['status'], p['payment_mode']), (1990, 'pending_payment', 'allinpay'))
        with patch.object(self.api.orchestrator, '_submit') as submit:
            self.api.orchestrator.tick(self.pid)
            submit.assert_not_called()

    def test_prepay_contract_cache_and_private_parameters(self):
        with patch('phase2.allinpay.requests.get', return_value=self.gateway()) as get:
            first = self.client.post('/api/v2/projects/'+self.pid+'/prepay')
            second = self.client.post('/api/v2/projects/'+self.pid+'/prepay')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.json, second.json)
        self.assertEqual(first.json['data']['extra_data'], self.cashier)
        get.assert_called_once()
        params = get.call_args.kwargs['params']
        self.assertEqual((params['amount'], params['payType'], params['merchantOrderNo']), ('1990', 'W06S', self.order))
        self.assertEqual(params['sign'], sign_params(params, 'test-payment-secret'))
        self.assertNotIn('allinpay_checkout', self.client.get('/api/v2/projects/'+self.pid).json['data'])

    def test_notification_without_user_login_and_late_duplicates(self):
        with patch.dict(os.environ, {'PHASE2_AUTH_MODE': 'wechat'}):
            result = self.notify()
        self.assertEqual((result.status_code, result.text), (200, 'success'))
        paid = self.store.get(self.pid)
        self.assertEqual((paid['status'], paid['payment_status'], paid['amount_paid']), ('portrait_submit', 'paid', 1990))
        self.store.complete(self.pid, 'saved-video')
        self.assertEqual(self.notify().status_code, 200)
        self.assertEqual(self.notify(self.notice(trxstatus='FAILED')).status_code, 200)
        self.assertEqual(self.store.get(self.pid)['status'], 'completed')
        self.assertEqual(self.store.get(self.pid)['paid_at'], paid['paid_at'])

    def test_bad_signature_amount_merchant_and_unknown_order(self):
        cases = [dict(self.notice(), sign='0'*32), self.notice(trxamt='1'),
                 self.notice(trxamt='1990.0'), self.notice(merchantNo='other'),
                 self.notice(merchantOrderNo='unknown')]
        for data in cases:
            with self.subTest(data=data):
                self.assertEqual(self.notify(data).status_code, 400)
                self.assertEqual(self.store.get(self.pid)['status'], 'pending_payment')

    def test_form_get_and_json_notifications(self):
        self.assertEqual(self.client.get('/api/v2/payments/allinpay/notify', query_string=self.notice()).text, 'success')
        self.assertEqual(self.client.post('/api/v2/payments/allinpay/notify', json=self.notice()).text, 'success')
        self.assertEqual(self.client.post('/api/v2/payments/allinpay/notify', json=['invalid']).status_code, 400)

    def test_callback_during_prepay_cannot_revert_paid_state(self):
        def gateway(*args, **kwargs):
            with self.api.app.test_client() as client:
                self.assertEqual(client.post('/api/v2/payments/allinpay/notify', data=self.notice()).status_code, 200)
            return self.gateway()
        with patch('phase2.allinpay.requests.get', side_effect=gateway):
            result = self.client.post('/api/v2/projects/'+self.pid+'/prepay')
        self.assertEqual(result.json['data']['payment_status'], 'paid')
        self.assertEqual(self.store.get(self.pid)['status'], 'portrait_submit')

    def test_concurrent_notifications_and_projection(self):
        def notify(_):
            with self.api.app.test_client() as client:
                return client.post('/api/v2/payments/allinpay/notify', data=self.notice()).status_code
        with ThreadPoolExecutor(4) as pool:
            self.assertEqual(list(pool.map(notify, range(8))), [200]*8)
        self.assertEqual(self.store.get(self.pid)['amount_paid'], 1990)
        if hasattr(self.store, 'engine'):
            with self.store.connection() as db:
                row = db.execute('SELECT amount,paid_at FROM phase2_orders WHERE order_no=?', (self.order,)).fetchone()
            self.assertEqual(row['amount'], 1990)
            self.assertGreater(row['paid_at'], 0)

    def test_transport_failure_does_not_leak_signed_url_or_start_generation(self):
        import requests
        with patch('phase2.allinpay.requests.get', side_effect=requests.Timeout('secret-signed-url')):
            result = self.client.post('/api/v2/projects/'+self.pid+'/prepay')
        self.assertEqual(result.status_code, 502)
        self.assertNotIn('secret-signed-url', result.text)
        self.assertEqual(self.store.get(self.pid)['status'], 'pending_payment')

    def test_payinfo_encodings(self):
        import json
        from urllib.parse import quote, urlencode
        for value in (self.cashier, json.dumps(self.cashier), quote(json.dumps(self.cashier)), urlencode(self.cashier)):
            self.assertEqual(parse_payinfo(value), self.cashier)
        with self.assertRaises(AllinpayError):
            parse_payinfo('bad response')

    def test_tiered_price_does_not_require_flat_override_for_prepay(self):
        with patch.dict(os.environ, {
            'AITRAVEL_AIVIDEO_PRICE_FEN': '',
            'ALLINPAY_MERCHANT_NO': 'test-merchant',
            'ALLINPAY_SECRET_KEY': 'test-payment-secret',
            'ALLINPAY_API_URL': 'https://payments.example/unifiedPay',
            'ALLINPAY_NOTIFY_URL': 'https://api.example/api/v2/payments/allinpay/notify',
            'ALLINPAY_CASHIER_APPID': 'wxef277996acc166c3',
        }):
            Allinpay().validate()
            with patch('phase2.allinpay.requests.get', return_value=self.gateway()) as get:
                response = self.client.post('/api/v2/projects/'+self.pid+'/prepay')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(get.call_args.kwargs['params']['amount'], '1990')


@unittest.skipUnless(os.getenv('MYSQL_TEST_URL'), 'requires disposable MySQL test schema')
class AllinpayMySQLIntegration(AllinpayIntegration):
    def make_store(self):
        from phase2.mysql_store import MySQLStore
        url = os.environ['MYSQL_TEST_URL']
        if '/aitravel_hardening_test' not in url:
            raise RuntimeError('Refusing non-test schema')
        store = MySQLStore(url)
        self.addCleanup(store.engine.dispose)
        return store
