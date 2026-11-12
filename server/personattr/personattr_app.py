"""Phase-two face validation. No legacy character CRUD or character database."""
import os
from flask import Flask,request,jsonify
from common.aliyun_face_provider import detect_face, FaceRejected
from common.service_app import protect,serve
from common import service_log
app=Flask(__name__)
app.config['MAX_CONTENT_LENGTH']=16384
service_log.install("personattr")
protect(app,{'local-phase2'},public={'/api/v1/personattr/health'})
@app.get('/api/v1/personattr/health')
def health():
    return jsonify(status='ok',service='aliyun-face',
                   build_revision=os.getenv('BUILD_REVISION','unknown'))
@app.post('/api/v1/personattr/detect')
def detect():
    body=request.get_json(silent=True) or {}
    try:
        result=detect_face(body.get('Url'))
        service_log.event("face", "人脸检测完成", context=f"face_count={result.get('face_count')}")
        return jsonify(result)
    except FaceRejected as error:
        service_log.event("face", "人脸检测未通过", context="face_rejected:" + str(error)[:300])
        return jsonify(error=str(error)), 422
    except ValueError as error:
        service_log.event("face", "照片地址无效", context="face_detect_rejected:" + str(error)[:300])
        return jsonify(error='照片地址无效'),422
    except Exception as error:
        app.logger.warning('Face validation failed: %s', str(error)[:300])
        service_log.event("face", "人脸检测服务异常",
                          context="face_detect_error:" + type(error).__name__ + ":" + str(error)[:300])
        return jsonify(error='人脸检测暂时不可用，请稍后重试'),502
if __name__=='__main__': serve(app,10002)
