"""Validate one node and start its isolated Compose project; secrets are never logged."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[2]
NODE_NAMES=('main','ai','data')
DEFAULT_NODE_IP={'main':'','ai':'','data':''}
NODE_IP=dict(DEFAULT_NODE_IP)

def read_env(path):
    values={}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        key,sep,value=line.partition('=')
        if not sep or not re.fullmatch('[A-Z0-9_]+',key) or key in values:
            raise ValueError('Invalid or duplicate setting name')
        if any(c in value for c in '\x00\r\n\'"`$'):
            raise ValueError('Unsupported quoting/shell character in '+key)
        values[key]=value.strip()
    return values

def validate(node,values):
    global NODE_IP
    NODE_IP={
        'main':values.get('MAIN_PRIVATE_IP',DEFAULT_NODE_IP['main']),
        'ai':values.get('AI_PRIVATE_IP',DEFAULT_NODE_IP['ai']),
        'data':values.get('DATA_PRIVATE_IP',DEFAULT_NODE_IP['data']),
    }
    legacy_ips={'172.31.24.158','172.31.24.159','172.31.24.157'}
    if legacy_ips & set(NODE_IP.values()):
        raise ValueError('Refusing legacy Guizhilv private IP addresses')
    app_env=values.get('APP_ENV','staging').strip().lower()
    if app_env not in ('staging','production'):
        raise ValueError('APP_ENV must be staging or production')
    common=['AITRAVEL_JWT_SECRET','LOCAL_CLOUD_ENABLED']
    common += ['MAIN_PRIVATE_IP','AI_PRIVATE_IP','DATA_PRIVATE_IP']
    required={
        'main':['AITRAVEL_DATABASE_URL','PHASE2_DB_NAME','PHASE2_DB_USER','PHASE2_DB_PASSWORD','WECHAT_APP_ID','WECHAT_APP_SECRET',
                'PUBLIC_API_BASE_URL','PUBLIC_BASE_URL','TEST_DOMAIN','LOCAL_MEDIA_TOKEN','REDIS_URL'],
        'ai':['AITRAVEL_DATABASE_URL','PUBLIC_API_BASE_URL','PUBLIC_BASE_URL'],
        'data':['ADMIN_USERNAME','ADMIN_PASSWORD_HASH','ADMIN_JWT_SECRET','PUBLIC_BASE_URL','PUBLIC_API_BASE_URL','MYSQL_ROOT_PASSWORD','PHASE2_DB_NAME','PHASE2_DB_USER','PHASE2_DB_PASSWORD','REDIS_PASSWORD']}
    if values.get('LOCAL_CLOUD_ENABLED') not in ('true','false'): raise ValueError('LOCAL_CLOUD_ENABLED must be true or false')
    for key in common+required[node]:
        if not values.get(key) or 'CHANGE_ME' in values[key]: raise ValueError('Fill '+key)
    if len(values['AITRAVEL_JWT_SECRET'])<32: raise ValueError('JWT secret is too short')
    if app_env=='production' and node=='main':
        if values.get('PHASE2_PAYMENT_MODE','').lower() not in ('allinpay','w06s'):
            raise ValueError('Production only allows Allinpay W06S')
        if not values.get('ALLINPAY_REFUND_API_URL'):
            raise ValueError('Fill ALLINPAY_REFUND_API_URL for production')
    if app_env=='production' and values['LOCAL_CLOUD_ENABLED']!='true':
        raise ValueError('Production requires LOCAL_CLOUD_ENABLED=true')
    if node=='main':
        pay_env=(values.get('ALLINPAY_ENV') or 'production').strip().lower()
        if pay_env not in ('test','production'):
            raise ValueError('ALLINPAY_ENV must be test or production')
        expected_host='interfacetest.allinpaygx.com' if pay_env=='test' else 'interface.allinpaygx.com'
        for key in ('ALLINPAY_API_URL','ALLINPAY_REFUND_API_URL'):
            if values.get(key) and urlsplit(values[key]).hostname!=expected_host:
                raise ValueError(key+' must use '+expected_host)
    if node=='main' and values.get('PHASE2_PAYMENT_MODE','allinpay').lower() in ('allinpay','w06s'):
        for key in ('ALLINPAY_MERCHANT_NO','ALLINPAY_SECRET_KEY',
                    'ALLINPAY_REFUND_VERSION'):
            if not values.get(key) or 'CHANGE_ME' in values[key]: raise ValueError('Fill '+key)
        price=values.get('AITRAVEL_AIVIDEO_PRICE_FEN','')
        if price and (not price.isdigit() or int(price)<=0):
            raise ValueError('Payment override price must be positive integer fen or empty')
        for key in ('ALLINPAY_API_URL','ALLINPAY_REFUND_API_URL','ALLINPAY_NOTIFY_URL'):
            if values.get(key):
                parsed=urlsplit(values[key])
                if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.fragment:
                    raise ValueError(key+' must be HTTPS')
    if node=='data':
        if len(values['ADMIN_JWT_SECRET'])<32 or values['ADMIN_JWT_SECRET']==values['AITRAVEL_JWT_SECRET']: raise ValueError('Use a separate administrator secret')
        if not re.fullmatch('scrypt:[a-f0-9]{32}:[a-f0-9]{128}',values['ADMIN_PASSWORD_HASH']): raise ValueError('Use the generated administrator password hash')
    if node in ('main','data') and app_env=='staging':
        if values['PHASE2_DB_NAME']!='aitravel_phase2_test' or values['PHASE2_DB_USER']!='phase2_test':
            raise ValueError('Only the dedicated phase-two test schema/user is allowed')
    if node in ('main','data') and app_env=='production':
        if 'test' in values['PHASE2_DB_NAME'].lower() or values['PHASE2_DB_USER']=='phase2_test':
            raise ValueError('Production must use a dedicated production schema/user')
    if node in ('main','data'):
        if app_env=='staging' and not re.fullmatch('[a-f0-9]{48}',values['PHASE2_DB_PASSWORD']):
            raise ValueError('Use the generated test database password')
        if app_env=='production' and len(values['PHASE2_DB_PASSWORD'])<16:
            raise ValueError('Use a production database password of at least 16 characters')
    if node in ('main','ai'):
        db=urlsplit(values['AITRAVEL_DATABASE_URL'])
        if db.scheme!='mysql+pymysql' or db.hostname!=NODE_IP['data'] or db.port!=13306:
            raise ValueError('Database URL must target the configured data node')
        if app_env=='staging' and (db.path!='/aitravel_phase2_test' or db.username!='phase2_test'):
            raise ValueError('Database URL must use the dedicated test schema/user')
        if app_env=='production' and ('test' in db.path.lower() or db.username=='phase2_test'):
            raise ValueError('Database URL must use a production schema/user')
        if node=='main' and db.path!='/'+values['PHASE2_DB_NAME']:
            raise ValueError('Database name differs from PHASE2_DB_NAME')
        if node=='main' and db.password!=values['PHASE2_DB_PASSWORD']: raise ValueError('Database passwords differ')
        api=urlsplit(values['PUBLIC_API_BASE_URL']); media=urlsplit(values['PUBLIC_BASE_URL'])
        if api.scheme!='https' or not api.hostname or api.path or api.query or api.username:
            raise ValueError('PUBLIC_API_BASE_URL must be an HTTPS origin')
        if media.scheme!='https' or media.netloc!=api.netloc or not re.fullmatch('/media/[a-f0-9]{64}',media.path):
            raise ValueError('PUBLIC_BASE_URL must use the generated installation token')
        if node=='main':
            if api.hostname!=values['TEST_DOMAIN'] or media.path!='/media/'+values['LOCAL_MEDIA_TOKEN']: raise ValueError('Domain/media settings differ')
            cache=urlsplit(values['REDIS_URL'])
            if cache.hostname!=NODE_IP['data'] or cache.port!=16379 or not cache.password: raise ValueError('Redis must use the private data node and password')
    if values.get('LOCAL_CLOUD_ENABLED')=='true':
        cloud={'main':[], 'ai':['ALIBABA_CLOUD_ACCESS_KEY_ID','ALIBABA_CLOUD_ACCESS_KEY_SECRET','SEEDREAM_API_KEY','SEEDANCE_API_KEY',
               'SEEDANCE_ASSET_ACCESS_KEY_ID','SEEDANCE_ASSET_ACCESS_KEY_SECRET','SEEDANCE_ASSET_GROUP_ID'],
               'data':['TOS_ACCESS_KEY_ID','TOS_ACCESS_KEY_SECRET','TOS_BUCKET','TOS_REGION','TOS_ENDPOINT','TOS_KEY_PREFIX']}[node]
        for key in cloud:
            if not values.get(key) or 'CHANGE_ME' in values[key]: raise ValueError('Fill '+key)
        if node=='data':
            expected_prefix='phase2-test/' if app_env=='staging' else 'phase2-prod/'
            if not re.fullmatch(re.escape(expected_prefix)+r'(?:[a-zA-Z0-9_-]+/)*',values['TOS_KEY_PREFIX']):
                raise ValueError('Use '+expected_prefix+' storage prefix')
        if node=='ai' and not values['SEEDANCE_ASSET_GROUP_ID'].startswith('group-'): raise ValueError('Invalid asset group ID')
        if node=='ai' and values['SEEDREAM_API_KEY']==values['SEEDANCE_API_KEY']: raise ValueError('Use separate image/video API keys')

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('node',choices=NODE_NAMES)
    p.add_argument('--check-only',action='store_true'); p.add_argument('--no-build',action='store_true'); a=p.parse_args()
    env_path=ROOT/('.env.'+a.node)
    values=read_env(env_path)
    payments=ROOT/'allinpay.env'
    if payments.is_file(): values.update(read_env(payments))
    validate(a.node,values)
    if a.node=='main':
        for name in ('fullchain.pem','privkey.pem'):
            if not (ROOT/'tls'/name).is_file(): raise ValueError('Missing TLS file: tls/'+name)
    if not a.check_only:
        # Binding a private IP fails early on the wrong machine.
        with socket.socket() as s: s.bind((NODE_IP[a.node],0))
        for host,port in ([(NODE_IP['data'],13306)] if a.node in ('main','ai') else []):
            with socket.create_connection((host,port),timeout=5): pass
    env=os.environ.copy(); env.update(values)
    release_info = ROOT / 'release_info.json'
    if release_info.is_file():
        info = json.loads(release_info.read_text(encoding='utf-8'))
        env['BUILD_REVISION'] = str(info.get('source_revision') or 'unknown')
        env['BUILD_TIME'] = str(info.get('built_at') or 'unknown')
    app_env=values.get('APP_ENV','staging').strip().lower()
    project_prefix='hejian-lvpai-test' if app_env=='staging' else 'hejian-lvpai-production'
    compose=['docker','compose','--project-name',project_prefix+'-'+a.node,'--env-file',str(env_path)]
    if payments.is_file(): compose+=['--env-file',str(payments)]
    compose+=['-f',str(ROOT/'infra/staging'/('docker-compose.'+a.node+'.yml'))]
    subprocess.run(compose+['config','--quiet'],env=env,check=True)
    if a.check_only:
        print('Node configuration valid: '+a.node); return
    subprocess.run(compose+['up','-d','--wait','--wait-timeout','180']+(['--no-build'] if a.no_build else ['--build']),env=env,check=True)
    print('Started '+app_env+' node: '+a.node+'; cloud calls enabled: '+values['LOCAL_CLOUD_ENABLED'])

if __name__=='__main__':
    try: main()
    except (ValueError,OSError,subprocess.CalledProcessError) as error:
        # Network/command errors may contain credentials in their repr; print only controlled validation errors.
        print(str(error) if isinstance(error,ValueError) else 'Node startup failed: '+type(error).__name__)
        raise SystemExit(1)
