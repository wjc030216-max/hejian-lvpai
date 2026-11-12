"""Generate separate test-node env files. Never reads existing cloud credentials."""
import argparse
import os
from pathlib import Path
import re
import secrets
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'server'))
from common.admin_password import password_hash


def generate(domain, appid, output, main_ip='10.0.0.12', ai_ip='10.0.0.13', data_ip='10.0.0.11'):
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?',domain) or '.' not in domain:
        raise ValueError('Use only a test hostname, without scheme/path')
    if re.search(r'(?i)guizhilv|juxiaobai',domain):
        raise ValueError('Use the new Hejian domain, not a legacy Guizhilv domain')
    if not re.fullmatch(r'wx[a-zA-Z0-9]{16}',appid): raise ValueError('Use a real WeChat AppID')
    if appid=='wxaa62b76ca42a9db5': raise ValueError('Use the new Hejian mini program AppID')
    output=Path(output)
    if any((output/('.env.'+node)).exists() for node in ('main','ai','data')):
        raise ValueError('Existing configuration is preserved. Choose an empty output directory.')
    password=secrets.token_hex(24); jwt_secret=secrets.token_hex(32); token=secrets.token_hex(32); redis=secrets.token_hex(24)
    common={'APP_ENV':'staging','MAIN_PRIVATE_IP':main_ip,
            'AI_PRIVATE_IP':ai_ip,'DATA_PRIVATE_IP':data_ip,
            'AITRAVEL_JWT_SECRET':jwt_secret,'LOCAL_CLOUD_ENABLED':'false'}
    database={'AITRAVEL_DATABASE_URL':'mysql+pymysql://phase2_test:'+password+'@'+data_ip+':13306/aitravel_phase2_test?charset=utf8mb4'}
    media={'PUBLIC_API_BASE_URL':'https://'+domain,'PUBLIC_BASE_URL':'https://'+domain+'/media/'+token}
    admin_password=secrets.token_urlsafe(18)
    nodes={
        'main':dict(common,**database,**media,PHASE2_DB_NAME='aitravel_phase2_test',PHASE2_DB_USER='phase2_test',
            PHASE2_DB_PASSWORD=password,WECHAT_APP_ID=appid,WECHAT_APP_SECRET='',TEST_DOMAIN=domain,
            WECHAT_SUBSCRIBE_TEMPLATE_ID='',
            REDIS_URL='redis://:'+redis+'@'+data_ip+':16379/2',LOCAL_MEDIA_TOKEN=token,
            PHASE2_PAYMENT_MODE='dev-bypass',WECHAT_PAY_APPID='',WECHAT_PAY_MCHID='',WECHAT_PAY_APIV3_KEY='',
            WECHAT_PAY_CERT_SERIAL_NO='',WECHAT_PAY_NOTIFY_URL='https://'+domain+'/api/v2/payments/notify',
            AITRAVEL_AIVIDEO_PRICE_FEN='10',
            ALLINPAY_API_URL='https://interface.allinpaygx.com/api/access/payInterface/unifiedPay',
            ALLINPAY_REFUND_API_URL='https://interface.allinpaygx.com/api/access/payInterface/refundsuhaimei',
            ALLINPAY_REFUND_VERSION='v1.0',
            ALLINPAY_MERCHANT_NO='',ALLINPAY_SECRET_KEY='',ALLINPAY_CASHIER_APPID='wxef277996acc166c3',
            ALLINPAY_NOTIFY_URL='https://'+domain+'/api/v2/payments/allinpay/notify'),
        'ai':dict(common,**database,**media,ALIBABA_CLOUD_ACCESS_KEY_ID='',ALIBABA_CLOUD_ACCESS_KEY_SECRET='',ALIBABA_CLOUD_SECURITY_TOKEN='',
            ALIYUN_FACE_ENDPOINT='facebody.cn-shanghai.aliyuncs.com',ALIYUN_FACE_REGION='cn-shanghai',SEEDREAM_API_KEY='',
            SEEDREAM_MODEL='doubao-seedream-5-0-pro-260628',SEEDANCE_API_KEY='',SEEDANCE_ASSET_ACCESS_KEY_ID='',
            SEEDANCE_ASSET_ACCESS_KEY_SECRET='',SEEDANCE_ASSET_GROUP_ID='',SEEDANCE_ASSET_PROJECT_NAME='default'),
        'data':dict(common,**media,ADMIN_USERNAME='admin',ADMIN_PASSWORD_HASH=password_hash(admin_password),ADMIN_JWT_SECRET=secrets.token_hex(32),PHASE2_DB_NAME='aitravel_phase2_test',PHASE2_DB_USER='phase2_test',PHASE2_DB_PASSWORD=password,
            MYSQL_ROOT_PASSWORD=secrets.token_hex(32),REDIS_PASSWORD=redis,TOS_ACCESS_KEY_ID='',TOS_ACCESS_KEY_SECRET='',TOS_SECURITY_TOKEN='',
            TOS_BUCKET='',TOS_REGION='cn-beijing',TOS_ENDPOINT='https://tos-cn-beijing.volces.com',TOS_KEY_PREFIX='phase2-test/')}
    output.mkdir(parents=True,exist_ok=True)
    for node,values in nodes.items():
        path=output/('.env.'+node)
        with path.open('w',encoding='utf-8',newline='\n') as f:
            f.write('# Dedicated phase-two test node: '+node+'\n')
            f.write('\n'.join(k+'='+v for k,v in values.items())+'\n')
        try: os.chmod(path,0o600)
        except OSError: pass
    login=output/'ADMIN_LOGIN.txt'
    login.write_text('Administrator URL: https://'+domain+'/admin/\nUsername: admin\nPassword: '+admin_password+'\nKeep this file private; never upload it with source code.\n',encoding='utf-8')
    try: os.chmod(login,0o600)
    except OSError: pass
    return nodes

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--domain',required=True); p.add_argument('--appid',required=True)
    p.add_argument('--output',default='deploy-config')
    p.add_argument('--main-ip',required=True); p.add_argument('--ai-ip',required=True); p.add_argument('--data-ip',required=True)
    a=p.parse_args()
    generate(a.domain,a.appid,a.output,a.main_ip,a.ai_ip,a.data_ip)
    print('Created .env.main, .env.ai, .env.data in '+str(Path(a.output).resolve()))
    print('Fill blank cloud/WeChat credentials on their respective nodes; no secrets were printed.')
