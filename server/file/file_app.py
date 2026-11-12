import os
import uuid
import mimetypes
from pathlib import Path
from flask import Flask, request, jsonify, send_file, redirect
from werkzeug.utils import secure_filename
from common import service_log
from common import storage_client

app = Flask(__name__)
from common.service_app import protect,serve
service_log.install("file")
protect(app, {'phase2-file'}, public={'/api/v1/file/health'}, public_prefixes=('/api/v1/file/download/',))

# ========== 配置 ==========
UPLOAD_ROOT = os.getenv("UPLOAD_ROOT", "/home/aitravel/file/data")          # 根目录
ALLOWED_EXTENSIONS = {'webp','png','jpg','jpeg','mp4','mp3'}
MAX_CONTENT_LENGTH = 30 * 1024 * 1024              # 30MB
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# 确保根目录存在
os.makedirs(UPLOAD_ROOT, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_uuid_filename(original_filename):
    """生成 UUID 文件名（保留扩展名）"""
    ext = os.path.splitext(original_filename)[1]
    return f"{uuid.uuid4().hex}{ext}"

def safe_path_component(value):
    """清洗路径组件，防止路径穿越"""
    clean = secure_filename(value)
    return clean if clean else 'unknown'

# ========== 上传接口 ==========
@app.route('/api/v1/file/upload', methods=['POST'])
def upload_file():
    # 1. 检查文件
    if 'file' not in request.files:
        return jsonify({"code": 400, "msg": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"code": 400, "msg": "No selected file"}), 400

    # 2. 获取 wx_openid（必填）
    wx_openid = request.form.get('wx_openid') or request.args.get('wx_openid')
    if not wx_openid:
        return jsonify({"code": 400, "msg": "Missing wx_openid"}), 400

    # 3. 校验文件类型
    if not allowed_file(file.filename):
        return jsonify({"code": 400, "msg": "File type not allowed"}), 400

    # 4. 安全处理文件名（仅用于获取扩展名）
    original_filename = secure_filename(file.filename)
    if not original_filename:
        return jsonify({"code": 400, "msg": "Invalid filename"}), 400

    # 5. 安全清洗 wx_openid 作为目录名
    safe_wechat = safe_path_component(wx_openid)
    save_dir = os.path.join(UPLOAD_ROOT, safe_wechat)
    os.makedirs(save_dir, exist_ok=True)

    # 6. 生成 UUID 文件名并保存
    uuid_filename = generate_uuid_filename(original_filename)
    save_path = os.path.join(save_dir, uuid_filename)
    try:
        file.save(save_path)
    except Exception as e:
        return jsonify({"code": 500, "msg": f"Save failed: {str(e)}"}), 500

    storage = 'local'
    if os.getenv('LOCAL_CLOUD_ENABLED') == 'true':
        asset_id = 'file_' + Path(uuid_filename).stem
        try:
            storage_client.upload_asset(
                asset_id, save_path,
                mimetypes.guess_type(uuid_filename)[0] or 'application/octet-stream')
            os.unlink(save_path)
            storage = 'tos'
            service_log.event(
                'tos', '文件已转存 TOS 并删除本地缓存',
                context=f'asset_id={asset_id}')
        except Exception:
            Path(save_path).unlink(missing_ok=True)
            service_log.event(
                'tos', '文件转存失败，本地临时文件已删除',
                context=f'asset_id={asset_id}', level='error')
            return jsonify({"code": 503, "msg": "Storage temporarily unavailable"}), 503

    # 7. 构造可直接访问的 URL（外部代理前缀）
    download_url = f"/aitravel/api/v1/file/download/{safe_wechat}/{uuid_filename}"
    relative_path = os.path.relpath(save_path, UPLOAD_ROOT)
    service_log.event("file", "文件上传成功", context=f"path={relative_path}")

    return jsonify({
        "code": 0,
        "msg": "Upload success",
        "data": {
            "file_path": relative_path,
            "download_url": download_url,
            "uuid_filename": uuid_filename,
            "wx_openid": wx_openid,
            "storage": storage
        }
    }), 200


# ========== 文件下载/访问接口 ==========
@app.route('/api/v1/file/download/<wx_openid>/<filename>', methods=['GET'])
def download_file(wx_openid, filename):
    """
    通过 wx_openid 和 UUID 文件名获取文件
    """
    safe_wechat = safe_path_component(wx_openid)
    safe_filename = secure_filename(filename)
    if not safe_filename:
        return jsonify({"code": 400, "msg": "Invalid filename"}), 400

    file_path = os.path.join(UPLOAD_ROOT, safe_wechat, safe_filename)
    if not os.path.isfile(file_path):
        asset_id = 'file_' + Path(safe_filename).stem
        try:
            return redirect(storage_client.asset_url(asset_id), code=302)
        except Exception:
            return jsonify({"code": 404, "msg": "File not found"}), 404
    if not os.path.realpath(file_path).startswith(os.path.realpath(UPLOAD_ROOT)):
        return jsonify({"code": 403, "msg": "Access denied"}), 403

    return send_file(file_path)


@app.delete('/api/v1/file/delete/<wx_openid>/<filename>')
def delete_file(wx_openid, filename):
    safe_wechat = safe_path_component(wx_openid)
    safe_filename = secure_filename(filename)
    if not safe_filename:
        return jsonify({"code": 400, "msg": "Invalid filename"}), 400
    file_path = os.path.join(UPLOAD_ROOT, safe_wechat, safe_filename)
    root = os.path.realpath(UPLOAD_ROOT)
    resolved = os.path.realpath(file_path)
    if resolved == root or os.path.commonpath([root, resolved]) != root:
        return jsonify({"code": 403, "msg": "Access denied"}), 403
    try:
        os.unlink(resolved)
    except FileNotFoundError:
        pass
    except OSError:
        return jsonify({"code": 500, "msg": "Delete failed"}), 500
    if os.getenv('LOCAL_CLOUD_ENABLED') == 'true':
        try:
            storage_client.delete_asset('file_' + Path(safe_filename).stem)
        except Exception:
            service_log.event(
                'tos', '生成文件 TOS 删除失败',
                context=f'path={safe_wechat}/{safe_filename}', level='error')
            return jsonify({"code": 503, "msg": "Storage deletion pending"}), 503
    service_log.event("file", "生成中间文件已按保留期删除", context=f"path={safe_wechat}/{safe_filename}")
    return '', 204

# ========== 健康检查 ==========
@app.route('/api/v1/file/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "build_revision": os.getenv("BUILD_REVISION", "unknown")}), 200

if __name__ == '__main__':
    serve(app,10004)
