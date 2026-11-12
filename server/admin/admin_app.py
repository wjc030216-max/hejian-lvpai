"""Chinese operator console: templates, immutable uploads, task logs and node health."""
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
import jwt
import requests
from flask import Flask,request,jsonify,g,send_from_directory
from sqlalchemy import text,inspect
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from common.admin_password import verify_password
from common.media_refs import transform as _base_transform
from common.redaction import redact_sensitive_text
from admin.importer import import_archive,batch_code,MEDIA
from admin import media_jobs
from admin.store import AdminStore
from common import service_log
from phase2.pricing import TIER_PRICES
from phase2.retention_service import expired as retention_expired, expires_at as retention_expires_at

ROOT=Path(__file__).resolve().parent
PROJECT_STATUSES=('draft','uploading','pending_payment','portrait_submit','portrait_unknown',
                  'portrait_processing','portrait_saving','portrait_retry','video_submit',
                  'video_unknown','video_processing','video_retry','needs_review','completed',
                   'terminated','refund_pending','refund_failed','refunded','archived','expired')


def project_summary(payload):
    template=payload.get('template') or {}
    roles=payload.get('roles') or []
    return dict(
        project_id=payload.get('project_id',''),
        owner_id=payload.get('wx_openid',''),
        template_code=payload.get('template_code',''),
        template_name=template.get('video_name',''),
        actor_count=template.get('actor_count') or len(roles),
        location=template.get('location_association',''),
        status=payload.get('status',''),
        stage=payload.get('stage',''),
        order_no=payload.get('order_no',''),
        payment_mode=payload.get('payment_mode',''),
        payment_status=payload.get('payment_status',''),
        amount_fen=payload.get('amount_fen',0),
        amount_paid=payload.get('amount_paid',0),
        refund_status=payload.get('refund_status',''),
        refund_no=payload.get('refund_no',''),
        media_cleanup=payload.get('media_cleanup',''),
        media_cleanup_error=redact_log_text(payload.get('media_cleanup_error','')),
        error=redact_log_text(payload.get('error','')),
        progress=payload.get('progress',0),
        portrait_url=transform(payload.get('portrait_url') or ''),
        video_url=transform(payload.get('video_url') or ''),
        created_at=payload.get('created_at',0),
        paid_at=payload.get('paid_at',0),
        completed_at=payload.get('completed_at',0),
        updated_at=payload.get('updated_at',0),
    )


def task_summary(task_id, kind, payload):
    template_code=str(payload.get('template_code') or '')
    if kind=='image':
        project_id=''
    elif template_code.startswith('P2S-'):
        project_id=template_code[4:]
    else:
        run_id=str(payload.get('wx_openid') or '')
        project_id=run_id.split('-r',1)[0]
    steps=payload.get('steps') or {}
    cloud_task_id=''
    cloud_tasks={}
    for key in ('video_submit','provider'):
        result=(steps.get(key) or {}).get('result')
        if isinstance(result,str):
            cloud_task_id=result
            break
    for name, step in steps.items():
        if name.startswith('video_submit:') and isinstance((step or {}).get('result'), str):
            cloud_tasks[name.split(':', 1)[1]] = step['result']
            cloud_task_id = cloud_task_id or step['result']
    video_groups=list(((payload.get('video_process') or {}).get('groups') or {}).keys())
    return dict(
        task_id=task_id,kind=kind,project_id=project_id,status=payload.get('status',''),
        attempt=payload.get('attempt',1),message=redact_log_text(payload.get('error','')),
        cloud_task_id=cloud_task_id,cloud_tasks=cloud_tasks,video_groups=video_groups,template_code=template_code,
        owner_id=(payload.get('request') or {}).get('user_id','') if kind=='image' else '',
        created_at=payload.get('created_at'),finished_at=payload.get('finished_at'),
        steps={name:{k:v for k,v in (step or {}).items() if k!='result'}
               for name,step in steps.items()},
    )


def redact_log_text(value):
    return redact_sensitive_text(value)


PUBLIC_API_BASE_URL = os.getenv('PUBLIC_API_BASE_URL', '').rstrip('/')
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')


def _origin(value):
    parsed = urlsplit(str(value or ''))
    return f'{parsed.scheme}://{parsed.netloc}' if parsed.scheme and parsed.netloc else ''


def media_origins():
    bucket = os.getenv('TOS_BUCKET', '').strip()
    region = os.getenv('TOS_REGION', '').strip()
    tos_origin = (
        f'https://{bucket}.tos-{region}.volces.com'
        if bucket and region else ''
    )
    return sorted({origin for origin in (
        _origin(PUBLIC_API_BASE_URL), _origin(PUBLIC_BASE_URL), tos_origin,
    ) if origin})


def _rewrite_media(value):
    """Point persisted local API URLs at the current local gateway."""
    if isinstance(value, dict):
        return {key: _rewrite_media(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rewrite_media(item) for item in value]
    if isinstance(value, str) and value.startswith(('http://', 'https://')):
        parsed = urlsplit(value)
        if parsed.hostname in ('127.0.0.1', 'localhost') and parsed.path.startswith(
                ('/api/v2/results/', '/api/v2/portraits/', '/api/v2/assets/')):
            if PUBLIC_API_BASE_URL:
                return PUBLIC_API_BASE_URL + parsed.path + (
                    ('?' + parsed.query) if parsed.query else '')
    return value


def transform(value, encode=False):
    """Use installation-relative media references in the operator console."""
    result = _base_transform(value, encode)
    return result if encode else _rewrite_media(result)


def _media_urls(value):
    result = []
    if isinstance(value, dict):
        for item in value.values():
            result.extend(_media_urls(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            result.extend(_media_urls(item))
    elif isinstance(value, str) and (
            value.startswith('http://') or value.startswith('https://')
            or value.startswith('local-media:')):
        result.append(transform(value))
    return list(dict.fromkeys(result))


def _media_records(value, group=''):
    result = []
    if isinstance(value, dict):
        if value.get('url'):
            record = {
                key: transform(item) if key == 'url' else item
                for key, item in value.items()
            }
            if group and not record.get('group'):
                record['group'] = group
            result.append(record)
        else:
            for key, item in value.items():
                child_group = str(key) if len(key) <= 16 else group
                result.extend(_media_records(item, child_group))
    elif isinstance(value, (list, tuple)):
        for item in value:
            result.extend(_media_records(item, group))
    elif isinstance(value, str) and (
            value.startswith('http://') or value.startswith('https://')
            or value.startswith('local-media:')):
        result.append({'url': transform(value), 'group': group})
    unique = {}
    for item in result:
        key = (str(item.get('group') or ''), item.get('url'))
        unique.setdefault(key, item)
    return list(unique.values())


def _portrait_reference_images(payload, process):
    """Return the exact images/submission order, including legacy tasks."""
    stored = process.get('reference_images')
    if isinstance(stored, list):
        references = []
        for position, item in enumerate(stored, 1):
            if not isinstance(item, dict) or not item.get('url'):
                continue
            try:
                index = int(item.get('index') or position)
            except (TypeError, ValueError):
                index = position
            references.append({
                'index': index,
                'kind': item.get('kind') or '',
                'character_id': item.get('character_id'),
                'source': item.get('source') or '',
                'url': transform(str(item['url'])),
            })
        if references:
            return sorted(references, key=lambda item: item['index'])

    mapping = payload.get('role_mapping') or []
    if not isinstance(mapping, list):
        return []
    roles = [role for role in mapping if isinstance(role, dict)]
    try:
        roles.sort(key=lambda role: int(role.get('role_index') or 0))
    except (TypeError, ValueError):
        pass
    references = []
    for position, role in enumerate(roles, 1):
        face = role.get('user_face_url') or role.get('template_face_url')
        if not face:
            continue
        try:
            face_index = int(role.get('face_index') or position)
        except (TypeError, ValueError):
            face_index = position
        references.append({
            'index': face_index,
            'kind': 'face',
            'character_id': role.get('character_id'),
            'source': role.get('source') or '',
            'url': transform(str(face)),
        })
    for position, role in enumerate(roles, 1):
        view = role.get('three_view_url')
        if not view:
            continue
        try:
            view_index = int(role.get('three_view_index') or len(roles) + position)
        except (TypeError, ValueError):
            view_index = len(roles) + position
        references.append({
            'index': view_index,
            'kind': 'three_view',
            'character_id': role.get('character_id'),
            'source': role.get('source') or '',
            'url': transform(str(view)),
        })
    scene = next((
        item for item in reversed(process.get('inputs') or [])
        if isinstance(item, dict) and item.get('character_id') is None and item.get('url')
    ), None)
    if scene:
        try:
            scene_index = int(scene.get('index') or len(roles) * 2 + 1)
        except (TypeError, ValueError):
            scene_index = len(roles) * 2 + 1
        references.append({
            'index': scene_index,
            'kind': 'scene',
            'character_id': None,
            'source': scene.get('source') or 'template',
            'url': transform(str(scene['url'])),
        })
    return sorted(references, key=lambda item: item['index'])


def monitor_task(task_id, kind, payload, events=None):
    payload = transform(payload)
    process = (payload.get('portrait_process') or payload.get('video_process')
               or payload.get('request') or {})
    prompt = (process.get('prompt') or payload.get('portrait_prompt')
              or payload.get('video_prompt') or '')
    reference_images = (_portrait_reference_images(payload, process)
                        if kind == 'character' and payload.get('role_mapping') else [])
    audit_inputs = _media_urls(process.get('inputs') or [])
    audit_records = _media_records(process.get('inputs') or [])
    inputs = [item['url'] for item in reference_images] if reference_images else audit_inputs
    input_records = reference_images or audit_records
    if not inputs:
        inputs = _media_urls(payload.get('actors') or [])
        input_records = _media_records(payload.get('actors') or [])
    if not inputs:
        inputs = _media_urls((payload.get('request') or {}).get('reference_image_urls') or [])
        input_records = _media_records(
            (payload.get('request') or {}).get('reference_image_urls') or [])
    outputs = _media_urls(process.get('outputs') or [])
    if process.get('provider_video_url'):
        outputs.extend(_media_urls(process['provider_video_url']))
    if not outputs:
        outputs = _media_urls(payload.get('steps') or {})
    if payload.get('result'):
        outputs.extend(_media_urls(payload['result']))
    steps = {}
    for name, step in (payload.get('steps') or {}).items():
        if not isinstance(step, dict):
            continue
        steps[name] = {key: transform(value) if isinstance(value, (dict, list)) else value
                       for key, value in step.items()}
    return {
        'task_id': task_id,
        'kind': kind,
        'status': payload.get('status', ''),
        'prompt': prompt,
        'inputs': inputs,
        'input_records': input_records,
        'reference_images': reference_images,
        'audit_inputs': audit_inputs,
        'audit_records': audit_records,
        'outputs': list(dict.fromkeys(outputs)),
        'steps': steps,
        'role_mapping': transform(payload.get('role_mapping') or []),
        'reference_plan': transform(payload.get('reference_plan') or []),
        'events': events or [],
        'error': redact_log_text(payload.get('error', '')),
        'created_at': payload.get('created_at'),
        'finished_at': payload.get('finished_at'),
    }


def monitor_project(project, tasks, events=None):
    data_expired = retention_expired(project)
    if data_expired:
        project = dict(project)
        project.update(portrait_url='', video_url='', roles=[], title='数据保留期已结束',
                       description='用户过程数据、提示词、输入图片和生成媒体已按 90 天策略清理')
    summary = project_summary(project)
    summary.update(
        portrait_url=transform(project.get('portrait_url') or ''),
        video_url=transform(project.get('video_url') or ''),
        title=project.get('title', ''),
        description=project.get('description', ''),
        template_name=(project.get('template') or {}).get('video_name', ''),
        roles=[{
            'character_id': role.get('character_id'),
            'display_name': role.get('display_name'),
            'person_uid': role.get('person_uid'),
            'check_status': role.get('check_status'),
            'photo_url': transform(role.get('photo_url') or ''),
        } for role in project.get('roles') or []],
        tasks=tasks,
        events=events or [],
        retention_expires_at=retention_expires_at(project),
        retention_expired=data_expired,
    )
    return summary


def load_project_monitor(db, project_id, project):
    """Load one project's tasks lazily for the expanded monitor detail."""
    tasks = []
    project_events = []
    if retention_expired(project):
        return monitor_project(project, tasks, project_events)
    runtime = project.get('runtime_code') or ''
    if inspect(db).has_table('phase2_tasks'):
        for task_id, kind, payload in db.execute(text(
                'SELECT id,kind,payload FROM phase2_tasks '
                'WHERE run_id LIKE :run OR payload LIKE :runtime '
                'ORDER BY id DESC LIMIT 100'),
                dict(run=project_id + '%', runtime='%' + runtime + '%')):
            try:
                task_events = []
                if inspect(db).has_table('phase2_task_events'):
                    task_events = [dict(event) for event in db.execute(text(
                        'SELECT status,message,created FROM phase2_task_events '
                        'WHERE task_id=:id ORDER BY created DESC LIMIT 100'),
                        dict(id=task_id)).mappings()]
                    for event in task_events:
                        event['message'] = redact_log_text(event.get('message'))
                tasks.append(monitor_task(
                    task_id, kind, json.loads(payload), task_events))
            except (TypeError, ValueError):
                continue
    if inspect(db).has_table('phase2_events'):
        project_events = [dict(event) for event in db.execute(text(
            'SELECT kind,title,created FROM phase2_events '
            'WHERE project_id=:pid ORDER BY created DESC LIMIT 100'),
            dict(pid=project_id)).mappings()]
    return monitor_project(project, tasks, project_events)


def create_app(store=None,asset_root=None):
    app=Flask(__name__,static_folder=None); app.config['MAX_CONTENT_LENGTH']=256*1024*1024
    # Exactly one private reverse proxy (the main gateway) is trusted.
    app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1)
    service_log.install("admin")
    service_log.install_http_access(app)
    store=store or AdminStore(os.environ['AITRAVEL_DATABASE_URL'])
    assets=Path(asset_root or os.getenv('ADMIN_TEMPLATE_ROOT','/var/lib/hejian-lvpai/templates')); assets.mkdir(parents=True,exist_ok=True)
    media_jobs.start(store,assets)
    secret=os.environ['ADMIN_JWT_SECRET']; username=os.getenv('ADMIN_USERNAME','admin'); password_hash=os.environ['ADMIN_PASSWORD_HASH']
    if len(secret)<32 or secret==os.getenv('AITRAVEL_JWT_SECRET'): raise RuntimeError('管理员登录必须使用独立密钥')

    def _cleanup_revisions(revisions,action='删除'):
        import re
        cleaned=0
        for revision in revisions:
            if not isinstance(revision,str) or not re.fullmatch(r'[0-9a-f]{32}',revision):
                continue
            if store.is_revision_referenced(revision):
                continue
            folder=assets/revision
            try:
                if folder.is_dir() and folder.resolve().parent==assets.resolve():
                    shutil.rmtree(folder)
                    cleaned+=1
            except OSError:
                pass
        if cleaned:
            service_log.event('admin',action+'模板后清理旧素材目录',context=f'removed={cleaned}')
        return cleaned

    @app.before_request
    def authorize():
        if not request.path.startswith('/admin/api/') or request.path=='/admin/api/login': return
        try:
            claims=jwt.decode(request.cookies.get('phase2_admin',''),secret,algorithms=['HS256'],audience='phase2-admin',options={'require':['exp','sub','csrf']})
            if claims['sub']!=username: raise ValueError('unknown admin')
            if request.method not in ('GET','HEAD'):
                if not hmac.compare_digest(claims['csrf'],request.headers.get('X-CSRF-Token','')): raise ValueError('invalid csrf')
            g.admin=claims
        except (jwt.InvalidTokenError,ValueError): return jsonify(message='请重新登录管理员账号'),401

    @app.after_request
    def headers(response):
        allowed_media = ' '.join(media_origins())
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy']=("default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data: " + allowed_media + "; media-src 'self' " + allowed_media
            + "; connect-src 'self'; form-action 'self'; frame-ancestors 'none'")
        if request.is_secure:
            response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
        if request.path.startswith('/admin/api/'): response.headers['Cache-Control']='no-store'
        return response

    @app.errorhandler(Exception)
    def error(error):
        from sqlalchemy.exc import IntegrityError
        if isinstance(error,IntegrityError): return jsonify(message='同名模板已被其他上传请求保存，请刷新列表后选择更新'),409
        if isinstance(error,HTTPException): return jsonify(message=error.description),error.code
        if isinstance(error,ValueError): return jsonify(message=str(error)),422
        app.logger.exception('Admin operation failed: %s',type(error).__name__)
        return jsonify(message='管理服务暂时不可用，请检查数据库或文件存储'),503

    @app.get('/health')
    def health():
        with store.engine.connect() as db: db.execute(text('SELECT 1'))
        return jsonify(status='ok',service='phase2-admin',
                       build_revision=os.getenv('BUILD_REVISION','unknown'))

    @app.get('/admin/')
    def index(): return send_from_directory(ROOT/'static','index.html')
    @app.get('/admin/static/<path:name>')
    def static(name): return send_from_directory(ROOT/'static',name)

    @app.post('/admin/api/login')
    def login():
        body=request.get_json(silent=True) or {}; password=body.get('password','')
        attempted_username=str(body.get('username',''))[:256]
        if not isinstance(password,str) or len(password)>256: raise ValueError('登录资料无效')
        source=hashlib.sha256(((request.remote_addr or 'unknown')+'|'+attempted_username).encode()).hexdigest()
        now=time.time()
        if not store.reserve_login_attempt(source,10,900):
            return jsonify(message='登录尝试过多，请 15 分钟后再试'),429
        valid=verify_password(password,password_hash)
        if not hmac.compare_digest(attempted_username,username) or not valid:
            return jsonify(message='账号或密码不正确'),401
        csrf=secrets.token_hex(24)
        token=jwt.encode(dict(sub=username,aud='phase2-admin',iat=int(now),exp=int(now)+28800,csrf=csrf),secret,algorithm='HS256')
        response=jsonify(username=username,csrf=csrf,media_origins=media_origins())
        response.set_cookie('phase2_admin',token,
                            secure=request.is_secure or os.getenv('APP_ENV') in ('staging','production'),
                            httponly=True,samesite='Strict',path='/admin/',max_age=28800)
        with store.engine.begin() as db:
            store.audit(db,username,'管理员登录','',dict(source_hash=source[:16],
                                                        secure_cookie=bool(response.headers.get('Set-Cookie','').find('Secure')>=0)))
        store.clear_login_attempts(source)
        return response

    @app.get('/admin/api/session')
    def session(): return jsonify(username=g.admin['sub'],csrf=g.admin['csrf'],
                                  media_origins=media_origins())
    @app.post('/admin/api/logout')
    def logout():
        response=jsonify(ok=True); response.delete_cookie('phase2_admin',path='/admin/'); return response

    def page():
        try: offset=int(request.args.get('offset',0))
        except ValueError: raise ValueError('分页参数无效')
        if offset<0: raise ValueError('分页参数无效')
        return offset

    @app.get('/admin/api/templates')
    def templates():
        q=request.args.get('q','').strip()
        if len(q)>100: raise ValueError('搜索内容过长')
        raw_status=request.args.get('status','').strip()
        raw_actor=request.args.get('actor_count','').strip()
        raw_duration=request.args.get('duration','').strip()
        scene=request.args.get('scene','').strip()[:128]
        sort=request.args.get('sort','updated').strip()
        if sort not in ('updated','heat','name'): sort='updated'
        try:
            size=int(request.args.get('size',30))
        except ValueError:
            raise ValueError('每页数量无效')
        if size not in (30,50,100): size=30
        status=int(raw_status) if raw_status in ('0','1') else None
        actor_count=int(raw_actor) if raw_actor in ('1','2','3') else None
        if raw_actor and actor_count is None: raise ValueError('人数筛选无效')
        if raw_duration and raw_duration not in ('15','30'): raise ValueError('时长筛选无效')
        duration_seconds=int(raw_duration) if raw_duration else None
        result=store.list_templates(q,page(),size,status=status,
                                    actor_count=actor_count,scene=scene,
                                    duration_seconds=duration_seconds,sort=sort)
        for item in result['items']:
            item['standard_price_fen']=TIER_PRICES.get(item.get('actor_count'),0)
        return jsonify(transform(result))

    @app.get('/admin/api/templates/codes')
    def template_codes():
        """All codes matching the current filters, so batch actions work across pages."""
        q=request.args.get('q','').strip()
        if len(q)>100: raise ValueError('搜索内容过长')
        raw_status=request.args.get('status','').strip()
        raw_actor=request.args.get('actor_count','').strip()
        raw_duration=request.args.get('duration','').strip()
        scene=request.args.get('scene','').strip()[:128]
        status=int(raw_status) if raw_status in ('0','1') else None
        actor_count=int(raw_actor) if raw_actor in ('1','2','3') else None
        if raw_actor and actor_count is None: raise ValueError('人数筛选无效')
        if raw_duration and raw_duration not in ('15','30'): raise ValueError('时长筛选无效')
        duration_seconds=int(raw_duration) if raw_duration else None
        codes=store.list_template_codes(q,status=status,actor_count=actor_count,scene=scene,
                                        duration_seconds=duration_seconds)
        return jsonify(codes=codes,total=len(codes))

    @app.post('/admin/api/templates/upload')
    def upload():
        import hashlib,re,zipfile
        file=request.files.get('file'); replacement=request.form.get('template_code') or None
        batch=request.form.get('batch_upload')=='1'
        if file is None or not file.filename.lower().endswith('.zip'): raise ValueError('请选择单个模板的 ZIP 文件')
        if batch and replacement: raise ValueError('批量新增不能同时替换现有模板')
        if replacement and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',replacement): raise ValueError('模板编号无效')
        digest=hashlib.sha256()
        while True:
            chunk=file.stream.read(1024*1024)
            if not chunk: break
            digest.update(chunk)
        file.stream.seek(0)
        source_sha256=digest.hexdigest()
        if not replacement:
            # 内容去重：同一个 ZIP 换名后重复上传也能拦住。
            same_archive=store.find_by_archive_hash(source_sha256)
            if same_archive:
                return jsonify(message='该模板包已上传过（'+same_archive[0]+' '+same_archive[1]+'），已跳过',
                               status='skipped',template_code=same_archive[0]),200
        code=replacement
        if batch:
            # 去重只按压缩包文件名：同名文件派生同一 code，重复上传直接跳过。
            code=batch_code(file.filename)
            with store.engine.connect() as db:
                exists=db.execute(text('SELECT template_code FROM phase2_templates WHERE template_code=:code'),dict(code=code)).first()
            if exists:
                return jsonify(message='同名压缩包已上传过，已跳过',status='skipped',template_code=exists[0])
        elif code:
            with store.engine.connect() as db:
                exists=db.execute(text('SELECT 1 FROM phase2_templates WHERE template_code=:code'),dict(code=code)).first()
            if not exists: raise ValueError('要替换的模板不存在')
        try: item=import_archive(file.stream,assets,code,optimize=False)
        except (zipfile.BadZipFile,OSError,KeyError,TypeError) as exc: raise ValueError('模板文件读取失败：'+str(exc)) from exc
        item['source_sha256']=source_sha256
        if batch:
            raw=file.filename
            name=Path(str(raw).replace('\\','/')).name
            item['source_filename']=name[:-4] if name.lower().endswith('.zip') else name
        if not replacement:
            same=store.find_same_name(item['location_association'],item['video_name'])
            if same:
                shutil.rmtree(assets/item['asset_revision'],ignore_errors=True)
                return jsonify(message='同景区同名称模板已存在，已跳过',
                               status='skipped',template_code=same),200
            # 并发兜底：同一份压缩包在导入过程中被另一次请求抢先写入。
            same_archive=store.find_by_archive_hash(source_sha256)
            if same_archive:
                shutil.rmtree(assets/item['asset_revision'],ignore_errors=True)
                return jsonify(message='该模板包已上传过（'+same_archive[0]+' '+same_archive[1]+'），已跳过',
                               status='skipped',template_code=same_archive[0]),200
        try:
            store.save_upload(item,g.admin['sub'],replace=bool(replacement),publish=False)
        except Exception:
            shutil.rmtree(assets/item['asset_revision'],ignore_errors=True)
            raise
        media_jobs.enqueue(store,item['template_code'],item['asset_revision'])
        # Keep the previous immutable revision for rollback and in-flight
        # projects. The maintenance job removes it only after it is unreferenced.
        return jsonify(message='模板校验通过，正在后台压缩，完成后自动上架',status='processing',
                       published=False,template_code=item['template_code'],
                       duration_seconds=item.get('duration_seconds',15),
                       segment_count=item.get('segment_count',1)),202

    @app.patch('/admin/api/templates/<code>')
    def edit(code):
        store.edit(code,request.get_json(silent=True) or {},g.admin['sub'])
        return jsonify(message='已保存，游客端约 15 秒内更新')

    @app.post('/admin/api/templates/batch-delete')
    def batch_delete():
        import re
        body=request.get_json(silent=True) or {}
        codes=body.get('codes')
        if not isinstance(codes,list) or not codes or len(codes)>200:
            raise ValueError('请选择要删除的模板')
        for code in codes:
            if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',code):
                raise ValueError('模板编号无效')
        deleted,revisions=store.delete_templates(codes,g.admin['sub'])
        _cleanup_revisions(revisions,'删除')
        return jsonify(message='已删除 '+str(deleted)+' 个模板',deleted=deleted)

    @app.post('/admin/api/templates/batch-heat')
    def batch_heat():
        import re
        body=request.get_json(silent=True) or {}
        codes=body.get('codes'); heat=body.get('heat_base')
        if not isinstance(codes,list) or not codes or len(codes)>200:
            raise ValueError('请选择要设置热度的模板')
        for code in codes:
            if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',code):
                raise ValueError('模板编号无效')
        changed=store.batch_heat(codes,heat,g.admin['sub'])
        return jsonify(message='已设置 '+str(changed)+' 个模板的热度',updated=changed)

    @app.post('/admin/api/templates/batch-status')
    def batch_status():
        import re
        body=request.get_json(silent=True) or {}
        codes=body.get('codes'); status=body.get('status')
        if not isinstance(codes,list) or not codes or len(codes)>200:
            raise ValueError('请选择要上架或下架的模板')
        for code in codes:
            if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',code):
                raise ValueError('模板编号无效')
        if type(status) is not int or status not in (0,1):
            raise ValueError('上架状态无效')
        changed=store.batch_status(codes,status,g.admin['sub'])
        return jsonify(message=('已上架 ' if status else '已下架 ')+str(changed)+' 个模板',updated=changed)

    @app.get('/media-assets/<revision>/<path:filename>')
    def media(revision,filename):
        import re
        if not re.fullmatch('[0-9a-f]{32}',revision) or Path(filename).suffix.lower() not in MEDIA: return jsonify(message='文件不存在'),404
        mime={
            '.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp',
            '.mp4':'video/mp4','.mp3':'audio/mpeg','.wav':'audio/wav',
        }.get(Path(filename).suffix.lower()) or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        response=send_from_directory(assets/revision,filename,mimetype=mime,
                                     max_age=0 if mime=='video/mp4' else 31536000)
        if mime=='video/mp4':
            # Preview files may be replaced in place; revalidate so phones do not
            # keep a stale 1080p sample in the year-long image cache.
            response.headers['Cache-Control']='no-cache, must-revalidate'
        return response

    @app.get('/admin/api/tasks')
    def tasks():
        status=request.args.get('status',''); offset=page()
        if status and status not in ('pending','running','recovering','completed','failed','needs_review'): raise ValueError('任务状态无效')
        q=request.args.get('q','').strip()
        if len(q)>100: raise ValueError('搜索内容过长')
        with store.engine.connect() as db:
            if not inspect(db).has_table('phase2_tasks'): return jsonify(items=[],total=0,next_offset=None)
            where=[]; params=dict(status=status,offset=offset)
            if status:
                where.append('status=:status')
            if q:
                where.append('(id LIKE :q OR run_id LIKE :q OR payload LIKE :q)')
                params['q']='%'+q+'%'
            clause=(' WHERE '+' AND '.join(where)) if where else ''
            total=db.execute(text('SELECT COUNT(*) FROM phase2_tasks'+clause),params).scalar()
            created_at=("CAST(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.created_at')),0) AS DECIMAL(20,3))"
                        if store.engine.dialect.name=='mysql'
                        else "CAST(COALESCE(json_extract(payload,'$.created_at'),0) AS REAL)")
            order=("ORDER BY CASE status WHEN 'needs_review' THEN 0 WHEN 'failed' THEN 1 "
                   "WHEN 'recovering' THEN 2 WHEN 'running' THEN 3 WHEN 'pending' THEN 4 ELSE 5 END,"
                   + created_at + " DESC,id DESC")
            rows=db.execute(text('SELECT id,kind,run_id,status,payload FROM phase2_tasks'+clause+' '+order+' LIMIT 30 OFFSET :offset'),params).mappings()
            items=[]
            for row in rows:
                try: p=json.loads(row['payload'])
                except (TypeError,ValueError): p={}
                items.append(task_summary(row['id'],row['kind'],p))
            project_ids=[item['project_id'] for item in items
                         if item['project_id'].startswith('p2_')]
            project_rows={}
            if project_ids and inspect(db).has_table('phase2_projects'):
                from sqlalchemy import bindparam
                for pid,payload in db.execute(text(
                        'SELECT id,payload FROM phase2_projects WHERE id IN :ids'
                ).bindparams(bindparam('ids',expanding=True)),dict(ids=project_ids)):
                    try:
                        summary=project_summary(json.loads(payload))
                        project_rows[pid]={k:summary[k] for k in
                                           ('template_name','order_no','owner_id')}
                        project_rows[pid]['project_status']=summary['status']
                    except (TypeError,ValueError):
                        pass
            for item in items:
                item.update(project_rows.get(item['project_id'],{}))
        return jsonify(items=items,total=total,next_offset=offset+30 if offset+30<total else None)

    @app.get('/admin/api/user-monitor')
    def user_monitor():
        offset = page()
        q = request.args.get('q', '').strip()
        if len(q) > 100:
            raise ValueError('搜索内容过长')
        where = ' WHERE owner_id LIKE :q' if q else ''
        params = dict(offset=offset, q='%' + q + '%')
        with store.engine.connect() as db:
            created_at = ("CAST(JSON_EXTRACT(payload,'$.created_at') AS DECIMAL(20,3))"
                          if store.engine.dialect.name == 'mysql'
                          else "CAST(json_extract(payload,'$.created_at') AS REAL)")
            total = db.execute(text('SELECT COUNT(DISTINCT owner_id) FROM phase2_projects' + where),
                               params).scalar()
            rows = db.execute(text(
                'SELECT owner_id,COUNT(*) AS project_count,COUNT(order_no) AS order_count,'
                'MAX(COALESCE(paid_at,' + created_at + ',0)) AS last_activity '
                'FROM phase2_projects' + where +
                ' GROUP BY owner_id ORDER BY last_activity DESC,owner_id LIMIT 50 OFFSET :offset'
            ), params).mappings()
            users = [dict(row) for row in rows]
        return jsonify(items=users, total=total,
                       next_offset=offset + 50 if offset + 50 < total else None)

    @app.get('/admin/api/user-monitor/<owner_id>')
    def user_monitor_detail(owner_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', owner_id):
            raise ValueError('用户编号无效')
        include_tasks = request.args.get('tasks', '1') != '0'
        with store.engine.connect() as db:
            created_at = ("CAST(JSON_EXTRACT(payload,'$.created_at') AS DECIMAL(20,3))"
                          if store.engine.dialect.name == 'mysql'
                          else "CAST(json_extract(payload,'$.created_at') AS REAL)")
            rows = list(db.execute(text(
                'SELECT id,payload FROM phase2_projects WHERE owner_id=:owner '
                'ORDER BY COALESCE(paid_at,' + created_at + ',0) DESC LIMIT 100'
            ), dict(owner=owner_id)).mappings())
            projects = []
            for row in rows:
                try:
                    project = json.loads(row['payload'])
                except (TypeError, ValueError):
                    continue
                pid = row['id']
                projects.append(load_project_monitor(db, pid, project)
                                if include_tasks else monitor_project(project, [], []))
        return jsonify(owner_id=owner_id, projects=projects)

    @app.get('/admin/api/tasks/<task_id>')
    def task_detail(task_id):
        with store.engine.connect() as db:
            row=db.execute(text('SELECT id,kind,run_id,status,payload FROM phase2_tasks WHERE id=:id'),
                           dict(id=task_id)).mappings().first()
            if not row: raise ValueError('任务不存在')
            try: payload=json.loads(row['payload'])
            except (TypeError,ValueError): payload={}
            summary=task_summary(row['id'],row['kind'],payload)
            if inspect(db).has_table('phase2_task_events'):
                events=[dict(r) for r in db.execute(text(
                    'SELECT status,message,created FROM phase2_task_events '
                    'WHERE task_id=:id ORDER BY created DESC LIMIT 100'),dict(id=task_id)).mappings()]
                for event in events:
                    event['message']=redact_log_text(event.get('message'))
            else:
                events=[]
            result = monitor_task(row['id'], row['kind'], payload, events)
            result.update(summary)
            result['events'] = events
        return jsonify(result)

    @app.get('/admin/api/projects/<pid>/monitor')
    def project_monitor(pid):
        with store.engine.connect() as db:
            row = db.execute(text('SELECT payload FROM phase2_projects WHERE id=:id'),
                             dict(id=pid)).first()
            if not row:
                raise ValueError('项目不存在')
            try:
                project = json.loads(row[0])
            except (TypeError, ValueError):
                raise ValueError('项目数据损坏') from None
            return jsonify(load_project_monitor(db, pid, project))

    @app.post('/admin/api/tasks/<task_id>/resolve')
    def task_resolve(task_id):
        body=request.get_json(silent=True) or {}
        action=body.get('action')
        if action not in ('not_submitted','attach_video'):
            raise ValueError('任务处理动作无效')
        with store.engine.begin() as db:
            lock=' FOR UPDATE' if store.engine.dialect.name=='mysql' else ''
            row=db.execute(text('SELECT id,kind,status,payload FROM phase2_tasks WHERE id=:id'+lock),
                           dict(id=task_id)).mappings().first()
            if not row:
                raise ValueError('任务不存在')
            if row['status']!='needs_review':
                raise ValueError('只有待核实任务可以人工处理')
            try: payload=json.loads(row['payload'])
            except (TypeError,ValueError):
                raise ValueError('任务数据无法解析') from None
            if action=='attach_video':
                cloud_id=str(body.get('cloud_task_id') or '').strip()
                if row['kind']!='video' or not re.fullmatch(r'[A-Za-z0-9_-]{4,128}',cloud_id):
                    raise ValueError('视频云任务编号无效')
                process=payload.get('video_process') or {}
                group_codes=[str(code) for code in (process.get('groups') or {})]
                group_code=str(body.get('group_code') or '').strip().upper()
                if len(group_codes)>1:
                    if group_code not in group_codes:
                        raise ValueError('双段视频必须选择要关联的片段组')
                    submit_name,poll_name='video_submit:'+group_code,'video_poll:'+group_code
                elif group_codes:
                    group_code=group_codes[0]
                    submit_name,poll_name='video_submit:'+group_code,'video_poll:'+group_code
                else:
                    submit_name,poll_name='video_submit','video_poll'
                payload.setdefault('steps',{})[submit_name]={
                    'status':'done','result':cloud_id,'finished_at':time.time()}
                for name in (poll_name,'compose','upload'):
                    payload['steps'].pop(name,None)
                if group_code and group_code in (process.get('groups') or {}):
                    process['groups'][group_code].update(
                        status='pending',error='',provider_video_url='',cloud_task_id=cloud_id)
                    process['completed_segments']=sum(
                        state.get('status')=='completed'
                        for state in process['groups'].values())
                    process['phase']='preparing'
                    payload['video_process']=process
            else:
                submitting=[name for name,step in (payload.get('steps') or {}).items()
                            if (step or {}).get('status')=='submitting']
                if not submitting:
                    raise ValueError('没有可确认的未完成云端提交')
                for name in submitting:
                    payload['steps'].pop(name,None)
            payload.update(status='pending',next_run=0,error='',cloud_failed=False)
            db.execute(text('UPDATE phase2_tasks SET status=:status,next_run=0,payload=:payload '
                            'WHERE id=:id'),
                       dict(status='pending',payload=json.dumps(payload,ensure_ascii=False),
                            id=task_id))
            store.audit(db,g.admin['sub'],'处理待核实任务',task_id,
                        dict(action=action,group_code=str(body.get('group_code') or '')[:16],
                             cloud_task_id=str(body.get('cloud_task_id') or '')[:128]))
        return jsonify(message='待核实任务已恢复，后台将重新调度')

    @app.get('/admin/api/projects')
    def projects():
        offset=page()
        status=request.args.get('status','').strip()
        payment=request.args.get('payment','').strip()
        q=request.args.get('q','').strip()
        if status and status not in PROJECT_STATUSES: raise ValueError('项目状态无效')
        if payment and payment not in ('allinpay','dev-bypass'): raise ValueError('支付方式无效')
        if len(q)>100: raise ValueError('搜索内容过长')
        where=[]; params=dict(offset=offset)
        if status:
            where.append('status=:status'); params['status']=status
        if q:
            where.append('(id LIKE :q OR owner_id LIKE :q OR order_no LIKE :q OR payload LIKE :q)')
            params['q']='%'+q+'%'
        if payment:
            where.append("JSON_UNQUOTE(JSON_EXTRACT(payload,'$.payment_mode'))=:payment"
                         if store.engine.dialect.name=='mysql'
                         else "json_extract(payload,'$.payment_mode')=:payment")
            params['payment']=payment
        clause=(' WHERE '+' AND '.join(where)) if where else ''
        with store.engine.connect() as db:
            total=db.execute(text('SELECT COUNT(*) FROM phase2_projects'+clause),params).scalar()
            rows=list(db.execute(text(
                'SELECT payload FROM phase2_projects'+clause+
                ' ORDER BY COALESCE(paid_at,0) DESC,id DESC LIMIT 30 OFFSET :offset'
            ),params).mappings())
            items=[]
            for row in rows:
                try: items.append(project_summary(json.loads(row['payload'])))
                except (TypeError,ValueError): continue
        return jsonify(items=items,total=total,
                       next_offset=offset+30 if offset+30<total else None)

    @app.get('/admin/api/transactions')
    def transactions():
        offset=page()
        bucket=request.args.get('bucket','').strip()
        payment=request.args.get('payment','').strip()
        q=request.args.get('q','').strip()
        action_only=request.args.get('action')=='1'
        try:
            hours=int(request.args.get('hours',0))
        except ValueError:
            raise ValueError('时间范围无效') from None
        allowed={'','paid','processing','completed','failed','refund_pending','refund_failed','refunded','pending_payment'}
        if bucket not in allowed: raise ValueError('交易状态无效')
        if payment and payment not in ('allinpay','dev-bypass'): raise ValueError('支付方式无效')
        if hours and hours not in (24,168,720,2160): raise ValueError('时间范围无效')
        if len(q)>100: raise ValueError('搜索内容过长')
        mysql=store.engine.dialect.name=='mysql'
        amount_paid=("CAST(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(p.payload,'$.amount_paid')),0) AS DECIMAL(20,2))"
                     if mysql else "CAST(COALESCE(json_extract(p.payload,'$.amount_paid'),0) AS REAL)")
        where=["(p.order_no IS NOT NULL OR r.refund_no IS NOT NULL)"]
        params=dict(offset=offset)
        if q:
            where.append("(p.id LIKE :q OR p.owner_id LIKE :q OR p.order_no LIKE :q OR p.payload LIKE :q "
                         "OR COALESCE(r.refund_no,'') LIKE :q OR COALESCE(r.provider_order_no,'') LIKE :q "
                         "OR COALESCE(r.provider_trxid,'') LIKE :q)")
            params['q']='%'+q+'%'
        if payment:
            where.append(("JSON_UNQUOTE(JSON_EXTRACT(p.payload,'$.payment_mode'))=:payment"
                          if mysql else "json_extract(p.payload,'$.payment_mode')=:payment"))
            params['payment']=payment
        if hours:
            where.append('p.paid_at>=:cutoff')
            params['cutoff']=time.time()-hours*3600
        buckets={
            'pending_payment':"p.status='pending_payment'",
            'paid':"p.order_no IS NOT NULL AND p.status NOT IN ('pending_payment','refunded')",
            'processing':"p.status IN ('portrait_submit','portrait_unknown','portrait_processing','portrait_saving',"
                         "'portrait_retry','video_submit','video_unknown','video_processing','video_retry','needs_review')",
            'completed':"p.status IN ('completed','archived')",
            'failed':"p.status='terminated'",
            'refund_pending':"(p.status='refund_pending' OR r.status IN ('pending','submitting','processing','needs_review'))",
            'refund_failed':"(p.status='refund_failed' OR r.status='failed')",
            'refunded':"(p.status='refunded' OR r.status='succeeded')",
        }
        if bucket: where.append(buckets[bucket])
        if action_only:
            where.append("(p.status='refund_failed' OR r.status IN ('failed','needs_review'))")
        clause=' WHERE '+' AND '.join(where)
        with store.engine.connect() as db:
            total=db.execute(text(
                'SELECT COUNT(*) FROM phase2_projects p '
                'LEFT JOIN phase2_refunds r ON r.project_id=p.id'+clause),params).scalar()
            rows=list(db.execute(text(
                "SELECT p.id AS project_id,p.owner_id,p.status,p.order_no,p.paid_at,p.payload,"
                "r.refund_no,r.status AS refund_record_status,r.amount_fen AS refund_amount_fen,"
                "r.provider_order_no,r.provider_trxid,r.provider_trxstatus,r.error_message,"
                "r.requested_at,r.updated_at AS refund_updated_at,r.finished_at AS refund_finished_at,r.operator "
                "FROM phase2_projects p LEFT JOIN phase2_refunds r ON r.project_id=p.id"
                +clause+" ORDER BY COALESCE(p.paid_at,0) DESC,p.id DESC LIMIT 50 OFFSET :offset"
            ),params).mappings())
            items=[]
            for row in rows:
                try: item=project_summary(json.loads(row['payload']))
                except (TypeError,ValueError): continue
                if row['refund_no']:
                    item['refund_no']=row['refund_no']
                    item['refund_status']=row['refund_record_status'] or item.get('refund_status','')
                    item['refund']={key:row[key] for key in
                        ('refund_no','refund_record_status','refund_amount_fen','provider_order_no',
                         'provider_trxid','provider_trxstatus','error_message','requested_at',
                         'refund_updated_at','refund_finished_at','operator')}
                else:
                    item['refund']=None
                item['needs_action']=bool(
                    item.get('status')=='refund_failed' or
                    (item.get('refund') or {}).get('refund_record_status') in ('failed','needs_review'))
                items.append(item)

            status_counts={r[0]:r[1] for r in db.execute(text(
                "SELECT p.status,COUNT(*) FROM phase2_projects p "
                "LEFT JOIN phase2_refunds r ON r.project_id=p.id "
                "WHERE p.order_no IS NOT NULL OR r.refund_no IS NOT NULL GROUP BY p.status"))}
            refund_counts={r[0]:dict(count=r[1],amount_fen=r[2] or 0) for r in db.execute(text(
                'SELECT status,COUNT(*),COALESCE(SUM(amount_fen),0) FROM phase2_refunds GROUP BY status'))}
            if inspect(db).has_table('phase2_orders'):
                paid_orders,revenue=db.execute(text(
                    'SELECT COUNT(*),COALESCE(SUM(amount),0) FROM phase2_orders '
                    'WHERE paid_at>0')).first()
                revenue_24h=db.execute(text(
                    'SELECT COALESCE(SUM(amount),0) FROM phase2_orders WHERE paid_at>=:cutoff'),
                    dict(cutoff=time.time()-86400)).scalar()
            else:
                paid_orders=db.execute(text(
                    "SELECT COUNT(*) FROM phase2_projects WHERE order_no IS NOT NULL")).scalar()
                revenue=db.execute(text(
                    'SELECT COALESCE(SUM('+amount_paid+'),0) FROM phase2_projects p')).scalar()
                revenue_24h=db.execute(text(
                    'SELECT COALESCE(SUM('+amount_paid+'),0) FROM phase2_projects p WHERE p.paid_at>=:cutoff'),
                    dict(cutoff=time.time()-86400)).scalar()
            action_count=db.execute(text(
                "SELECT COUNT(*) FROM phase2_projects p LEFT JOIN phase2_refunds r ON r.project_id=p.id "
                "WHERE p.status='refund_failed' OR r.status IN ('failed','needs_review')")).scalar()
        return jsonify(items=items,total=total,
                       next_offset=offset+50 if offset+50<total else None,
                       summary=dict(paid_orders=paid_orders,revenue_fen=revenue,
                                    revenue_24h_fen=revenue_24h,status_counts=status_counts,
                                    refund_counts=refund_counts,needs_action=action_count))

    @app.get('/admin/api/projects/<pid>')
    def project_detail(pid):
        with store.engine.connect() as db:
            row=db.execute(text('SELECT payload FROM phase2_projects WHERE id=:id'),
                           dict(id=pid)).first()
            if not row: raise ValueError('项目不存在')
            project=json.loads(row[0]); result=project_summary(project)
            selected=project.get('selected_person_uid','')
            single=project.get('participation_mode')=='single'
            result['roles']=[dict(character_id=r.get('character_id'),
                                  display_name=r.get('display_name'),
                                  person_uid=r.get('person_uid'),
                                  source='template' if single and r.get('person_uid')!=selected
                                         else 'user_face',
                                  check_status=r.get('check_status'))
                             for r in (project.get('roles') or [])]
            refund=db.execute(text(
                'SELECT refund_no,status,amount_fen,provider_order_no,provider_trxid,'
                'provider_trxstatus,error_message,requested_at,finished_at,operator '
                'FROM phase2_refunds WHERE project_id=:id'),dict(id=pid)).mappings().first()
            result['refund']=dict(refund) if refund else None
            task_ids=[]; runtime=project.get('runtime_code') or ''
            if inspect(db).has_table('phase2_tasks') and runtime:
                for task_id,kind,payload in db.execute(text(
                        'SELECT id,kind,payload FROM phase2_tasks WHERE run_id LIKE :run '
                        'OR payload LIKE :runtime ORDER BY id DESC LIMIT 20'),
                        dict(run=pid+'%',runtime='%'+runtime+'%')):
                    try: task_ids.append(task_summary(task_id,kind,json.loads(payload)))
                    except (TypeError,ValueError): pass
            result['tasks']=task_ids
        return jsonify(result)

    @app.get('/admin/api/refunds')
    def refunds():
        offset = page()
        status = request.args.get('status', '').strip()
        q = request.args.get('q', '').strip()
        allowed = {'pending','submitting','processing','needs_review','succeeded','failed'}
        if status and status not in allowed:
            raise ValueError('退款状态无效')
        if len(q)>100: raise ValueError('搜索内容过长')
        where=[]; params = dict(status=status, offset=offset)
        if status: where.append('status=:status')
        if q:
            where.append('(refund_no LIKE :q OR project_id LIKE :q OR order_no LIKE :q '
                         'OR provider_order_no LIKE :q OR provider_trxid LIKE :q)')
            params['q']='%'+q+'%'
        clause=(' WHERE '+' AND '.join(where)) if where else ''
        with store.engine.connect() as db:
            total = db.execute(text('SELECT COUNT(*) FROM phase2_refunds' + clause), params).scalar()
            rows = db.execute(text(
                'SELECT refund_no,project_id,order_no,amount_fen,status,attempts,next_run,'
                'provider_order_no,provider_trxid,provider_trxstatus,error_code,error_message,'
                'requested_at,updated_at,finished_at,operator FROM phase2_refunds'
                + clause + ' ORDER BY requested_at DESC,refund_no LIMIT 50 OFFSET :offset'
            ), params).mappings()
            items = [dict(row) for row in rows]
            ids=[item['project_id'] for item in items]
            projects={}
            if ids:
                from sqlalchemy import bindparam
                for pid,payload in db.execute(text(
                        'SELECT id,payload FROM phase2_projects WHERE id IN :ids'
                ).bindparams(bindparam('ids',expanding=True)),dict(ids=ids)):
                    try:
                        summary=project_summary(json.loads(payload))
                        projects[pid]={k:summary[k] for k in
                                       ('template_name','owner_id','project_id',
                                        'media_cleanup','media_cleanup_error')}
                    except (TypeError,ValueError):
                        pass
            for item in items:
                item.update(projects.get(item['project_id'],{}))
        return jsonify(items=items, total=total,
                       next_offset=offset+50 if offset+50 < total else None)

    @app.patch('/admin/api/refunds/<refund_no>')
    def refund_action(refund_no):
        body = request.get_json(silent=True) or {}
        action = body.get('action')
        expected_status = str(body.get('expected_status') or '')
        if action not in ('retry', 'succeeded', 'failed'):
            raise ValueError('退款处理动作无效')
        note = str(body.get('note') or '').strip()[:2000]
        with store.engine.begin() as db:
            lock=' FOR UPDATE' if store.engine.dialect.name=='mysql' else ''
            row = db.execute(text('SELECT * FROM phase2_refunds WHERE refund_no=:no'+lock),
                             dict(no=refund_no)).mappings().first()
            if not row:
                raise ValueError('退款记录不存在')
            record = dict(row)
            if expected_status and record['status'] != expected_status:
                raise ValueError('退款状态已变化，请刷新后重试')
            allowed_transitions={
                'retry': {'needs_review','failed'},
                'succeeded': {'processing','needs_review','failed'},
                'failed': {'processing','needs_review'},
            }
            if record['status'] not in allowed_transitions[action]:
                raise ValueError('当前退款状态不允许此操作')
            project_row = db.execute(text('SELECT payload FROM phase2_projects WHERE id=:id'),
                                     dict(id=record['project_id'])).first()
            if not project_row:
                raise ValueError('退款关联项目不存在')
            project = json.loads(project_row[0])
            now = time.time()
            if action == 'retry':
                if record['status'] not in ('needs_review', 'failed'):
                    raise ValueError('当前退款状态不可重试')
                db.execute(text("UPDATE phase2_refunds SET status='pending',next_run=0,"
                                "operator=:operator,"
                                "updated_at=:now WHERE refund_no=:no"),
                           dict(operator=g.admin['sub'], now=now, no=refund_no))
                project.update(status='refund_pending', refund_status='requested',
                               title='退款处理中', description='管理员已使用原退款单号重新处理')
            else:
                db.execute(text("UPDATE phase2_refunds SET status=:status,operator=:operator,"
                                "provider_order_no=COALESCE(NULLIF(:provider_order_no,''),provider_order_no),"
                                "provider_trxid=COALESCE(NULLIF(:provider_trxid,''),provider_trxid),"
                                "error_message=COALESCE(NULLIF(:note,''),error_message),"
                                "updated_at=:now,finished_at=:now WHERE refund_no=:no"),
                           dict(status=action, operator=g.admin['sub'],
                                provider_order_no=str(body.get('provider_order_no') or '')[:64],
                                provider_trxid=str(body.get('provider_trxid') or '')[:128],
                                note=note, now=now, no=refund_no))
                if action == 'succeeded':
                    project.update(status='refunded', refund_status='succeeded',
                                   title='退款成功，作品已归档',
                                   description='全额退款已完成，作品已停止播放、保存和分享',
                                   refunded_at=now, next_run_at=0,
                                   media_cleanup='pending', media_cleanup_attempts=0,
                                   media_cleanup_error='')
                else:
                    project.update(status='refund_failed', refund_status='failed',
                                   title='退款失败', description='退款未完成，请联系客服处理',
                                   next_run_at=0)
            db.execute(text('UPDATE phase2_projects SET status=:status,runnable=0,payload=:payload,'
                            'due=0 WHERE id=:id'),
                       dict(status=project['status'],
                            payload=json.dumps(transform(project, True), ensure_ascii=False),
                            id=record['project_id']))
            store.audit(db, g.admin['sub'], '处理退款', refund_no,
                        dict(action=action, note=note,
                             before_status=record['status'],
                             before_error=record.get('error_message') or ''))
        return jsonify(message='退款处理结果已保存' if action != 'retry' else '退款已进入原单号重试')

    @app.get('/admin/api/logs')
    def logs():
        kind=request.args.get('kind','operations'); offset=page()
        if kind not in ('progress','operations','tasks','program'):
            raise ValueError('日志类别无效')
        q=request.args.get('q','').strip()
        if len(q)>100: raise ValueError('搜索内容过长')
        try:
            hours=int(request.args.get('hours',0))
        except ValueError:
            raise ValueError('时间范围无效') from None
        if hours and hours not in (24,168,720,2160):
            raise ValueError('时间范围无效')
        cutoff=time.time()-hours*3600 if hours else 0
        if kind=='program':
            with store.engine.connect() as db:
                sources=[]
                if inspect(db).has_table('phase2_events'):
                    sources.append("SELECT id,'作品进度' AS source,project_id AS subject,kind AS category,'' AS status,title AS message,created FROM phase2_events")
                if inspect(db).has_table('phase2_task_events'):
                    sources.append("SELECT id,'制作任务' AS source,task_id AS subject,kind AS category,status,message,created FROM phase2_task_events")
                if not sources:
                    return jsonify(items=[],total=0,next_offset=None)
                where=[]; params=dict(offset=offset,cutoff=cutoff)
                if cutoff:
                    where.append('created>=:cutoff')
                if q:
                    where.append('(subject LIKE :q OR category LIKE :q OR status LIKE :q OR message LIKE :q)')
                    params['q']='%'+q+'%'
                clause=(' WHERE '+' AND '.join(where)) if where else ''
                union='('+' UNION ALL '.join(sources)+')'
                total=db.execute(text('SELECT COUNT(*) FROM '+union+' logs'+clause),params).scalar()
                rows=db.execute(text('SELECT * FROM '+union+' logs'+clause+
                                     ' ORDER BY created DESC,id DESC LIMIT 50 OFFSET :offset'),params).mappings()
                items=[dict(row) for row in rows]
            return jsonify(items=items,total=total,next_offset=offset+50 if offset+50<total else None)
        if kind=='progress':
            with store.engine.connect() as db:
                if not inspect(db).has_table('phase2_events'):
                    return jsonify(items=[],total=0,next_offset=None)
                clause=' WHERE created>=:cutoff' if cutoff else ''
                params=dict(offset=offset,cutoff=cutoff)
                total=db.execute(text('SELECT COUNT(*) FROM phase2_events'+clause),params).scalar()
                rows=db.execute(text('SELECT project_id,kind,title,created FROM phase2_events'+clause+
                                     ' ORDER BY created DESC,id LIMIT 50 OFFSET :offset'),params).mappings()
                items=[dict(r) for r in rows]
            return jsonify(items=items,total=total,next_offset=offset+50 if offset+50<total else None)
        table='phase2_admin_audit' if kind=='operations' else 'phase2_task_events'
        with store.engine.connect() as db:
            where=[]; params=dict(offset=offset,cutoff=cutoff)
            if cutoff: where.append('created>=:cutoff')
            if q:
                if kind=='operations':
                    where.append('(actor LIKE :q OR action LIKE :q OR target LIKE :q OR detail LIKE :q)')
                else:
                    where.append('(task_id LIKE :q OR kind LIKE :q OR status LIKE :q OR message LIKE :q)')
                params['q']='%'+q+'%'
            clause=(' WHERE '+' AND '.join(where)) if where else ''
            total=db.execute(text('SELECT COUNT(*) FROM '+table+clause),params).scalar()
            items=[dict(r) for r in db.execute(text('SELECT * FROM '+table+clause+
                                                    ' ORDER BY created DESC,id LIMIT 50 OFFSET :offset'),params).mappings()]
        return jsonify(items=items,total=total,next_offset=offset+50 if offset+50<total else None)

    @app.get('/admin/api/service-logs')
    def service_logs():
        category=request.args.get('category','').strip()
        service=request.args.get('service','').strip()
        level=request.args.get('level','').strip()
        q=request.args.get('q','').strip()
        try:
            hours=int(request.args.get('hours',0))
        except ValueError:
            raise ValueError('时间范围无效') from None
        if hours and hours not in (24,168,720,2160):
            raise ValueError('时间范围无效')
        if len(q)>100: raise ValueError('搜索内容过长')
        with store.engine.connect() as db:
            if not inspect(db).has_table('phase2_service_logs'):
                return jsonify(items=[],total=0,next_offset=None)
            offset=page()
            where=[]; params=dict(offset=offset)
            if category:
                where.append('category=:category'); params['category']=category
            if service:
                where.append('service=:service'); params['service']=service
            if level:
                where.append('level=:level'); params['level']=level
            if q:
                where.append(
                    '(message LIKE :q OR context LIKE :q OR request_id LIKE :q '
                    'OR project_id LIKE :q OR task_id LIKE :q OR error_type LIKE :q)')
                params['q']='%'+q+'%'
            if hours:
                where.append('created>=:cutoff'); params['cutoff']=time.time()-hours*3600
            clause=(' WHERE '+' AND '.join(where)) if where else ''
            total=db.execute(text('SELECT COUNT(*) FROM phase2_service_logs'+clause),params).scalar()
            rows=db.execute(text(
                                 'SELECT id,service,category,level,message,context,created,'
                                 'request_id,project_id,task_id,source_ip,method,path,'
                                 'status_code,duration_ms,error_type FROM phase2_service_logs'
                                 +clause+' ORDER BY created DESC,id DESC LIMIT 50 OFFSET :offset'),params).mappings()
            items=[]
            for row in rows:
                item=dict(row)
                item['message']=redact_log_text(item.get('message'))
                item['context']=redact_log_text(item.get('context'))
                items.append(item)
        return jsonify(items=items,total=total,next_offset=offset+50 if offset+50<total else None)

    @app.get('/admin/api/overview')
    def overview():
        with store.engine.connect() as db:
            templates=db.execute(text('SELECT COUNT(*),COALESCE(SUM(status),0) FROM phase2_templates')).first()
            actual=db.execute(text('SELECT COUNT(*) FROM phase2_template_selections')).scalar()
            baseline=db.execute(text('SELECT COALESCE(SUM(heat_base),0) FROM phase2_template_settings')).scalar()
            task_counts={r[0]:r[1] for r in db.execute(text('SELECT status,COUNT(*) FROM phase2_tasks GROUP BY status'))} if inspect(db).has_table('phase2_tasks') else {}
            today=db.execute(text('SELECT COUNT(*) FROM phase2_template_selections WHERE created>=:today'),dict(today=time.time()-86400)).scalar()
            queue={r[0]:dict(count=r[1],oldest_age_seconds=max(0,int(time.time()-float(r[2] or time.time())))) for r in db.execute(text("SELECT status,COUNT(*),MIN(CAST(JSON_EXTRACT(payload,'$.created_at') AS DECIMAL(20,3))) FROM phase2_tasks WHERE status IN ('pending','running','recovering','needs_review') GROUP BY status"))}
            refund_queue={r[0]:dict(count=r[1],oldest_age_seconds=max(0,int(time.time()-float(r[2] or time.time()))))
                          for r in db.execute(text("SELECT status,COUNT(*),MIN(requested_at) FROM phase2_refunds "
                                                   "WHERE status IN ('pending','submitting','processing','needs_review','failed') "
                                                   "GROUP BY status"))}
            has_projects=inspect(db).has_table('phase2_projects')
            project_counts={r[0]:r[1] for r in db.execute(text(
                'SELECT status,COUNT(*) FROM phase2_projects GROUP BY status'))} if has_projects else {}
            paid_not_started=db.execute(text(
                "SELECT COUNT(*) FROM phase2_projects WHERE order_no IS NOT NULL "
                "AND status IN ('portrait_submit','portrait_unknown')")).scalar() if has_projects else 0
            quota=None
            if inspect(db).has_table('phase2_free_daily'):
                import time as _time
                _day=_time.strftime('%Y-%m-%d')
                row=db.execute(text('SELECT total,used FROM phase2_free_daily WHERE day=:day'),
                               dict(day=_day)).first()
                if row:
                    quota=dict(day=_day,total=int(row[0]),used=int(row[1]),
                               remaining=max(0,int(row[0])-int(row[1])))
        disk=shutil.disk_usage(assets)
        return jsonify(templates=templates[0],published=templates[1],real_selections=actual,display_baseline=baseline,
            selections_24h=today,tasks=task_counts,queue=queue,refunds=refund_queue,
            projects=project_counts,paid_not_started=paid_not_started,free_quota=quota,
            disk=dict(total=disk.total,used=disk.used,free=disk.free))

    @app.get('/admin/api/free-quota')
    def free_quota_status():
        import time as _time
        day=_time.strftime('%Y-%m-%d')
        with store.engine.connect() as db:
            settings=None
            if inspect(db).has_table('phase2_free_settings'):
                row=db.execute(text('SELECT total FROM phase2_free_settings WHERE id=1')).first()
                if row is not None:
                    settings=int(row[0])
            used=0
            if inspect(db).has_table('phase2_free_daily'):
                row=db.execute(text('SELECT used FROM phase2_free_daily WHERE day=:day'),dict(day=day)).first()
                if row is not None:
                    used=int(row[0])
        env_total=0
        try:
            env_total=max(0,int((os.getenv('PHASE2_FREE_QUOTA_TOTAL') or '0').strip() or 0))
        except ValueError:
            env_total=0
        total=settings if settings is not None else env_total
        return jsonify(day=day,total=total,used=used,remaining=max(0,total-used),
                       source='settings' if settings is not None else 'env')

    @app.patch('/admin/api/free-quota')
    def update_free_quota():
        body=request.get_json(silent=True) or {}
        total=body.get('total')
        if isinstance(total,bool) or not isinstance(total,int) or not 0<=total<=100000:
            raise ValueError('每日免费名额需为 0 至 100000 的整数')
        import time as _time
        now=_time.time(); day=_time.strftime('%Y-%m-%d')
        with store.engine.begin() as db:
            if inspect(db).has_table('phase2_free_settings'):
                if db.execute(text('SELECT 1 FROM phase2_free_settings WHERE id=1')).first():
                    db.execute(text('UPDATE phase2_free_settings SET total=:t,updated_at=:n,updated_by=:a WHERE id=1'),
                               dict(t=total,n=now,a=g.admin['sub']))
                else:
                    db.execute(text('INSERT INTO phase2_free_settings(id,total,updated_at,updated_by) VALUES(1,:t,:n,:a)'),
                               dict(t=total,n=now,a=g.admin['sub']))
            if inspect(db).has_table('phase2_free_daily'):
                db.execute(text('UPDATE phase2_free_daily SET total=:t WHERE day=:day'),dict(t=total,day=day))
            store.audit(db,g.admin['sub'],'设置每日免费名额','free-quota',dict(total=total))
        return jsonify(message=('已关闭免费名额活动' if total==0 else '今日免费名额已设为 %d 个' % total),total=total)

    @app.get('/admin/api/monitor')
    def monitor():
        checks={'主节点入口':os.getenv('ADMIN_MAIN_URL','http://127.0.0.1:10020')+'/api/v2/health',
            '阿里云人脸服务':os.getenv('PERSONATTR_SERVICE_URL','http://127.0.0.1:10002')+'/api/v1/personattr/health',
            '定妆照制作服务':os.getenv('CHARACTERGEN_SERVICE_URL','http://127.0.0.1:10005')+'/api/v1/image/health',
            '视频制作服务':os.getenv('SEEDDANCE_SERVICE_URL','http://127.0.0.1:10006')+'/api/v1/health',
            '文件服务':os.getenv('FILE_SERVICE_URL','http://file:10004')+'/api/v1/file/health',
            'TOS 存储服务':os.getenv('TOS_SERVICE_URL','http://tos:10012')+'/health'}
        def check(pair):
            name,url=pair; start=time.monotonic()
            try:
                response=requests.get(url,timeout=(2,3),allow_redirects=False); result=response.json()
                ok=response.status_code==200 and (result.get('status')=='ok' or result.get('data',{}).get('status')=='ok' or result.get('service')=='local-tos')
                return dict(name=name,ok=ok,latency_ms=round((time.monotonic()-start)*1000),detail='服务可访问' if ok else '服务返回异常')
            except Exception: return dict(name=name,ok=False,latency_ms=round((time.monotonic()-start)*1000),detail='连接失败或超时')
        with ThreadPoolExecutor(max_workers=6) as pool: items=list(pool.map(check,checks.items()))
        return jsonify(items=items,checked_at=time.time(),note='此处检查服务可用性，不发起付费云生成；不代表云账号余额或配额充足。')
    return app

if __name__=='__main__':
    from common.service_app import serve
    serve(create_app(),10030)
