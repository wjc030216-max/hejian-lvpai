"""WeChat login and resource ownership; local development remains explicitly local."""
import hashlib
import os
import time
from urllib.parse import urlsplit
import jwt
import requests
from flask import g,request,jsonify
from common import service_log


def mode(): return os.getenv('PHASE2_AUTH_MODE','local')


def issue(subject,audience='phase2-user',ttl=7200,**extra):
    return jwt.encode(dict(sub=subject,aud=audience,iat=int(time.time()),exp=int(time.time())+ttl,**extra),
                      os.environ['AITRAVEL_JWT_SECRET'],algorithm='HS256')


def verify(token,audience):
    return jwt.decode(token,os.environ['AITRAVEL_JWT_SECRET'],algorithms=['HS256'],audience=audience,
                      options={'require':['exp','sub','aud']})


def user(): return getattr(g,'user_id','local-user')


def openid(): return getattr(g,'openid','') or ''


def media_url(url, ttl=3600):
    # 本地联调也签名：云端拉参考图时必须带 access，否则会被 local_guard 拒绝。
    if not url or not os.environ.get('AITRAVEL_JWT_SECRET'): return url
    path=urlsplit(url).path
    if path.startswith(('/api/v2/results/','/api/v2/portraits/','/api/v2/assets/')):
        try:
            subject=user()
        except RuntimeError:
            subject='local-user'
        return url.split('?')[0]+'?access='+issue(subject,'phase2-media',ttl=ttl,path=path)
    return url


def install(app,get_store):
    @app.post('/api/v2/auth/login')
    def login():
        if mode()!='wechat':
            return jsonify(code=200,data={'mode':'local','user_id':'local-user'})
        body=request.get_json(silent=True) or {}
        code=body.get('code')
        appid=os.getenv('WECHAT_APP_ID'); secret=os.getenv('WECHAT_APP_SECRET')
        if not appid or not secret:
            return jsonify(code=503,msg='尚未配置二期微信登录凭据'),503
        if not isinstance(code,str) or not 1<=len(code)<=256:
            return jsonify(code=422,msg='请重新登录'),422
        try:
            response=requests.get('https://api.weixin.qq.com/sns/jscode2session',params={
                'appid':appid,'secret':secret,'js_code':code,'grant_type':'authorization_code'},timeout=(5,15))
            response.raise_for_status(); result=response.json()
            if result.get('errcode') or not result.get('openid'): raise ValueError('login failed')
        except Exception:
            return jsonify(code=401,msg='微信登录失败，请重试'),401
        raw_openid = result['openid']
        uid='wx_'+hashlib.sha256((appid+':'+raw_openid).encode()).hexdigest()
        service_log.event('login', '微信登录成功', context=f"uid={uid}")
        return jsonify(code=200,data={'token':issue(uid,openid=raw_openid),'expires_in':7200,'user_id':uid,'openid':raw_openid,'mode':'wechat'})

    @app.before_request
    def authorize():
        if mode()=='local':
            if os.getenv('APP_ENV','development') not in ('local','local-real','development','test'):
                return jsonify(code=503,msg='当前环境禁止开发身份'),503
            g.user_id='local-user'
            return
        if mode()!='wechat': return jsonify(code=503,msg='登录模式配置无效'),503
        path=request.path
        if not path.startswith('/api/v2/') or path in ('/api/v2/auth/login','/api/v2/health','/api/v2/config','/api/v2/media-ready','/api/v2/scenes','/api/v2/payments/notify'):
            return
        if path == '/api/v2/payments/allinpay/notify':
            return
        if path.startswith('/api/v2/share'):
            return
        if path.startswith('/api/v2/templates/') and path.endswith('/runtime'):
            try:
                # Internal service JWTs deliberately do not use the user audience.
                claims=jwt.decode(request.headers.get('Authorization','').removeprefix('Bearer '),
                    os.environ['AITRAVEL_JWT_SECRET'],algorithms=['HS256'],options={'require':['exp','sub']})
                if claims['sub']=='local-phase2': return
            except jwt.InvalidTokenError: pass
            return jsonify(code=403,msg='生成资料仅供内部服务读取'),403
        elif path.startswith('/api/v2/templates'): return
        if path.startswith(('/api/v2/results/','/api/v2/portraits/','/api/v2/assets/')):
            try:
                claims=verify(request.args.get('access',''),'phase2-media')
                if claims.get('path')!=path: raise ValueError('wrong asset')
                g.user_id=claims['sub']; return
            except (jwt.InvalidTokenError,ValueError):
                return jsonify(code=401,msg='素材链接已过期，请重新打开作品'),401
        try:
            claims=verify(request.headers.get('Authorization','').removeprefix('Bearer '),'phase2-user')
            g.user_id=claims['sub']
            g.openid=claims.get('openid','')
        except jwt.InvalidTokenError:
            return jsonify(code=401,msg='请重新登录'),401
        pid=(request.view_args or {}).get('pid')
        if '/templates/P2S-' in path: pid=(request.view_args or {}).get('template_code','')[4:]
        if pid:
            record=get_store().get(pid)
            if not record or record.get('wx_openid')!=user():
                return jsonify(code=404,msg='作品不存在'),404
