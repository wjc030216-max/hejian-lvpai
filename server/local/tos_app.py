"""Only this container receives TOS credentials. No delete/list/bucket-admin API."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from PIL import Image, UnidentifiedImageError

import subprocess
from flask import Flask, jsonify, request, send_file
import jwt
from common.local_guard import require_cloud_enabled
from local.tos_storage import StorageConfig, ResultStore
from common import service_log

ASSET_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.webp': 'image/webp', '.mp4': 'video/mp4', '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
}


def create_app(store=None, records_root=None):
    app = Flask(__name__)
    service_log.install("tos")
    app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024
    records = Path(records_root or os.getenv('TOS_RECORDS_ROOT', '/app/tos-records'))
    records.mkdir(parents=True, exist_ok=True)
    registry=None
    if os.getenv('AITRAVEL_DATABASE_URL'):
        from common.asset_registry import AssetRegistry
        registry=AssetRegistry(os.environ['AITRAVEL_DATABASE_URL'])
    app.extensions['asset_registry']=registry
    if store is None and os.getenv('LOCAL_CLOUD_ENABLED') == 'true':
        store = ResultStore(StorageConfig.from_env())

    def cleanup_expired_objects():
        retention_days = max(1, int(os.getenv('TOS_OBJECT_RETENTION_DAYS', '90')))
        interval = max(300, int(os.getenv('TOS_OBJECT_CLEANUP_INTERVAL_SECONDS', '3600')))
        while True:
            try:
                cutoff = time.time() - retention_days * 86400
                removed = 0
                for record in registry.expired_objects(cutoff) if registry else []:
                    if (record['bucket'] != store.config.bucket
                            or not record['object_key'].startswith(store.config.prefix)):
                        continue
                    try:
                        store.delete_key(record['object_key'])
                        registry.delete(record['id'], record['kind'])
                        removed += 1
                    except Exception as error:
                        service_log.event(
                            'tos', '过期 TOS 对象删除失败',
                            context=(
                                f"id={record['id']} kind={record['kind']} "
                                f"error={type(error).__name__}"),
                            level='error')
                if removed:
                    service_log.event(
                        'tos', '过期 TOS 对象已清理',
                        context=f'removed={removed} retention_days={retention_days}')
            except Exception as error:
                service_log.event(
                    'tos', 'TOS 过期对象清理调度异常',
                    context=f'error={type(error).__name__}', level='error')
            time.sleep(interval)

    if store is not None:
        threading.Thread(
            target=cleanup_expired_objects,
            daemon=True,
            name='tos-retention',
        ).start()

    @app.before_request
    def authenticate():
        if request.path == '/health':
            return None
        token = request.headers.get('Authorization', '').removeprefix('Bearer ')
        try:
            claims = jwt.decode(token, os.environ['AITRAVEL_JWT_SECRET'], algorithms=['HS256'],
                                options={'require': ['exp', 'sub']})
            allowed = {
                'local-video-storage', 'local-phase2-storage',
                'local-profile-lifecycle', 'phase2-file',
            }
            if claims['sub'] not in allowed:
                raise jwt.InvalidTokenError('Invalid storage caller')
            if request.method == 'POST':
                if request.path.startswith('/internal/assets/'):
                    expected = {'local-phase2-storage', 'phase2-file'}
                elif request.path.startswith('/internal/portraits/'):
                    expected = {'local-phase2-storage'}
                else:
                    expected = {'local-video-storage'}
                if claims['sub'] not in expected:
                    raise jwt.InvalidTokenError('Caller cannot upload this asset type')
            if request.method == 'DELETE' and claims['sub'] != 'local-profile-lifecycle':
                raise jwt.InvalidTokenError('Caller cannot delete this asset')
        except (jwt.InvalidTokenError, KeyError):
            return jsonify(error='Unauthorized'), 401
        try:
            require_cloud_enabled()
        except RuntimeError:
            return jsonify(error='Local cloud calls disabled'), 503
        if store is None:
            return jsonify(error='TOS credentials are not configured'), 503

    @app.get('/health')
    def health():
        return jsonify(service='local-tos', cloud_calls_enabled=os.getenv('LOCAL_CLOUD_ENABLED') == 'true',
                       configured=store is not None,
                       build_revision=os.getenv('BUILD_REVISION','unknown'))

    @app.post('/internal/results/<result_id>')
    def upload(result_id):
        if not re.fullmatch(r'[0-9a-f]{32}', result_id):
            return jsonify(error='Invalid result ID'), 422
        uploaded = request.files.get('file')
        if uploaded is None:
            return jsonify(error='Missing file'), 422
        # Callers cannot choose the bucket, key, ACL, endpoint or credentials.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.mp4'
            uploaded.save(path)
            with path.open('rb') as stream:
                if stream.read(12)[4:8] != b'ftyp':
                    return jsonify(error='Expected an MP4 result'), 422
            key = store.put(result_id, path)
            if registry: registry.save(result_id,'video',store.config.bucket,key,path)
        record = {'bucket': store.config.bucket, 'key': key}
        target = records / (result_id + '.json')
        with tempfile.NamedTemporaryFile(mode='w',dir=records,delete=False,encoding='utf-8') as output:
            json.dump(record,output); temporary=Path(output.name)
        temporary.replace(target)
        service_log.event("tos", "视频已上传 TOS", context=f"result_id={result_id}")
        return jsonify(result_id=result_id), 201

    @app.post('/internal/assets/<asset_id>')
    def upload_asset(asset_id):
        if not re.fullmatch(r'(?:asset|file)_[0-9a-f]{32}', asset_id):
            return jsonify(error='Invalid asset ID'), 422
        uploaded = request.files.get('file')
        if uploaded is None:
            return jsonify(error='Missing file'), 422
        suffix = Path(uploaded.filename or '').suffix.lower()
        mime = ASSET_TYPES.get(suffix)
        if mime is None:
            return jsonify(error='Unsupported asset format'), 422
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ('asset' + suffix)
            uploaded.save(path)
            if path.stat().st_size <= 0 or path.stat().st_size > 512 * 1024 * 1024:
                return jsonify(error='Invalid asset size'), 413
            if mime.startswith('image/'):
                try:
                    with Image.open(path) as picture:
                        picture.verify()
                except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                    return jsonify(error='Invalid image asset'), 422
            key = store.put_asset(asset_id, suffix, path, mime)
            if registry:
                registry.save(asset_id, 'asset', store.config.bucket, key, path, mime=mime)
        record = {'bucket': store.config.bucket, 'key': key, 'mime': mime}
        target = records / ('asset-' + asset_id + '.json')
        with tempfile.NamedTemporaryFile(
                mode='w', dir=records, delete=False, encoding='utf-8') as output:
            json.dump(record, output)
            temporary = Path(output.name)
        temporary.replace(target)
        service_log.event(
            'tos', '素材已上传 TOS',
            context=f'asset_id={asset_id} mime={mime}')
        return jsonify(asset_id=asset_id), 201

    @app.get('/internal/assets/<asset_id>/url')
    def get_asset_url(asset_id):
        if not re.fullmatch(r'(?:asset|file)_[0-9a-f]{32}', asset_id):
            return jsonify(error='Invalid asset ID'), 422
        path = records / ('asset-' + asset_id + '.json')
        record = registry.get(asset_id, 'asset') if registry else None
        if record is None and path.exists():
            stored = json.loads(path.read_text(encoding='utf-8'))
            record = {'bucket': stored['bucket'], 'key': stored['key']}
            if registry:
                registry.save(
                    asset_id, 'asset', record['bucket'], record['key'],
                    mime=stored.get('mime'))
        if record is None:
            return jsonify(error='Asset not found'), 404
        expected_prefix = store.config.prefix + 'assets/' + asset_id
        if record['bucket'] != store.config.bucket or not record['key'].startswith(expected_prefix):
            return jsonify(error='Asset belongs to a different local storage configuration'), 409
        response = jsonify(url=store.sign_key(record['key']), expires_in=store.config.expires)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.delete('/internal/assets/<asset_id>')
    def delete_asset(asset_id):
        if not re.fullmatch(r'(?:asset|file)_[0-9a-f]{32}', asset_id):
            return jsonify(error='Invalid asset ID'), 422
        record = registry.get(asset_id, 'asset') if registry else None
        path = records / ('asset-' + asset_id + '.json')
        if record is None and path.exists():
            stored = json.loads(path.read_text(encoding='utf-8'))
            record = {'bucket': stored['bucket'], 'key': stored['key']}
        if record is None:
            return '', 204
        if record['bucket'] != store.config.bucket or not record['key'].startswith(
                store.config.prefix + 'assets/' + asset_id):
            return jsonify(error='Asset belongs to a different local storage configuration'), 409
        store.delete_key(record['key'])
        if registry:
            registry.delete(asset_id, 'asset')
        path.unlink(missing_ok=True)
        service_log.event('tos', '素材已从 TOS 删除', context=f'asset_id={asset_id}')
        return '', 204

    @app.get('/internal/results/<result_id>/url')
    def get_url(result_id):
        if not re.fullmatch(r'[0-9a-f]{32}', result_id):
            return jsonify(error='Invalid result ID'), 422
        path = records / (result_id + '.json')
        record=registry.get(result_id,'video') if registry else None
        if record is None and path.exists():
            record=json.loads(path.read_text(encoding='utf-8'))
            if registry: registry.save(result_id,'video',record['bucket'],record['key'])
        if record is None:
            return jsonify(error='Result not found'), 404
        if record != {'bucket': store.config.bucket, 'key': store.object_key(result_id)}:
            return jsonify(error='Result belongs to a different local storage configuration'), 409
        response = jsonify(url=store.sign(result_id), expires_in=store.config.expires)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/internal/results/<result_id>/poster')
    def result_poster(result_id):
        if not re.fullmatch(r'[0-9a-f]{32}', result_id):
            return jsonify(error='Invalid result ID'), 422
        poster_dir = records / 'posters'
        poster_dir.mkdir(parents=True, exist_ok=True)
        poster = poster_dir / (result_id + '.jpg')
        if poster.is_file() and poster.stat().st_size > 0:
            return send_file(poster, mimetype='image/jpeg', conditional=True, max_age=86400)
        record = registry.get(result_id, 'video') if registry else None
        if record is None:
            path = records / (result_id + '.json')
            if not path.is_file():
                return jsonify(error='Result not found'), 404
        url = store.sign(result_id)
        temporary = poster.with_suffix('.tmp.jpg')
        result = subprocess.run(
            ['ffmpeg', '-y', '-v', 'error', '-ss', '1', '-i', url,
             '-frames:v', '1', '-vf', 'scale=720:-2', '-q:v', '3', str(temporary)],
            capture_output=True, text=True)
        if result.returncode != 0 or not temporary.is_file():
            result = subprocess.run(
                ['ffmpeg', '-y', '-v', 'error', '-i', url,
                 '-frames:v', '1', '-vf', 'scale=720:-2', '-q:v', '3', str(temporary)],
                capture_output=True, text=True)
        if result.returncode != 0 or not temporary.is_file():
            service_log.event('tos', '封面抽帧失败', level='error',
                              context=result_id + ' ' + (result.stderr or '')[-200:])
            return jsonify(error='Poster unavailable'), 502
        os.replace(temporary, poster)
        return send_file(poster, mimetype='image/jpeg', conditional=True, max_age=86400)

    @app.delete('/internal/results/<result_id>')
    def delete_result(result_id):
        if not re.fullmatch(r'[0-9a-f]{32}', result_id):
            return jsonify(error='Invalid result ID'), 422
        store.delete(result_id)
        if registry:
            registry.delete(result_id, 'video')
        path = records / (result_id + '.json')
        if path.is_file():
            path.unlink(missing_ok=True)
        service_log.event('tos', '退款后删除视频对象', context='result_id=' + result_id)
        return '', 204

    @app.post('/internal/portraits/<result_id>')
    def upload_portrait(result_id):
        if not re.fullmatch(r'[0-9a-f]{32}', result_id):
            return jsonify(error='Invalid portrait ID'), 422
        uploaded = request.files.get('file')
        if uploaded is None:
            return jsonify(error='Missing file'), 422
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / 'source'
            path = Path(directory) / 'portrait.jpg'
            uploaded.save(original)
            if original.stat().st_size > 32 * 1024 * 1024:
                return jsonify(error='Portrait exceeds 32 MB'), 413
            try:
                with Image.open(original) as picture:
                    if picture.format not in ('JPEG', 'PNG', 'WEBP'):
                        return jsonify(error='Unsupported portrait format'), 422
                    width,height=picture.size
                    if width>=height or abs(width/height-9/16)>0.04:
                        return jsonify(error='Expected a 9:16 portrait'),422
                    picture.convert('RGB').save(path, 'JPEG', quality=95)
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                return jsonify(error='Invalid portrait image'), 422
            key = store.put_portrait(result_id, path)
            if registry: registry.save(result_id,'portrait',store.config.bucket,key,path,width,height)
        target = records / ('portrait-' + result_id + '.json')
        with tempfile.NamedTemporaryFile(mode='w', dir=records, delete=False, encoding='utf-8') as output:
            json.dump({'bucket': store.config.bucket, 'key': key}, output)
            temporary = Path(output.name)
        temporary.replace(target)
        service_log.event("tos", "定妆照已上传 TOS", context=f"result_id={result_id}")
        return jsonify(result_id=result_id), 201

    @app.get('/internal/portraits/<result_id>/url')
    def get_portrait_url(result_id):
        if not re.fullmatch(r'[0-9a-f]{32}', result_id):
            return jsonify(error='Invalid portrait ID'), 422
        path = records / ('portrait-' + result_id + '.json')
        record=registry.get(result_id,'portrait') if registry else None
        if record is None and path.exists():
            record=json.loads(path.read_text(encoding='utf-8'))
            if registry: registry.save(result_id,'portrait',record['bucket'],record['key'])
        if record is None:
            return jsonify(error='Portrait not found'), 404
        if record != {'bucket': store.config.bucket, 'key': store.portrait_key(result_id)}:
            return jsonify(error='Portrait belongs to a different local storage configuration'), 409
        response = jsonify(url=store.sign_portrait(result_id), expires_in=store.config.expires)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.delete('/internal/portraits/<result_id>')
    def delete_portrait(result_id):
        if not re.fullmatch(r'[0-9a-f]{32}', result_id):
            return jsonify(error='Invalid portrait ID'), 422
        store.delete_portrait(result_id)
        if registry:
            registry.delete(result_id, 'portrait')
        path = records / ('portrait-' + result_id + '.json')
        if path.is_file():
            path.unlink(missing_ok=True)
        service_log.event('tos', '退款后删除定妆照对象', context='result_id=' + result_id)
        return '', 204

    @app.errorhandler(Exception)
    def storage_error(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return error
        # SDK exceptions may contain signed URLs: report safe diagnostics only.
        app.logger.error('TOS operation failed: %s code=%s request_id=%s',
                         type(error).__name__, getattr(error, 'code', ''), getattr(error, 'request_id', ''))
        return jsonify(error='TOS operation failed; check bucket permissions, region and dedicated credentials'), 502

    return app


if __name__ == '__main__':
    from common.service_app import serve
    serve(create_app(),10012)
