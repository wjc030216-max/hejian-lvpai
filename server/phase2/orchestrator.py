"""Restartable phase-two production. One durable checkpoint per downstream operation."""
from __future__ import annotations
import os
import uuid
import base64
import hashlib
import hmac
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
import logging

logger = logging.getLogger(__name__)
from common import service_log
from common import http_client
try:
    from .refund_service import RefundService
except ImportError:
    from refund_service import RefundService
try:
    from .retention_service import RetentionService
except ImportError:
    from retention_service import RetentionService


class FaceUnavailable(RuntimeError):
    """人脸服务暂时不可用：应稍后重试，而不是把照片判定为不合格。"""


class Phase2Orchestrator:
    def __init__(self, store, upload_root=None, share_cache=None):
        self.store = store
        self.enabled = os.getenv('PHASE2_AI_ORCHESTRATION_ENABLED', 'false').lower() == 'true'
        self.character_url = os.getenv('CHARACTERGEN_SERVICE_URL', 'http://127.0.0.1:10005').rstrip('/')
        self.video_url = os.getenv('SEEDDANCE_SERVICE_URL', 'http://127.0.0.1:10006').rstrip('/')
        self.face_url = os.getenv('PERSONATTR_SERVICE_URL', 'http://127.0.0.1:10002').rstrip('/')
        self.face_token = os.getenv('PHASE2_INTERNAL_JWT', '')
        self.owner = uuid.uuid4().hex
        self.stop = threading.Event()
        workers = max(1, int(os.getenv('PHASE2_ORCHESTRATOR_WORKERS', '128')))
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.active = {}
        self.photo_pool = ThreadPoolExecutor(max_workers=workers)
        self.photo_active = {}
        self.refund_service = RefundService(store)
        self.retention_service = (
            RetentionService(store, upload_root, share_cache)
            if upload_root is not None and share_cache is not None else None
        )
        self.last_subscription_retry = 0

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name='phase2-recovery').start()
        self.refund_service.start()
        if self.retention_service:
            self.retention_service.start()

    def _loop(self):
        while not self.stop.wait(3):
            try:
                self.active = {k:v for k,v in self.active.items() if not v.done()}
                self.photo_active = {k:v for k,v in self.photo_active.items() if not v.done()}
                for photos in (False, True):
                    pool, active = (self.photo_pool, self.photo_active) if photos else (self.pool, self.active)
                    capacity = pool._max_workers - len(active)
                    if capacity <= 0: continue
                    for p in self.store.due(capacity, photos=photos):
                        pid = p['project_id']
                        if pid not in active:
                            active[pid] = pool.submit(self.tick, pid)
                if time.time() - self.last_subscription_retry >= 30:
                    self.last_subscription_retry = time.time()
                    for project in self.store.pending_subscriptions(20):
                        self._notify_subscribed(project)
            except Exception:
                logger.exception('Phase-two scheduler failed to read or schedule records')


    @staticmethod
    def _needs_photo_check(project):
        from phase2.participation import user_roles
        return any(r.get('check_status') == 'checking' for r in user_roles(project))

    def _json(self, method, url, payload=None):
        r = http_client.session().request(method, url, json=payload if method == 'POST' else None,
                             params=payload if method == 'GET' else None, headers=self._headers(), timeout=(5,30))
        r.raise_for_status()
        b = r.json()
        if b.get('code') not in (0,200,201,202):
            raise RuntimeError('下游请求未确认，请检查服务状态')
        return b.get('data') or {}

    def tick(self, pid):
        if not self.store.claim(pid, self.owner):
            return
        error_seen = False
        p = {}
        try:
            p = self.store.get(pid)
            if not p or p.get('next_run_at', 0) > time.time():
                return
            self.store.update(pid, {'last_tick_at':time.time()})
            status = p['status']
            if status in ('draft','uploading'):
                self._photos(p)
                return
            if status == 'pending_payment':
                return
            if status in ('completed','terminated','refund_pending','refund_failed','refunded','archived','expired'):
                return
            max_runtime = max(60, int(os.getenv('PHASE2_PROJECT_MAX_SECONDS', '7200')))
            started = (p.get('submission_at') or p.get('paid_at') or p.get('created_at')
                       or time.time())
            if status in ('portrait_submit','portrait_unknown','portrait_processing',
                          'portrait_saving','portrait_retry','video_submit','video_unknown',
                          'video_processing','video_retry','needs_review') and (
                    time.time() - float(started) > max_runtime):
                self.store.review_timeout(pid, '制作超过处理时限，已停止自动恢复')
                service_log.event('orchestration', '制作超过时限',
                                  context=f'project={pid} status={status}',
                                  project_id=pid)
                return
            if not self.enabled:
                self.store.update(pid, {
                    'status':'terminated',
                    'title':'制作服务未启用',
                    'description':'AI制作服务未启用，本次未开始生成',
                    'error':'AI制作服务未启用，本次未开始生成',
                    'progress':0,
                    'next_run_at':0,
                    'terminated_at':time.time(),
                    'refund_status':('not_charged' if p.get('payment_mode') == 'dev-bypass' else 'available'),
                })
                service_log.event('orchestration', 'AI制作服务未启用，订单已停止',
                                  context=f'project={pid}', project_id=pid)
                return
            stage = p.get('stage', 'portrait')
            if status == 'portrait_saving':
                from common.storage_client import save_portrait
                aid, url = save_portrait(pid, p['character_task_id'], p['portrait_source_url'])
                self.store.portrait_saved(pid, {'portrait_url':url,'portrait_asset_id':aid,'portrait_saved':True,
                                               'portrait_version':p['character_task_id'],'progress':50,'error':''})
                return
            if status in ('portrait_submit','video_submit','portrait_retry','video_retry'):
                self._submit(p, stage, status.endswith('_retry'))
            elif status in ('portrait_unknown','video_unknown'):
                self._reconcile(p, stage)
            elif status in ('portrait_processing','video_processing','needs_review'):
                self._poll_once(p, stage)
        except ValueError as error:
            error_seen = True
            logger.exception('Phase-two task data rejected: project=%s', pid)
            self.store.failed_attempt(pid, str(error) or '制作资料无效')
        except Exception:
            error_seen = True
            failures = int(p.get('consecutive_tick_errors') or 0) + 1
            limit = max(1, int(os.getenv('PHASE2_PROJECT_MAX_CONSECUTIVE_ERRORS', '20')))
            logger.exception('Phase-two task checkpoint failed: project=%s', pid)
            if failures >= limit:
                self.store.review_timeout(
                    pid, '制作服务连续查询失败，已停止自动恢复，请重新生成或申请退款')
            else:
                delay = min(300, 15 * (2 ** min(failures - 1, 4)))
                self.store.update(pid, {
                    'consecutive_tick_errors': failures,
                    'description': '制作服务暂时异常，正在退避重试',
                    'next_run_at': time.time() + delay,
                })
        finally:
            if not error_seen:
                try:
                    current = self.store.get(pid)
                    if current and current.get('consecutive_tick_errors'):
                        self.store.update(pid, {'consecutive_tick_errors': 0})
                except Exception:
                    logger.warning('Could not clear transient error counter project=%s', pid)
            self.store.release(pid, self.owner)

    def _photos(self, p):
        from phase2.participation import user_roles
        if self.enabled and os.getenv('APP_ENV') == 'local-real' and os.getenv('LOCAL_CLOUD_ENABLED') != 'true':
            return
        for role in user_roles(p):
            if role.get('check_status') != 'checking':
                continue
            asset = role['photo_asset_id']
            error = ''
            transient = False
            try:
                if self.enabled:
                    self._check_face(p['project_id'], role['photo_url'])
                # In mock mode only local image parsing runs, never pretend face detection happened.
            except FaceUnavailable as exc:
                transient = True
                error = str(exc)
            except Exception as exc:
                error = str(exc)
            with self.store.transaction() as db:
                current = self.store.get(p['project_id'], db)
                for r in current['roles']:
                    if r.get('photo_asset_id') == asset:
                        attempts = int(r.get('check_attempts') or 0) + 1
                        if transient and attempts < 6:
                            r.update(check_status='checking', check_message=error, check_attempts=attempts)
                            current['next_run_at'] = time.time() + 10
                        elif transient:
                            r.update(check_status='rejected',
                                     check_message=(error or '人脸检测暂时不可用') + '，已停止自动重试，请更换照片或稍后重试',
                                     check_attempts=attempts,
                                     check_mode='real' if self.enabled else 'mock')
                        else:
                            r.update(check_status='rejected' if error else 'passed', check_message=error or (
                                '照片检查通过' if self.enabled else '开发模式：仅检查图片文件'),
                                check_mode='real' if self.enabled else 'mock', check_attempts=attempts)
                self.store.put(current, db)
            return

    def _submit(self, p, stage, retry):
        pid = p['project_id']
        key = 'character_task_id' if stage == 'portrait' else 'video_task_id'
        base = self.character_url if stage == 'portrait' else self.video_url
        run_id = p.get('run_id') or pid
        if stage == 'video' and not p.get('portrait_saved'):
            raise ValueError('定妆照尚未保存')
        if retry:
            path = '/api/v1/image/task/' + p[key] + '/retry' if stage == 'portrait' else '/api/v1/video/task/' + p[key] + '/retry'
            payload = {'wx_openid':run_id,'retry_attempt':p.get('attempt',1)}
        elif stage == 'portrait':
            from phase2.participation import generation_actors
            path = '/api/v1/image/generatecharacter'
            payload = {'wx_openid':run_id,'template_code':p.get('runtime_code') or p['template_code'],
                       'actors': generation_actors(p)}
        else:
            path = '/api/v1/video/generate'
            payload = {'wx_openid':run_id,'template_code':p.get('runtime_code') or p['template_code'],
                       'character_task_id':p['character_task_id'], 'role_mapping':p.get('role_mapping') or []}
        # Persist BEFORE network: on response loss, query the existing task, never blindly POST again.
        self.store.update(pid, {'status':stage+'_unknown','stage':stage,'run_id':run_id,
                               'submission_at':time.time(),'submission_retry':retry,'title':'正在制作定妆照' if stage=='portrait' else '视频生成中'})
        data = self._json('POST', base+path, payload)
        if not data.get('task_id'):
            raise ValueError('下游未返回任务编号')
        self.store.update(pid, {key:data['task_id'],'status':stage+'_processing','submission_retry':False,'next_run_at':0})
        service_log.event(
            'orchestration', '已提交云端制作',
            context=f"project={pid} stage={stage} task={data['task_id']}",
            project_id=pid, task_id=data['task_id'])

    def _reconcile(self, p, stage):
        if stage == 'portrait':
            url = self.character_url+'/api/v1/image/task/by-user'
            args = {'wx_openid':p['run_id'],'task_type':'character'}
        else:
            url = self.video_url+'/api/v1/video/task/by-user'
            args = {'wx_openid':p['run_id']}
        data = self._json('GET',url,args)
        # A failed old task after an uncertain retry is ambiguous. Do not retry or count it twice.
        retry_not_accepted = p.get('submission_retry') and data.get('status')=='failed' and (
            not data.get('authoritative') or data.get('attempt',1)<p.get('attempt',1))
        if data.get('exists') and data.get('task_id') and not retry_not_accepted:
            key = 'character_task_id' if stage=='portrait' else 'video_task_id'
            self.store.update(p['project_id'],{key:data['task_id'],'status':stage+'_processing','submission_retry':False})
        elif data.get('authoritative') and not data.get('exists') and not p.get('submission_retry'):
            # Durable service confirmed that no task was accepted; same run ID is idempotent.
            self.store.update(p['project_id'],{'status':stage+'_submit','next_run_at':time.time()+5})
        elif data.get('authoritative') and retry_not_accepted:
            self.store.update(p['project_id'],{'status':stage+'_retry','next_run_at':time.time()+5})
        else:
            self.store.update(p['project_id'],{'title':'制作状态待核实','description':'正在核对原任务，未重复生成或扣费','next_run_at':time.time()+30})

    def _poll_once(self, p, stage):
        key = 'character_task_id' if stage=='portrait' else 'video_task_id'
        base = self.character_url+'/api/v1/image/task/' if stage=='portrait' else self.video_url+'/api/v1/video/task/'
        data = self._json('GET',base+p[key])
        if data.get('status') == 'needs_review':
            self.store.update(p['project_id'], {'status':'needs_review','title':'制作状态待核实',
                'description':'云端提交结果尚未确认，已暂停自动重发，请联系管理员核实','next_run_at':time.time()+60})
        elif data.get('status') == 'failed':
            self.store.failed_attempt(
                p['project_id'], str(data.get('error') or '生成失败')
            )
            service_log.event(
                'orchestration', '生成失败',
                context=f"project={p['project_id']} stage={stage}",
                project_id=p['project_id'], task_id=p.get(key, ''))
        elif data.get('status') == 'completed':
            if not data.get('result'):
                self.store.update(p['project_id'],{'title':'产物正在核验','description':'生成服务尚未提供可访问结果，正在恢复'})
                return
            if stage == 'portrait':
                mapping = data.get('role_mapping') or []
                if not mapping:
                    raise ValueError('定妆照任务未返回角色对应表，禁止进入视频制作')
                self.store.update(p['project_id'],{'portrait_source_url':data['result'],'role_mapping':mapping,
                                                   'status':'portrait_saving','title':'正在保存定妆照'})
            else:
                # The video service persists to private TOS before declaring completed.
                if data.get('video_progress'):
                    self.store.update(p['project_id'], {
                        'video_progress': data['video_progress'],
                        'title': '旅行视频生成中',
                        'progress': min(95, 70 + 10 * int(data['video_progress'].get('completed') or 0)),
                    })
                self.store.complete(p['project_id'],data['result'])
                self._notify_subscribed(p)
                service_log.event(
                    'orchestration', '作品完成',
                    context=f"project={p['project_id']} stage=video",
                    project_id=p['project_id'], task_id=p.get('video_task_id', ''))
        else:
            patch={'description':'后台持续制作，可以离开或再次拍摄','next_run_at':time.time()+5}
            if data.get('video_progress'):
                patch['video_progress']=data['video_progress']
                patch['progress']=min(95, 70 + 10 * int(data['video_progress'].get('completed') or 0))
            self.store.update(p['project_id'],patch)

    def _check_face(self, user_id: str, image_url: str) -> None:
        headers = self._headers()
        try:
            response = http_client.session().post(self.face_url + "/api/v1/personattr/detect", json={"Url": image_url, "wx_openid": user_id}, headers=headers, timeout=(5, 60))
        except requests.RequestException as exc:
            raise FaceUnavailable("人脸检测服务连接失败或超时，请稍后重试") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise FaceUnavailable("人脸检测服务暂时不可用，请稍后重试") from exc
        if response.status_code in (502, 503):
            raise FaceUnavailable(body.get("error") or "人脸检测服务暂时不可用，请稍后重试")
        if response.status_code >= 400 or body.get("error"):
            raise RuntimeError(body.get("error") or "人脸检测服务暂时不可用，请稍后重试")
        if body.get("message") == "No face detected":
            raise RuntimeError("角色照片未检测到清晰人脸")
        if body.get("face_count") != 1:
            raise RuntimeError("请上传只有一人的清晰正面照片")
        if body.get("quality_passed") is False:
            raise RuntimeError("照片清晰度或遮挡检查未通过，请更换照片")
        location = body.get("location") or {}
        if not location.get("width") or not location.get("height"):
            raise RuntimeError(body.get("message") or body.get("error") or "人脸检测未返回有效结果")

    def _notify_subscribed(self, p):
        openid = p.get('subscribe_openid')
        if not openid or p.get('subscribe_sent'):
            return
        from phase2.wechat_subscribe import send_completed_notice
        try:
            ok = send_completed_notice(
                openid,
                p['project_id'],
                (p.get('template') or {}).get('video_name') or '',
                p.get('paid_at') or p.get('created_at'),
                p.get('completed_at') or time.time(),
                p.get('subscribe_template') or None,
            )
        except Exception as error:
            ok = False
            error_text = type(error).__name__
        else:
            error_text = '' if ok else '微信订阅消息发送失败'
        if ok:
            self.store.mark_subscribed(p['project_id'])
        else:
            self.store.mark_subscribe_result(p['project_id'], False, error_text)

    def _headers(self) -> dict:
        if os.getenv("APP_ENV") in ("local-real", "staging", "production"):
            # Short-lived signed token, renewed for each request (no stale pasted JWT).
            secret = os.environ["AITRAVEL_JWT_SECRET"].encode()
            def enc(value):
                return base64.urlsafe_b64encode(value).rstrip(b"=")
            header = enc(b'{"alg":"HS256","typ":"JWT"}')
            payload = enc(json.dumps({"sub": "local-phase2", "iat": int(time.time()),
                                     "exp": int(time.time()) + 300}).encode())
            message = header + b"." + payload
            signature = enc(hmac.new(secret, message, hashlib.sha256).digest())
            return {"Authorization": "Bearer " + (message + b"." + signature).decode()}
        return {"Authorization": "Bearer " + self.face_token} if self.face_token else {}

