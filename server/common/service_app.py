"""Internal HTTP authentication and bounded WSGI runtime."""
import os
import jwt
from flask import request, jsonify
from common import service_log


def protect(app, subjects, public=(), public_prefixes=()):
    service_log.install_http_access(app)

    @app.before_request
    def authorize_service():
        if request.path in public or (request.method in ('GET','HEAD') and any(request.path.startswith(p) for p in public_prefixes)): return
        try:
            token=request.headers.get('Authorization','').removeprefix('Bearer ')
            claims=jwt.decode(token,os.environ['AITRAVEL_JWT_SECRET'],algorithms=['HS256'],
                              options={'require':['exp','sub']})
            if claims['sub'] not in subjects: raise ValueError('wrong caller')
        except (jwt.InvalidTokenError, ValueError, KeyError):
            return jsonify(code=401,message='服务身份验证失败'),401


def serve(app,port):
    bind_port=int(os.getenv('PORT',str(port)))
    try:
        from waitress import serve as run
    except ImportError:
        # Local development images may predate waitress; Flask's threaded
        # server keeps the complete local pipeline runnable without a rebuild.
        app.run(host='0.0.0.0',port=bind_port,threaded=True,use_reloader=False)
        return
    run(app,host='0.0.0.0',port=bind_port,
        threads=int(os.getenv('SERVICE_THREADS','32')),
        max_request_body_size=512*1024*1024,channel_timeout=int(os.getenv('CHANNEL_TIMEOUT','120')))
