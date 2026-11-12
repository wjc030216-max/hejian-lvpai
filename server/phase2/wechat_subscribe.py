"""微信订阅消息：AI 写真制作完成通知（一次性订阅）。"""
import json
import os
import time
from urllib.parse import urlencode

import requests

from common import service_log

TEMPLATE_ID = os.getenv('WECHAT_SUBSCRIBE_TEMPLATE_ID', '').strip()
PRODUCTION_TEXT = os.getenv('WECHAT_SUBSCRIBE_PRODUCTION_TEXT', '定妆照 + 旅行视频')
STATUS_TEXT = os.getenv('WECHAT_SUBSCRIBE_STATUS_TEXT', '已生成')
TIP_TEXT = os.getenv('WECHAT_SUBSCRIBE_TIP_TEXT', '视频已生成，点击查看')

_token = {'value': None, 'expires': 0}


def _miniprogram_state():
    configured = os.getenv('WECHAT_SUBSCRIBE_MINIPROGRAM_STATE', '').strip().lower()
    if configured in ('developer', 'trial', 'formal'):
        return configured
    environment = os.getenv('APP_ENV', 'development').strip().lower()
    return 'trial' if environment in ('local', 'local-real', 'development', 'test') else 'formal'


def _clip(text, limit):
    value = str(text or '').strip()
    return value[:limit]


def _fmt_time(value):
    if not value:
        return ''
    try:
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(float(value)))
    except (TypeError, ValueError, OSError):
        return ''


def _access_token():
    now = time.time()
    if _token['value'] and _token['expires'] > now + 60:
        return _token['value']
    appid = os.getenv('WECHAT_APP_ID', '').strip()
    secret = os.getenv('WECHAT_APP_SECRET', '').strip()
    if not appid or not secret:
        raise RuntimeError('未配置 WECHAT_APP_ID / WECHAT_APP_SECRET')
    try:
        response = requests.get('https://api.weixin.qq.com/cgi-bin/token',
                                params={'grant_type': 'client_credential', 'appid': appid, 'secret': secret},
                                timeout=(5, 15))
        data = response.json()
    except Exception as exc:
        raise RuntimeError('获取 access_token 网络异常') from exc
    if response.status_code != 200 or not data.get('access_token'):
        raise RuntimeError('获取 access_token 失败: ' + str(data.get('errcode', response.status_code)))
    _token['value'] = data['access_token']
    _token['expires'] = now + int(data.get('expires_in', 7200))
    return _token['value']


def send_completed_notice(openid, project_id, video_name, created_at, completed_at,
                          template_id=None):
    if not openid or not project_id:
        return False
    template_id = str(template_id or TEMPLATE_ID).strip()
    if not template_id:
        service_log.event('system', '订阅消息跳过（未配置模板）',
                          context='project=' + project_id)
        return False
    try:
        token = _access_token()
    except RuntimeError as exc:
        service_log.event('system', '订阅消息跳过（token）', context=str(exc))
        return False
    payload = {
        'touser': openid,
        'template_id': template_id,
        'page': 'pages/phase2/work/work?' + urlencode({'id': project_id}),
        'miniprogram_state': _miniprogram_state(),
        'lang': 'zh_CN',
        'data': {
            'thing1': {'value': _clip(video_name or 'AI旅拍作品', 20)},
            'date2': {'value': _fmt_time(completed_at)},
            'thing5': {'value': _clip(PRODUCTION_TEXT, 20)},
            'phrase11': {'value': _clip(STATUS_TEXT, 5)},
            'thing20': {'value': _clip(TIP_TEXT, 20)},
        },
    }
    try:
        response = requests.post('https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token=' + token,
                                 data=json.dumps(payload, ensure_ascii=False),
                                 headers={'Content-Type': 'application/json'}, timeout=(5, 15))
        result = response.json()
    except Exception as exc:
        service_log.event('system', '订阅消息发送异常', context=str(exc))
        return False
    ok = response.status_code == 200 and result.get('errcode') == 0
    service_log.event('system', '订阅消息发送' + ('成功' if ok else '失败'),
                      context='project=' + project_id + ' errcode=' + str(result.get('errcode')) + ' errmsg=' + str(result.get('errmsg')))
    return ok
