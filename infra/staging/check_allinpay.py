#!/usr/bin/env python3
"""离线检查 allinpay.env 的字段格式；不联网、不打印密钥明文。

用法：python3 infra/staging/check_allinpay.py deploy-config/allinpay.env
"""
import re
import sys
from urllib.parse import urlsplit

HOSTS = {'test': 'interfacetest.allinpaygx.com', 'production': 'interface.allinpaygx.com'}
REQUIRED = ('ALLINPAY_ENV', 'ALLINPAY_MERCHANT_NO', 'ALLINPAY_SECRET_KEY',
            'ALLINPAY_CASHIER_APPID', 'ALLINPAY_NOTIFY_URL')


def read_env(path):
    values = {}
    for line in open(path, encoding='utf-8-sig'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        values[key.strip()] = value.strip()
    return values


def mask(value):
    if not value:
        return '(空)'
    if len(value) <= 8:
        return value[:2] + '*' * (len(value) - 2)
    return value[:4] + '*' * (len(value) - 8) + value[-4:]


def main(path):
    values = read_env(path)
    errors, warnings = [], []

    env = (values.get('ALLINPAY_ENV') or '').lower()
    if env not in HOSTS:
        errors.append('ALLINPAY_ENV 必须是 test 或 production，当前 "%s"' % values.get('ALLINPAY_ENV', ''))
        env = 'production'
    expected_host = HOSTS[env]

    merchant = values.get('ALLINPAY_MERCHANT_NO', '')
    if not merchant:
        errors.append('ALLINPAY_MERCHANT_NO 为空')
    elif not re.fullmatch(r'[0-9A-Za-z]{8,32}', merchant):
        errors.append('ALLINPAY_MERCHANT_NO 必须是 8-32 位字母数字（通联商户号 cusid）')
    elif merchant.isdigit() and len(merchant) in (9, 10):
        warnings.append('ALLINPAY_MERCHANT_NO 是 %d 位纯数字，像微信商户号；通联商户号通常形如 56461107333W9SW' % len(merchant))

    secret = values.get('ALLINPAY_SECRET_KEY', '')
    if not secret:
        errors.append('ALLINPAY_SECRET_KEY 为空')
    elif len(secret) < 16:
        errors.append('ALLINPAY_SECRET_KEY 只有 %d 位，明显偏短' % len(secret))
    elif len(secret) != 32:
        warnings.append('ALLINPAY_SECRET_KEY 是 %d 位（常见 32 位），确认没有多余空格或换行' % len(secret))

    appid = values.get('ALLINPAY_CASHIER_APPID', '')
    if not re.fullmatch(r'wx[0-9a-zA-Z]{16}', appid or ''):
        errors.append('ALLINPAY_CASHIER_APPID 必须是 wx + 16 位字母数字')

    notify = values.get('ALLINPAY_NOTIFY_URL', '')
    parsed = urlsplit(notify)
    if not notify:
        errors.append('ALLINPAY_NOTIFY_URL 为空')
    elif parsed.scheme != 'https' or not parsed.hostname or parsed.query or parsed.fragment:
        errors.append('ALLINPAY_NOTIFY_URL 必须是干净的 HTTPS 地址（不带查询串）')
    elif not parsed.path.endswith('/api/v2/payments/allinpay/notify'):
        warnings.append('ALLINPAY_NOTIFY_URL 路径不是 /api/v2/payments/allinpay/notify，确认网关路由')

    for key in ('ALLINPAY_API_URL', 'ALLINPAY_REFUND_API_URL'):
        value = values.get(key, '')
        if value and urlsplit(value).hostname != expected_host:
            errors.append('%s 的域名与 ALLINPAY_ENV=%s 不匹配（应为 %s）' % (key, env, expected_host))

    version = values.get('ALLINPAY_REFUND_VERSION', '')
    if not re.fullmatch(r'[A-Za-z0-9_.\-]+', version or ''):
        errors.append('ALLINPAY_REFUND_VERSION 建议填 v1.0')

    print('文件：%s' % path)
    print('环境：%s（接口域名 %s）' % (env, expected_host))
    print('-' * 60)
    for key in ('ALLINPAY_MERCHANT_NO', 'ALLINPAY_SECRET_KEY', 'ALLINPAY_CASHIER_APPID',
                'ALLINPAY_NOTIFY_URL', 'ALLINPAY_REFUND_VERSION'):
        value = values.get(key, '')
        print('%-24s %-46s %d 位' % (key, mask(value) or '(空)', len(value)))
    print('-' * 60)
    for item in errors:
        print('错误：' + item)
    for item in warnings:
        print('提醒：' + item)
    if not errors and not warnings:
        print('全部字段格式正确。')
    elif not errors:
        print('格式无错误，只有提醒。')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'deploy-config/allinpay.env'))
