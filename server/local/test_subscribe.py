"""Subscription-message deep-link contract checks."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from phase2 import wechat_subscribe


ROOT = Path(__file__).resolve().parents[2]


class SubscribeMessageTests(unittest.TestCase):
    def test_notification_opens_the_registered_work_page(self):
        app_config = json.loads((ROOT / 'client' / 'app.json').read_text(encoding='utf-8'))
        self.assertIn('pages/phase2/work/work', app_config['pages'])

        response = type('Response', (), {
            'status_code': 200,
            'json': lambda self: {'errcode': 0, 'errmsg': 'ok'},
        })()
        with patch.dict(os.environ, {
            'WECHAT_SUBSCRIBE_MINIPROGRAM_STATE': 'formal',
            'APP_ENV': 'staging',
        }, clear=False), patch.object(wechat_subscribe, '_access_token', return_value='token'), \
                patch.object(wechat_subscribe.service_log, 'event'), \
                patch.object(wechat_subscribe.requests, 'post', return_value=response) as post:
            self.assertTrue(wechat_subscribe.send_completed_notice(
                'openid-1', 'p2_abc123', '漓江旅行电影', 1_700_000_000, 1_700_000_100,
                'authorized-template-id',
            ))

        payload = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(payload['page'], 'pages/phase2/work/work?id=p2_abc123')
        self.assertEqual(payload['miniprogram_state'], 'formal')
        self.assertEqual(payload['template_id'], 'authorized-template-id')

    def test_staging_defaults_to_formal_and_allows_trial_override(self):
        with patch.dict(os.environ, {'APP_ENV': 'staging'}, clear=False):
            os.environ.pop('WECHAT_SUBSCRIBE_MINIPROGRAM_STATE', None)
            self.assertEqual(wechat_subscribe._miniprogram_state(), 'formal')
            os.environ['WECHAT_SUBSCRIBE_MINIPROGRAM_STATE'] = 'trial'
            self.assertEqual(wechat_subscribe._miniprogram_state(), 'trial')


if __name__ == '__main__':
    unittest.main()
