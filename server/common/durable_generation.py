"""Restartable phase-two workers; paid submits and repeatable post-processing are distinct."""
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import jsonify, request
from common.task_journal import TaskJournal, UnknownSubmission, CloudFailed, TaskPending
from common.media_refs import transform
from common.video_references import build_references, build_prompt
from common import service_log


PORTRAIT_FIXED = (
    "根据各角色的人脸参考图和造型参考图最大化保持各个角色的脸型、五官、身高、体型比例等等人脸长相和外观特点、"
    "尊重真实性不要过度美化各个角色的人脸长相、同时根据各个照片主体人物的人脸长相特征调整定妆照中各人物的身高、"
    "比例和体型使其与人脸长相自然适配不违和、根据上传的角色照片、角色服装造型配饰和场景图，"
    "遵循以下的画面内容将角色融入场景中，创建一张绝美的电影级艺术定妆照。"
)


VIDEO_FIXED = (
    "创建多镜头剪辑视频，风格保持ARRI Alexa摄影机拍摄，RAW原图质感，8K超写实真人电影级画面质感，"
    "真人皮肤质感纹理，真实毛孔肌理，真实物理光照系统；"
    "视频不允许出现任何字幕、不允许出现任何背景音乐和任何配乐。"
)


def _legacy_portrait_model(values):
    costumes, scenes, defaults, prompt, ids, portrait_prompt = values
    if isinstance(scenes, str):
        scenes = [scenes]
    return {
        "ids": list(ids),
        "groups": [{
            "code": "A",
            "total_seconds": 15,
            "prompt": prompt,
            "storyboard_url": "",
            "actor_url": list(defaults or []),
            "costume_url": list(costumes or []),
        }],
        "scene_urls": list(scenes or []),
        "bgm_url": "",
        "portrait_prompt": portrait_prompt,
        "portrait_group": "A",
        "duration_seconds": 15,
    }


def _legacy_video_model(values):
    prompt, bgm, ids, defaults, costumes, scenes, storyboard = values
    if isinstance(scenes, str):
        scenes = [scenes]
    return {
        "ids": list(ids),
        "groups": [{
            "code": "A",
            "total_seconds": 15,
            "prompt": prompt,
            "storyboard_url": storyboard,
            "actor_url": list(defaults or []),
            "costume_url": list(costumes or []),
        }],
        "scene_urls": list(scenes or []),
        "bgm_url": bgm,
        "portrait_group": "A",
        "duration_seconds": 15,
    }


class Generation:
    def __init__(self, kind, api, journal):
        self.kind, self.api, self.journal = kind, api, journal
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=max(1,int(os.getenv('PHASE2_GENERATION_WORKERS','4'))))
        self.active = {}

    def tick(self, task_id):
        owner = uuid.uuid4().hex
        p = self.journal.claim(task_id, owner)
        if not p:
            return
        try:
            if p.get('deadline_at', float('inf')) <= time.time():
                waiting = any(step.get('status') == 'submitting'
                              for step in p.get('steps', {}).values())
                status = 'needs_review' if waiting else 'failed'
                p.update(status=status, cloud_failed=False,
                         error='任务超过处理时限，已停止自动恢复',
                         finished_at=time.time(), next_run=0)
                self.journal.put(p)
                return
            if p['status']!='running':
                p['status']='running'
                self.journal.put(p)
            try:
                if self.kind == 'video':
                    self.video(p)
                else:
                    self.portrait(p)
                p.update(status='completed',error='',finished_at=time.time(),next_run=0)
                self.journal.put(p)
            except TaskPending as pending:
                p.update(status='running', next_run=time.time()+pending.seconds, error='')
                self.journal.put(p)
            except CloudFailed as error:
                from common.error_detail import redact
                p['last_error_detail']=redact(error,500)
                service_log.event('task','云端明确失败', level='warning',
                                  context=f"task={task_id} detail={redact(error,400)}")
                p.update(status='failed',cloud_failed=True,error=str(error) or '云端已确认生成失败',
                         failed_group=getattr(error,'group_code',''),finished_at=time.time())
                self.journal.put(p)
            except ValueError as error:
                # Template/reference validation failures are deterministic and
                # will never succeed by retrying the same task in place; mark
                # terminal so the operator-facing flow can offer retry/refund.
                p.update(status='failed',cloud_failed=False,error=str(error) or '制作资料无效',finished_at=time.time())
                self.journal.put(p)
            except Exception as error:
                # A synchronous response may be lost even though a paid request succeeded.
                unknown = isinstance(error,UnknownSubmission) or any(s.get('status')=='submitting' for s in p['steps'].values())
                status_code=(getattr(error,'status_code',None)
                             or getattr(getattr(error,'response',None),'status_code',None))
                from common.error_detail import redact
                p['last_error_type']=type(error).__name__
                p['last_error_detail']=redact(error, 500)
                service_log.event('task','任务执行异常', level='warning',
                                  context=f"task={task_id} type={type(error).__name__} status={status_code or '-'} state={p.get('status')} detail={redact(error, 300)}")
                retries = p.get('recovery_count',0)+1
                limit = max(1, int(os.getenv('PHASE2_TASK_MAX_RECOVERIES', '8')))
                # Domain validation errors carry safe, user-readable text; upstream
                # SDK/network errors may embed model names, so keep those generic.
                detail = str(error) if isinstance(error, ValueError) else ''
                if retries >= limit:
                    p.update(status='needs_review', recovery_count=retries,
                             error='多次重试仍未成功，需要人工核实',
                             next_run=0, finished_at=time.time())
                else:
                    p.update(status='needs_review' if unknown else 'recovering',recovery_count=retries,
                             error=detail or ('云端提交结果待核实，未自动重发' if unknown else '正在恢复原任务或保存作品'),
                             next_run=time.time()+min(300,5*2**min(retries,6)))
                self.journal.put(p)
                self.api.logger.exception('Task recovery task=%s state=%s error_type=%s',task_id,p['status'],type(error).__name__)
        finally:
            self.journal.release(task_id, owner)

    def step(self,p,name,operation,billable=False):
        return transform(self.journal.step(p,name,lambda:transform(operation(),True),billable))

    def portrait(self,p):
        a=self.api
        if hasattr(a,'fetch_template_model_by_code'):
            model=self.step(p,'template',lambda:a.fetch_template_model_by_code(p['template_code']))
        else:
            model=_legacy_portrait_model(
                self.step(p,'template',lambda:a.fetch_template_by_code(p['template_code']))
            )
        ids=model['ids']
        scene=(model.get('scene_urls') or [''])[0]
        actors=transform(p['actors'])
        mappings={}
        all_views=[]
        groups=model.get('groups') or []
        for group in groups:
            code=group['code']
            group_actors=actors
            if len(groups)>1:
                defaults=dict(zip(ids,group.get('actor_url') or []))
                group_actors=[
                    dict(actor,image=defaults[actor['character_id']])
                    if actor.get('source')=='template' and defaults.get(actor.get('character_id'))
                    else actor
                    for actor in actors
                ]
            roles=a._build_role_bindings(
                group_actors,ids,group.get('costume_url') or [],group.get('actor_url') or []
            )
            mapping=[]
            for index, role in enumerate(roles, start=1):
                actor=role['actor']; face=actor['image']; cid=role['character_id']
                template=actor.get('source')=='template'
                step_name=f"role:{code}:{cid}" if len(groups)>1 else 'role:'+cid
                makeup=(group.get('makeup') or {}).get(cid) or {}
                role_prompt=(a.step1_prompt(makeup.get('text') or '')
                             if hasattr(a,'step1_prompt') else a.PROMPT_STEP1)
                view=face if template else self.step(p,step_name,lambda face=face,role=role,cid=cid,code=code:a.call_internal_api(
                    user_id=p['wx_openid'],reference_urls=[face,role['costume_url']],prompt=role_prompt,
                    aspect_ratio='16:9',max_retries=0,
                    size=os.getenv('PHASE2_THREE_VIEW_IMAGE_SIZE','1.5K'),
                    request_id=p['task_id']+':role:'+(code+':' if len(groups)>1 else '')+cid+':'+str(p.get('attempt',1))))
                mapping.append(dict(role_index=role['role_index'],character_id=cid,display_name=actor.get('name') or cid,
                    outfit_group=code,
                    source='template' if template else 'user_face',three_view_url=view,costume_url=role['costume_url'],
                    face_index=index,three_view_index=len(ids)+index,
                    **({'template_face_url':face} if template else {'user_face_url':face})))
            mappings[code]=mapping
            all_views.extend(item['three_view_url'] for item in mapping)
            p['role_mapping_by_group']=transform(mappings,True)
            p['role_mapping']=transform(mappings.get(model.get('portrait_group')) or mapping,True)
            p['outfit_views']=transform({
                key:{item['character_id']:{'three_view_url':item['three_view_url'],'costume_url':item['costume_url']}
                     for item in values}
                for key,values in mappings.items()
            },True)
            p['three_view_list']=transform(all_views,True)
            self.journal.put(p)
        portrait_code=model.get('portrait_group') or groups[-1]['code']
        mapping=mappings[portrait_code]
        descriptions=[]
        reference_images=[]
        def _add(kind,cid,url,source,label):
            reference_images.append({'index':len(reference_images)+1,'kind':kind,
                                     'character_id':cid,'source':source,'url':url})
            descriptions.append(f'图片{len(reference_images)}为[{cid}]{label}')
        for item in mapping:
            _add('face',item['character_id'],
                 item.get('user_face_url') or item.get('template_face_url'),
                 item['source'],'人脸')
        for item in mapping:
            _add('three_view',item['character_id'],item['three_view_url'],item['source'],'人物三视图')
        _add('scene',None,scene,'template','场景参考')
        adaptive='；'.join(descriptions)+'。'
        portrait_prompt=(adaptive+'人脸特写只用于核对人物长相，不得照搬其构图或背景。'
                         +PORTRAIT_FIXED+'保持各自人脸，不得互换。'+'\\n'
                         +model.get('portrait_prompt','')+'\\n'+'采用9:16竖屏构图。')
        portrait_inputs = [
            {'character_id': item['character_id'],
             'source': item['source'],
             'url': item.get('user_face_url') or item.get('template_face_url'),
             'costume_url': item.get('costume_url')}
            for item in mapping
        ] + [{'character_id': None, 'source': 'template', 'url': scene}]
        p['portrait_process'] = {
            'prompt': portrait_prompt,
            'reference_images': transform(reference_images, True),
            # Kept for auditability: these include costume references that are
            # not part of the final portrait request.
            'inputs': transform(portrait_inputs, True),
        }
        p['result']=transform(self.step(p,'portrait',lambda:a.call_internal_api(user_id=p['wx_openid'],
            reference_urls=[item['url'] for item in reference_images],prompt=portrait_prompt,aspect_ratio='9:16',max_retries=0,
            size=os.getenv('PHASE2_PORTRAIT_IMAGE_SIZE','1.5K'),
            request_id=p['task_id']+':portrait:'+str(p.get('attempt',1)))),True)
        p['portrait_group']=portrait_code
        p['portrait_process']['outputs'] = [
            {'kind': 'three_view', 'character_id': item['character_id'], 'url': item['three_view_url']}
            for item in mapping
        ] + [{'kind': 'portrait', 'url': p['result']}]
        self.journal.put(p)

    def video(self,p):
        a=self.api
        if hasattr(a,'fetch_template_model'):
            model=self.step(p,'template',lambda:a.fetch_template_model(p['template_code']))
        else:
            model=_legacy_video_model(
                self.step(p,'template',lambda:a.fetch_template_prompt(p['template_code']))
            )
        ids=model['ids']; bgm=model.get('bgm_url') or ''; scenes=model.get('scene_urls') or []
        groups=model.get('groups') or []
        maps=transform(p.get('role_mapping_by_group') or {})
        legacy_mapping=transform(p.get('role_mapping') or [])
        one_group=len(groups)==1
        p['video_process'] = {
            'prompt': '' if one_group else [],
            'inputs': [] if one_group else {},
            'outputs': [],
            'groups': {},
            'total_segments': len(groups),
            'completed_segments': 0,
            'phase': 'preparing',
        }
        self.journal.put(p)
        for group in groups:
            code=group['code']
            mapping=maps.get(code) if isinstance(maps,dict) else None
            if not mapping:
                mapping=legacy_mapping
            plan=build_references(ids,mapping,scenes,group['storyboard_url'])
            service_log.event("video", "视频片段参考资产", context=json.dumps(
                {"group":code,"references":[
                    {"index": item["index"], "kind": item["kind"],
                     "character_id": item.get("character_id"), "url": item["url"]} for item in plan]},
                ensure_ascii=False), task_id=p['task_id'],
                project_id=p.get('project_id', ''))
            for item in plan:
                asset_step=f"asset:{code}:{item['index']}"
                active_step=f"active:{code}:{item['index']}"
                asset_id=self.step(p,asset_step,lambda item=item:a.create_asset(item['url']))
                try:
                    self.step(p,active_step,lambda asset_id=asset_id:a.wait_asset_active(asset_id))
                except CloudFailed:
                    # 素材激活失败：重新注册一次并再等一轮，仍失败才让任务失败。
                    service_log.event('asset','素材激活失败，重试一次', level='warning',
                                      context=f"group={code} index={item['index']} asset_id={asset_id}")
                    asset_id=self.step(p,asset_step+':retry',lambda item=item:a.create_asset(item['url']))
                    self.step(p,active_step+':retry',lambda asset_id=asset_id:a.wait_asset_active(asset_id))
                item['asset_id']=asset_id
            video_prompt=build_prompt(plan,ids)+VIDEO_FIXED+group['prompt']
            content=[{'type':'text','text':video_prompt}]
            content += [{'type':'image_url','image_url':{'url':'asset://'+item['asset_id']},'role':'reference_image'} for item in plan]
            submit_name=f"video_submit:{code}"
            poll_name=f"video_poll:{code}"
            if one_group:
                p['video_process']['prompt']=video_prompt
                p['video_process']['inputs']=plan
                p['reference_plan']=transform(plan,True)
            else:
                p['video_process']['prompt'].append({'group':code,'text':video_prompt})
                p['video_process']['inputs'][code]=plan
            p['video_process']['groups'][code]={
                'status':'submitting','provider_video_url':'','error':'','duration':group['total_seconds'],
            }
            if p['steps'].get(submit_name,{}).get('status')=='done':
                cloud_id=p['steps'][submit_name]['result']
            elif p['steps'].get(submit_name,{}).get('status')=='submitting':
                raise UnknownSubmission('视频提交结果待核实，已停止自动重发')
            else:
                own_inflight=sum(
                    name.startswith('video_submit:') and step.get('status') in ('submitting','done')
                    and p['steps'].get('video_poll:'+name.split(':',1)[1],{}).get('status')!='done'
                    for name,step in p['steps'].items()
                )
                with self.journal.lock('video-cloud-capacity',0) as acquired:
                    active=self.journal.video_inflight(p['task_id'])+own_inflight
                    if not acquired or active>=max(1,int(os.getenv('PHASE2_VIDEO_MAX_INFLIGHT','40'))):
                        p['video_process']['groups'][code]['status']='waiting_capacity'
                        self.journal.put(p)
                        raise TaskPending(15)
                    p['generation_parameters']=p.get('generation_parameters') or []
                    p['generation_parameters'].append(dict(group=code,ratio='9:16',duration=a.VIDEO_DURATION,
                        generate_audio=a.VIDEO_GENERATE_AUDIO,bgm_policy='mix_once_after_concat' if bgm else 'model_audio'))
                    # Reserve the paid submission while holding the global
                    # capacity lock, then release it before provider I/O.
                    p['steps'][submit_name] = {'status':'submitting','started_at':time.time()}
                    self.journal.put(p)
                cloud_id=self.journal.complete_reserved_submission(
                    p,submit_name,lambda content=content,group=group:a.call_volcano_video(
                        content=content,generate_audio=a.VIDEO_GENERATE_AUDIO,ratio='9:16',
                        duration=group['total_seconds'],watermark=a.VIDEO_WATERMARK))
            p['video_process']['groups'][code]['cloud_task_id']=cloud_id
            self.journal.put(p)

        pending=False
        completed=0
        sources=[]
        for group in groups:
            code=group['code']
            poll_name=f"video_poll:{code}"
            state=p['video_process']['groups'][code]
            try:
                source=self.step(p,poll_name,lambda code=code:a.poll_volcano_task(
                    p['video_process']['groups'][code]['cloud_task_id']))
            except TaskPending:
                state['status']='processing'
                pending=True
                self.journal.put(p)
                continue
            except CloudFailed as error:
                state.update(status='failed',error=str(error))
                setattr(error,'group_code',code)
                p['video_process']['completed_segments']=completed
                self.journal.put(p)
                raise
            state.update(status='completed',provider_video_url=source)
            if one_group:
                p['video_process']['provider_video_url']=source
            completed += 1
            sources.append(source)
            p['video_process']['completed_segments']=completed
            self.journal.put(p)
        p['video_process']['phase']='composing' if not pending else 'generating'
        self.journal.put(p)
        if pending:
            raise TaskPending(15)
        from local.results import save_local_video
        from common.storage_client import upload_result
        if len(sources)==1:
            target=self.step(p,'compose',lambda:str(save_local_video(
                sources[0],bgm,p['wx_openid'],p['task_id'],prepare_only=True)))
        else:
            from local.results import save_local_video_segments
            segment_seconds=groups[0]['total_seconds']
            target=self.step(p,'compose',lambda:str(save_local_video_segments(
                sources,bgm,p['wx_openid'],p['task_id'],segment_seconds=segment_seconds,
                prepare_only=True)))
        if not Path(target).is_file():
            p['steps'].pop('compose',None)
            self.journal.put(p)
            raise RuntimeError('本地合成文件缺失，使用原云端结果恢复')
        if os.getenv('PHASE2_PLAYBACK_OPTIMIZE', 'false').lower() == 'true':
            from local.playback import optimize_playback
            self.step(p, 'playback', lambda: optimize_playback(target))
        if (os.getenv('PHASE2_VALIDATE_FINAL_VIDEO', 'true') == 'true'
                and os.getenv('APP_ENV') in ('local-real', 'staging', 'production')):
            from local.results import validate_final_video
            expected_seconds = sum(int(group.get('total_seconds') or 15) for group in groups)
            validate_final_video(target, expected_seconds)
        p['result']=self.step(
            p, 'upload',
            lambda: upload_result(p['task_id'], target, delete_local=False))
        from common.storage_client import _delete_local_cache
        _delete_local_cache(target, p['task_id'])
        p['video_process']['outputs']=[{'kind':'video','url':p['result']}]
        p['video_process']['phase']='completed'
        self.journal.put(p)

    def start(self):
        def loop():
            while not self.stop.wait(2):
                try:
                    self.active={k:v for k,v in self.active.items() if not v.done()}
                    capacity=self.pool._max_workers-len(self.active)
                    for p in self.journal.due(self.kind,max(1,capacity)) if capacity>0 else []:
                        if p['task_id'] not in self.active:
                            self.active[p['task_id']]=self.pool.submit(self.tick,p['task_id'])
                except Exception as error:
                    self.api.logger.warning('Task scheduler unavailable: %s',type(error).__name__)
        threading.Thread(target=loop,daemon=True,name=self.kind+'-recovery').start()


def install(api,kind):
    journal=TaskJournal()
    worker=Generation(kind,api,journal)
    app=api.app
    @app.errorhandler(Exception)
    def service_error(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error,HTTPException):
            return jsonify(code=error.code,message=error.description),error.code
        api.logger.warning('Generation request failed: %s',type(error).__name__)
        code=422 if isinstance(error,ValueError) else 503
        return jsonify(code=code,message='请求资料无效' if code==422 else '制作服务暂时不可用，请查询原任务状态'),code
    def response(p):
        data=transform(p)
        result={key:data.get(key) for key in ('task_id','status','result','error','role_mapping','three_view_list','template_code','character_task_id','created_at','finished_at','type')}
        result['attempt']=data.get('attempt',1)
        video_process=data.get('video_process') or {}
        if video_process:
            result['video_progress']={
                'total':int(video_process.get('total_segments') or 0),
                'completed':int(video_process.get('completed_segments') or 0),
                'phase':video_process.get('phase') or '',
                'groups':{
                    code:{'status':item.get('status') or '', 'error':item.get('error') or ''}
                    for code,item in (video_process.get('groups') or {}).items()
                },
            }
        return result
    def generate():
        from common.local_guard import require_cloud_enabled
        require_cloud_enabled()
        body=request.get_json(silent=True) or {}
        run=body.get('wx_openid'); template=body.get('template_code')
        if not isinstance(run,str) or not 1<=len(run)<=128 or not isinstance(template,str) or not template.startswith('P2S-'):
            return jsonify(code=400,message='缺少二期制作编号或模板版本'),400
        field='actors' if kind=='character' else 'role_mapping'
        if not isinstance(body.get(field),list) or not body[field] or not all(isinstance(x,dict) for x in body[field]):
            return jsonify(code=400,message='角色资料不完整'),400
        if kind=='video' and (not body.get('character_task_id') or body.get('portrait_url') or body.get('storyboard_image')):
            return jsonify(code=400,message='视频只接收角色参考资料'),400
        p,new=journal.create(kind,run,transform({k:body[k] for k in ('template_code','actors','role_mapping','character_task_id') if k in body},True))
        return jsonify(code=0,data=response(p)),202 if new else 200
    def status(task_id):
        p=journal.get(task_id)
        return (jsonify(code=0,data=response(p)),200) if p and p['type']==kind else (jsonify(code=404,message='任务不存在'),404)
    def by_user():
        p=journal.latest(kind,request.args.get('wx_openid',''))
        return jsonify(code=0,data=dict(response(p),exists=True,authoritative=True) if p else {'exists':False,'authoritative':True})
    def retry(task_id):
        try:
            body=request.get_json(silent=True) or {}
            if 'retry_attempt' not in body:
                raise ValueError('缺少本轮重试编号')
            p=journal.retry(task_id,body.get('wx_openid'),body['retry_attempt'])
            return jsonify(code=0,data=response(p)),202
        except ValueError as error:
            return jsonify(code=409,message=str(error)),409
    def health():
        try:
            journal.due(kind,1)
            return jsonify(status='ok',task_store='mysql',redis_required=False,
                           build_revision=os.getenv('BUILD_REVISION','unknown'))
        except Exception:
            return jsonify(status='database unavailable'),503
    prefix='/api/v1/image' if kind=='character' else '/api/v1/video'
    app.add_url_rule(prefix+('/generatecharacter' if kind=='character' else '/generate'),'generate',generate,methods=['POST'])
    app.add_url_rule(prefix+'/task/<task_id>','status',status)
    app.add_url_rule(prefix+'/task/by-user','by_user',by_user)
    app.add_url_rule(prefix+'/task/<task_id>/retry','retry',retry,methods=['POST'])
    app.add_url_rule('/api/v1/image/health' if kind=='character' else '/api/v1/health','health',health)
    worker.start()
    return worker
