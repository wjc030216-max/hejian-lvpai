"""Idempotent synchronous image boundary: save the paid response before downloading."""
import base64
import hashlib
import re
import time
import uuid
from flask import jsonify, request
import requests
from common.task_journal import TaskJournal, CloudFailed
from common.media_refs import transform


def install(app, provider, upload):
    journal=TaskJournal()
    def generate():
        body=request.get_json(silent=True) or {}
        key=body.get('request_id')
        if not isinstance(key,str) or not 1<=len(key)<=200 or not body.get('user_id') or not body.get('prompt'):
            return jsonify(code=400,message='缺少图片请求编号或内容'),400
        run=hashlib.sha256(key.encode()).hexdigest()
        p,_=journal.create('image',run,dict(template_code=run,request=transform(body,True)))
        latest=journal.get(p['task_id'])
        if latest and latest['status']=='completed':
            saved=transform(latest)
            file_result=transform((saved.get('steps',{}).get('file') or {}).get('result'))
            provider_result=transform((saved.get('steps',{}).get('provider') or {}).get('result') or {})
            return jsonify(code=0,data={'image_url':file_result,
                                        'output_size':provider_result.get('size'),
                                        'task_id':p['task_id']})
        if latest and latest['status']=='failed':
            return jsonify(code=422,message='云端已明确拒绝此图片请求'),422
        if latest and latest['status']=='needs_review':
            from common import service_log
            service_log.event('image','图片任务幂等冲突，返回任务编号等待核实', level='warning',
                              context=f"task={p['task_id']}")
            return jsonify(code=409,message='图片提交待核实',task_id=p['task_id']),409
        owner=uuid.uuid4().hex
        p=journal.claim(p['task_id'],owner)
        if not p:
            return jsonify(code=503,message='原图片任务仍在处理，请稍后查询'),503
        try:
            try:
                saved=transform(p['request'])
                raw=journal.step(p,'provider',lambda:provider(saved['prompt'],saved.get('reference_image_urls'),
                    aspect_ratio=saved.get('aspect_ratio'),download=False),True)
                def store_image():
                    result=requests.get(raw['provider_url'],timeout=(10,120))
                    result.raise_for_status()
                    return transform(upload(saved['user_id'],base64.b64encode(result.content).decode()),True)
                url=transform(journal.step(p,'file',store_image))
                p.update(status='completed',result=transform(url,True)); journal.put(p)
                return jsonify(code=0,data={'image_url':url,'output_size':raw['size'],'task_id':p['task_id']})
            except CloudFailed as error:
                p['steps']['provider']={'status':'failed'}
                p.update(status='failed',error=str(error)); journal.put(p)
                return jsonify(code=422,message=str(error) or '云端已明确拒绝此图片请求'),422
            except ValueError as error:
                # Validation errors are raised before the billable provider
                # request. They must not be treated as an uncertain submission.
                p['steps']['provider']={'status':'failed','error':str(error)}
                p.update(status='failed',error=str(error),finished_at=time.time())
                journal.put(p)
                return jsonify(code=422,message=str(error) or '图片生成资料无效'),422
            except Exception as error:
                unknown=p['steps'].get('provider',{}).get('status')=='submitting'
                detail=str(error) if isinstance(error,ValueError) else ''
                p.update(status='needs_review' if unknown else 'recovering',
                         error=detail or ('图片提交待核实' if unknown else '原图保存中，请稍后恢复'))
                journal.put(p)
                app.logger.exception('Image task=%s state=%s error_type=%s',p['task_id'],p['status'],type(error).__name__)
                return jsonify(code=409 if unknown else 503,message='图片提交待核实' if unknown else '原图保存中，请稍后恢复'),409 if unknown else 503
        finally:
            journal.release(p['task_id'],owner)
    def task_state(task_id):
        if not re.fullmatch(r'[0-9a-f]{32}', task_id or ''):
            return jsonify(code=404,message='任务不存在'),404
        record=journal.get(task_id)
        if not record or record.get('type')!='image':
            return jsonify(code=404,message='任务不存在'),404
        saved=transform(record)
        steps=saved.get('steps') or {}
        provider_result=transform((steps.get('provider') or {}).get('result') or {})
        return jsonify(code=0,data={'task_id':task_id,'status':saved.get('status') or '',
                                    'error':saved.get('error') or '',
                                    'image_url':transform((steps.get('file') or {}).get('result') or ''),
                                    'output_size':provider_result.get('size')})

    app.add_url_rule('/api/v1/images/task/<task_id>','image_task_state',task_state)
    app.view_functions['generate_image']=generate
