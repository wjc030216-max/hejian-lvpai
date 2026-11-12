"""Fail-closed deployment validation for staging and production."""
import os
from urllib.parse import urlsplit

def validate_staging():
    app_env = os.getenv('APP_ENV')
    if app_env not in ('staging', 'production'): return
    if os.getenv('PHASE2_AUTH_MODE') != 'wechat': raise RuntimeError('测试服务器必须使用微信登录')
    if os.getenv('PHASE2_STATE_BACKEND') != 'mysql': raise RuntimeError('测试服务器必须使用 MySQL')
    for key in ('WECHAT_APP_ID','WECHAT_APP_SECRET','AITRAVEL_JWT_SECRET','PUBLIC_API_BASE_URL','PUBLIC_BASE_URL'):
        if not os.getenv(key): raise RuntimeError('缺少测试配置: '+key)
    if len(os.environ['AITRAVEL_JWT_SECRET'])<32: raise RuntimeError('服务密钥至少 32 字符')
    if app_env == 'staging' and os.getenv('PHASE2_DB_NAME') != 'aitravel_phase2_test':
        raise RuntimeError('只能使用二期测试数据库')
    if app_env == 'production':
        if os.getenv('PHASE2_PAYMENT_MODE') != 'allinpay':
            raise RuntimeError('正式环境只允许通联 W06S')
        if os.getenv('PHASE2_DEV_PAYMENT_BYPASS', 'false').lower() == 'true':
            raise RuntimeError('正式环境禁止跳过支付')
        if os.getenv('LOCAL_CLOUD_ENABLED') != 'true':
            raise RuntimeError('正式环境必须启用真实云服务')
        db_name = os.getenv('PHASE2_DB_NAME', '')
        if not db_name or 'test' in db_name.lower():
            raise RuntimeError('正式环境禁止使用测试数据库')
        refund_value = os.getenv('ALLINPAY_REFUND_API_URL', '').strip()
        refund_url = urlsplit(refund_value)
        if not refund_value or refund_url.scheme != 'https' or not refund_url.hostname or refund_url.username:
            raise RuntimeError('正式退款地址必须是 HTTPS')
    api=urlsplit(os.environ['PUBLIC_API_BASE_URL']); media=urlsplit(os.environ['PUBLIC_BASE_URL'])
    if api.scheme!='https' or not api.hostname or api.path or api.query or api.username:
        raise RuntimeError('API 入口必须是 HTTPS 域名根地址')
    if media.scheme!='https' or media.netloc!=api.netloc or not media.path.startswith('/media/'):
        raise RuntimeError('素材入口必须属于本次安装')
