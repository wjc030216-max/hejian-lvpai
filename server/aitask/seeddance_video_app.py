"""Phase-two video service. Task state is owned by TaskJournal."""
import logging
import os
import re
from pathlib import Path
from flask import Flask
from flask import jsonify, request
from werkzeug.utils import secure_filename
from common import video_provider as provider
from common.durable_generation import install
from common.service_app import protect,serve
from common import service_log
provider.app=Flask(__name__)
provider.logger=logging.getLogger(__name__)
app=provider.app
service_log.install("video")
protect(app,{'local-phase2'},public={'/api/v1/health'})
worker=install(provider,'video')


@app.delete('/api/v1/video/result/<task_id>')
def delete_local_result(task_id):
    user_id=request.args.get('wx_openid','')
    user=secure_filename(user_id)
    task=secure_filename(task_id)
    if not user or not task:
        return jsonify(code=400,message='制作编号无效'),400
    path=Path(os.environ['LOCAL_RESULT_ROOT'])/user/(task+'.mp4')
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return jsonify(code=500,message='本地视频清理失败'),500
    service_log.event('video', '本地合成视频已按保留期删除', context='task_id='+task)
    return '',204


@app.delete('/api/v1/video/assets/<asset_id>')
def delete_reference_asset(asset_id):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', asset_id):
        return jsonify(code=400, message='视频素材编号无效'), 400
    provider.delete_asset(asset_id)
    return '', 204
if __name__=='__main__': serve(app,10006)
