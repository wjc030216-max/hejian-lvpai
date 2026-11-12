"""Persist installation-relative media references, independent of tunnel hostname."""
import os
from urllib.parse import urlsplit

PREFIX = 'local-media:'


def media_token(path, ttl=None):
    import jwt,time
    if ttl is None:
        try:
            ttl=int(os.getenv('MEDIA_TOKEN_TTL_SECONDS','3600'))
        except ValueError:
            ttl=3600
    ttl=max(300,min(ttl,7*86400))
    now=int(time.time())
    return jwt.encode({'sub':'local-phase2','aud':'phase2-media','path':path,
        'iat':now,'exp':now+ttl},os.environ['AITRAVEL_JWT_SECRET'],algorithm='HS256')


def current_url(base,relative):
    url=base+'/'+relative
    if os.getenv('PHASE2_AUTH_MODE')=='wechat' and relative.startswith(
            ('api/v2/assets/','api/v2/results/','api/v2/portraits/')):
        token=media_token('/'+relative.split('?')[0])
        url=url.split('?')[0]+'?access='+token
    return url


def transform(value, encode=False):
    if isinstance(value, dict):
        return {k:transform(v,encode) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [transform(v,encode) for v in value]
    if not isinstance(value,str):
        return value
    base = (os.getenv('PUBLIC_BASE_URL') or os.getenv('EXTERNAL_DOMAIN') or '').rstrip('/')
    if not base:
        return value
    if value.startswith(PREFIX):
        return value if encode else current_url(base,value[len(PREFIX):])
    current = urlsplit(base)
    old = urlsplit(value)
    if not encode and os.getenv('PHASE2_AUTH_MODE')=='wechat' and (
            not old.scheme and value.startswith(('/api/v2/assets/','/api/v2/results/','/api/v2/portraits/')) or
            old.scheme in ('http','https') and old.netloc==current.netloc and old.path.startswith(
                ('/api/v2/assets/','/api/v2/results/','/api/v2/portraits/'))):
        path=old.path if old.path else value.split('?',1)[0]
        origin=current.scheme+'://'+current.netloc
        return origin+path+'?access='+media_token(path)
    # Same installation token, never accept arbitrary remote paths/hosts.
    if '/media/' in current.path and old.scheme in ('http','https') and old.path.startswith(current.path+'/'):
        relative = old.path[len(current.path)+1:]
        if any(relative.startswith(p) for p in (
                'assets/','api/v2/assets/','api/v2/results/','api/v2/portraits/',
                'aitravel/api/v1/file/download/')):
            ref = PREFIX + relative + ('?'+old.query if old.query else '')
            return ref if encode else current_url(base,ref[len(PREFIX):])
    return value
