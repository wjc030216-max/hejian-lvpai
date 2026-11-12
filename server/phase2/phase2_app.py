"""Phase-two catalogue, participant uploads, test orders, and durable work records."""
import json
import mimetypes
import os
import re
import threading
import time
import uuid
import hashlib
import requests
from io import BytesIO
from threading import Lock
from PIL import Image, ImageOps
from werkzeug.exceptions import HTTPException
try:
    from .store import Store
    from .catalog import select_catalog
except ImportError:
    from store import Store
    from catalog import select_catalog
from urllib.parse import quote, urlsplit
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, jsonify, request, send_file, send_from_directory, redirect, Response
try:
    from .orchestrator import Phase2Orchestrator
except ImportError:  # direct `python server/phase2/phase2_app.py`
    from orchestrator import Phase2Orchestrator
try:
    from .runtime_templates import build_runtime_template, normalize_template, SOURCE_ROOT
    from .participation import user_roles, validate_selection
    from .pricing import price_for_template, public_pricing
    from . import free_quota
    from .retention_service import (
        RETENTION_SECONDS, expired as retention_expired, expires_at as retention_expires_at,
    )
except ImportError:
    from runtime_templates import build_runtime_template, normalize_template, SOURCE_ROOT
    from participation import user_roles, validate_selection
    from pricing import price_for_template, public_pricing
    import free_quota
    from retention_service import (
        RETENTION_SECONDS, expired as retention_expired, expires_at as retention_expires_at,
    )
try:
    from flask_cors import CORS
except ImportError:
    def CORS(_app, **_kwargs):
        """本机快速验证时允许缺少可选 flask-cors 依赖。"""
        return None

try:
    import redis
except ImportError:  # 本地直接 python 启动时允许没有 redis 依赖
    redis = None
try:
    import pymysql
except ImportError:
    pymysql = None

ROOT = Path(__file__).resolve().parent
DATA_FILE = Path(os.getenv("PHASE2_TEMPLATE_FILE", ROOT / "templates.json"))
UPLOAD_ROOT = Path(os.getenv("PHASE2_UPLOAD_ROOT", ROOT / "uploads"))
SHARE_CACHE_DIR = Path(os.getenv("PHASE2_SHARE_CACHE_DIR", UPLOAD_ROOT / "share-cache"))
SHARE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
STATIC_ROOT = Path(os.getenv("PHASE2_STATIC_ROOT", ROOT.parent.parent / "client/assets"))
PORT = int(os.getenv("PORT", "10020"))
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/2")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:10020").rstrip("/")
PUBLIC_API_BASE_URL = os.getenv("PUBLIC_API_BASE_URL", "http://127.0.0.1:10020").rstrip("/")
MEDIA_TOKEN_TTL = max(300, int(os.getenv("PHASE2_MEDIA_TOKEN_TTL", "7200")))
_MEDIA_ARCHIVE_TTL = max(0, int(os.getenv("PHASE2_MEDIA_ARCHIVE_CACHE", "30")))
TEMPLATE_PREVIEW_VERSION = os.getenv("PHASE2_TEMPLATE_PREVIEW_VERSION", "720p-20261029")
if os.getenv("APP_ENV", "").startswith("local") and any(host in PUBLIC_BASE_URL for host in ("test-api.guizhilv.com.cn", "api.guizhilv.com.cn", "guizhilv.com.cn")):
    raise RuntimeError("本地二期服务禁止使用测试或生产 PUBLIC_BASE_URL")
DB_HOST = os.getenv("PHASE2_DB_HOST", "")
DB_PORT = int(os.getenv("PHASE2_DB_PORT", "3306"))
DB_NAME = os.getenv("PHASE2_DB_NAME", "aitravel_dev")
DB_USER = os.getenv("PHASE2_DB_USER", "aitravel_dev")
DB_PASSWORD = os.getenv("PHASE2_DB_PASSWORD", "aitravel_dev_password")

LOCAL_REAL = os.getenv('APP_ENV') in ('local-real','staging','production')
from common.staging import validate_staging
validate_staging()
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
_cors_origins = [value.strip() for value in os.getenv('PHASE2_CORS_ORIGINS', '').split(',') if value.strip()]
if not _cors_origins:
    _cors_origins = ['*'] if os.getenv('APP_ENV') not in ('staging', 'production') else [PUBLIC_API_BASE_URL]
CORS(app, resources={r"/api/*": {"origins": _cors_origins}})
_template_lock = Lock()
_db_ready = os.getenv('PHASE2_STATE_BACKEND') == 'mysql' or bool(DB_HOST)
_catalog_engine = None
if os.getenv('PHASE2_STATE_BACKEND') == 'mysql':
    from phase2.mysql_store import MySQLStore
    store = MySQLStore(os.environ['AITRAVEL_DATABASE_URL'], UPLOAD_ROOT / 'phase2.sqlite3')
else:
    store = Store(os.getenv("PHASE2_STATE_DB", str(UPLOAD_ROOT / "phase2.sqlite3")))
orchestrator = Phase2Orchestrator(store, UPLOAD_ROOT, SHARE_CACHE_DIR)
from phase2 import auth, wechat_pay, allinpay, wechat_subscribe
from common.media_refs import transform
from common.rate_limit import SlidingWindowLimiter
from common import service_log
from common import storage_client
service_log.install('phase2-api')
service_log.install_http_access(app)
_project_limiter = SlidingWindowLimiter()
_upload_limiter = SlidingWindowLimiter()


_template_cache = {'expires':0,'items':None}
PROJECT_CREATE_LIMIT = max(1, int(os.getenv('PHASE2_PROJECTS_PER_HOUR', '30')))
UPLOAD_LIMIT = max(1, int(os.getenv('PHASE2_UPLOADS_PER_10_MINUTES', '30')))
# 本地开发/联调默认跳过限流，避免测试把配额打满；生产不受影响。
DEV_RATE_LIMIT_BYPASS = (os.getenv('APP_ENV', 'development') in ('local', 'local-real', 'development', 'test')
                         and os.getenv('PHASE2_RATE_LIMIT_DEV', 'true').lower() == 'true')


def _load_templates():
    if os.getenv('APP_ENV') == 'test':
        return _read_templates()
    if _template_cache['expires']>time.monotonic():
        return _template_cache['items']
    if not _template_lock.acquire(blocking=_template_cache['items'] is None):
        return _template_cache['items']
    try:
        if _template_cache['expires'] <= time.monotonic():
            _template_cache.update(items=_read_templates(),expires=time.monotonic()+15)
        return _template_cache['items']
    finally:
        _template_lock.release()


def _read_templates():
    if hasattr(store, 'engine'):
        from sqlalchemy import text
        with store.engine.connect() as db:
            source = [r[0] for r in db.execute(text('SELECT payload_json FROM phase2_templates WHERE status=1 ORDER BY sort_order,id'))]
    elif DB_HOST:
        # Local SQLite project state may still share a MySQL template catalogue.
        from common.database_schema import engine_for, ensure_schema
        from sqlalchemy.engine import URL
        from sqlalchemy import text
        global _catalog_engine
        if _catalog_engine is None:
            _catalog_engine=engine_for(URL.create('mysql+pymysql',username=DB_USER,password=DB_PASSWORD,host=DB_HOST,port=DB_PORT,database=DB_NAME,query={'charset':'utf8mb4'}).render_as_string(hide_password=False))
            ensure_schema(_catalog_engine)
        with _catalog_engine.connect() as db:
            source=[r[0] for r in db.execute(text('SELECT payload_json FROM phase2_templates WHERE status=1 ORDER BY sort_order,id'))]
    elif DATA_FILE.is_file():
        source=json.loads(DATA_FILE.read_text(encoding='utf-8'))
    else:
        source=[]
    items=[]
    for row in source:
        try:
            value=json.loads(row) if isinstance(row,str) else row
            items.append(normalize_template(value))
        except (ValueError,KeyError,TypeError,OSError):
            app.logger.warning('Invalid template excluded from catalogue')
    return items


def _public_template(item):
    """统一给小程序的模板字段，兼容本地模板 JSON 和后续数据库来源。"""
    fields=('template_code','video_name','story_hook','actor_count','character_settings','relationship_tags','video_genre','location_association','video_specification','duration_seconds','segment_count','cover','example','assets','lead_gender_tags')
    result = transform({key:item[key] for key in fields if key in item})
    result.pop("mock_heat_label", None)
    result["version"] = (str(item.get("asset_revision") or "")[:16]
                         or hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16])
    result.pop('managed_runtime',None)
    result.pop('source_metadata',None)
    result.setdefault("id", result.get("template_code"))
    result.setdefault("name", result.get("video_name", ""))
    for key in ("cover", "example"):
        if result.get(key, "").startswith("/"):
            result[key] = PUBLIC_BASE_URL + quote(result[key], safe="/%")
    version = quote(TEMPLATE_PREVIEW_VERSION, safe='')
    direct_cover = str(result.get('cover') or '')
    direct_example = str(result.get('example') or '')
    if direct_cover.startswith(PUBLIC_BASE_URL):
        result['cover'] = direct_cover + ('&' if '?' in direct_cover else '?') + 'v=' + version
    if direct_example.startswith(PUBLIC_BASE_URL):
        result['example'] = direct_example + ('&' if '?' in direct_example else '?') + 'v=' + version
    if item.get('managed_runtime') and not direct_example.startswith(PUBLIC_BASE_URL):
        result['example'] = (
            PUBLIC_API_BASE_URL.rstrip('/')
            + '/api/v2/templates/'
            + quote(item['template_code'], safe='')
            + '/example.mp4?v='
            + quote(TEMPLATE_PREVIEW_VERSION, safe='')
        )
        result['cover'] = (
            PUBLIC_API_BASE_URL.rstrip('/')
            + '/api/v2/templates/'
            + quote(item['template_code'], safe='')
            + '/cover'
        )
    if LOCAL_REAL:
        try:
            runtime = build_runtime_template(item)
            ids = [role["character_id"] for role in runtime["config_json"]["module_1"]["character_settings"]]
            result["assets"] = {"scene": runtime["screen_url"][0], "roles": {
                role_id: {"roleImage": runtime["actor_url"][index], "costumeImage": runtime["costume_url"][index]}
                for index, role_id in enumerate(ids)
            }}
        except (ValueError, OSError) as error:
            result["runtime_error"] = str(error)
    cover = str(result.get("cover") or "")
    if cover and "v=" not in cover:
        result["cover"] = cover + ("&" if "?" in cover else "?") + "v=" + quote(TEMPLATE_PREVIEW_VERSION, safe="")
    return result


_cached_redis = None


def _redis_client():
    global _cached_redis
    if not redis:
        return None
    try:
        if _cached_redis is None:
            _cached_redis = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=1, socket_connect_timeout=1)
        client = _cached_redis
        client.ping()
        return client
    except Exception:
        return None


def _response(data=None, msg="ok", code=200):
    return jsonify({"code": code, "msg": msg, "data": data}), code


@app.get("/api/v2/health")
def health():
    try:
        with store.connection() as db:
            db.execute('SELECT 1').fetchone()
    except Exception:
        return _response({'status':'database unavailable'},'数据库暂时不可用',503)
    redis_ok = _redis_client() is not None
    return _response({"service": "phase2", "environment": os.getenv("APP_ENV", "development"),
                      "build_revision": os.getenv("BUILD_REVISION", "unknown"),
                      "cloud_calls_enabled": os.getenv("LOCAL_CLOUD_ENABLED") == "true",
                      "payment_mode": wechat_pay.payment_mode(), "status": "ok",
                      "template_source": "mysql" if _db_ready else "json",
                      "redis": redis_ok, "database_configured": bool(DB_HOST and pymysql),
                      "ai_orchestration_enabled": orchestrator.enabled})


def _stream_remote(url, content_type):
    headers = {}
    requested_range = request.headers.get("Range", "").strip()
    if requested_range:
        headers["Range"] = requested_range[:256]
    try:
        upstream = requests.get(url, headers=headers, stream=True, timeout=(5, 60))
    except requests.RequestException:
        return _response(None, "文件暂时无法读取，请稍后重试", 502)
    if upstream.status_code not in (200, 206):
        upstream.close()
        return _response(None, "文件暂时无法读取，请稍后重试", 502)
    response_headers = {
        "Content-Type": content_type,
        "Cache-Control": "no-store",
        "Vary": "Range",
    }
    for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
        value = upstream.headers.get(name, "")
        if value:
            response_headers[name] = value

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=256 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(generate(), status=upstream.status_code, headers=response_headers)


def _harmony_client():
    """HarmonyOS WeChat does not reliably follow cross-domain media redirects."""
    agent = (request.headers.get("User-Agent") or "").lower()
    return any(token in agent for token in ("harmonyos", "openharmony", "arkweb"))


def _direct_media(value):
    """Prefer a pre-signed object-storage URL so media bypasses this gateway."""
    if os.getenv('PHASE2_MEDIA_DIRECT', 'true').lower() == 'false':
        return ''
    match = re.search(r'/api/v2/(results|portraits|assets)/([0-9a-f]{32})', value or '')
    if not match:
        return ''
    kind, result_id = match.groups()
    try:
        from common.storage_client import asset_url, portrait_url, result_url
        if kind == 'results':
            return result_url(result_id)
        if kind == 'portraits':
            return portrait_url(result_id)
        return asset_url(result_id)
    except Exception:
        return ''


_archive_cache = {}
_archive_lock = threading.Lock()


def _media_archived(kind, result_id):
    key = (kind, result_id)
    now = time.time()
    with _archive_lock:
        cached = _archive_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    project = store.project_for_media(kind, result_id)
    archived = bool(project and (project.get('status') == 'refunded'
                                 or project.get('refund_status') == 'succeeded'
                                 or project.get('status') == 'expired'
                                 or retention_expired(project)))
    if _MEDIA_ARCHIVE_TTL:
        with _archive_lock:
            if len(_archive_cache) > 5000:
                _archive_cache.clear()
            _archive_cache[key] = (now + _MEDIA_ARCHIVE_TTL, archived)
    return archived


@app.get("/api/v2/results/<result_id>/poster")
def stored_result_poster(result_id):
    if not LOCAL_REAL or not re.fullmatch(r"[0-9a-f]{32}", result_id):
        return _response(None, "作品不存在", 404)
    if _media_archived('video', result_id):
        return _response(None, "作品已因退款归档", 404)
    from common.storage_client import result_poster
    try:
        payload = result_poster(result_id)
    except (RuntimeError, OSError):
        return _response(None, "封面暂不可用", 503)
    return Response(payload, status=200, mimetype="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})

@app.get("/api/v2/results/<result_id>")
def stored_result(result_id):
    if not LOCAL_REAL or not re.fullmatch(r"[0-9a-f]{32}", result_id):
        return _response(None, "作品不存在", 404)
    if _media_archived('video', result_id):
        return _response(None, "作品已因退款归档", 404)
    from common.storage_client import result_url
    try:
        url = result_url(result_id)
    except (RuntimeError, OSError):
        return _response(None, "TOS 作品暂时无法读取，请检查本地存储服务", 503)
    if os.getenv("PHASE2_MEDIA_REDIRECT", "false").lower() == "true" and not _harmony_client():
        # Hand playback straight to object storage so media never crosses this
        # gateway. Extra query parameters from the client stay on this URL.
        response = redirect(url, code=302)
        response.headers["Cache-Control"] = "private, max-age=60"
        return response
    # Keep local playback on the API origin. WeChat's video component follows
    # redirects inconsistently in DevTools, especially across HTTPS domains.
    return _stream_remote(url, "video/mp4")


@app.get("/api/v2/portraits/<result_id>")
def stored_portrait(result_id):
    if not LOCAL_REAL or not re.fullmatch(r"[0-9a-f]{32}", result_id):
        return _response(None, "定妆照不存在", 404)
    if _media_archived('portrait', result_id):
        return _response(None, "作品已因退款归档", 404)
    from common.storage_client import portrait_url
    try:
        url = portrait_url(result_id)
    except (RuntimeError, OSError):
        return _response(None, "定妆照暂时无法读取，请检查本地存储服务", 503)
    if os.getenv("PHASE2_MEDIA_REDIRECT", "false").lower() == "true" and not _harmony_client():
        response = redirect(url, code=302)
        response.headers["Cache-Control"] = "private, max-age=60"
        return response
    return _stream_remote(url, "image/jpeg")


@app.get("/api/v2/share/<pid>")
def share_view(pid):
    """Public, read-only snapshot for the WeChat share card."""
    if not re.fullmatch(r"p2_[A-Za-z0-9_-]{1,64}", pid):
        return _response(None, "作品不存在", 404)
    p = store.get(pid)
    if not p:
        return _response(None, "作品未完成或不存在", 404)
    if retention_expired(p):
        return _response(None, "作品数据保留期已结束", 410)
    if p.get("status") != "completed" or not (p.get("portrait_url") or p.get("video_url")):
        return _response(None, "作品未完成或不存在", 404)
    template = p.get("template") or {}
    share_ttl = MEDIA_TOKEN_TTL
    data = {
        "template_code": p.get("template_code", ""),
        "video_name": template.get("video_name", "我的旅行电影"),
        "location_association": template.get("location_association", ""),
        "story_hook": template.get("story_hook", ""),
        "portrait_url": _direct_media(p.get("portrait_url") or "") or auth.media_url(p.get("portrait_url") or "", share_ttl),
        "video_url": _direct_media(p.get("video_url") or "") or auth.media_url(p.get("video_url") or "", share_ttl),
        "share_image_url": (PUBLIC_API_BASE_URL.rstrip("/") + "/api/v2/share-image/" + pid + ".jpg?v=2") if p.get("portrait_url") else "",
        "completed": p.get("status") == "completed",
        "created_at": p.get("created_at"),
    }
    return _response(data)


SHARE_LOGO = os.path.join(os.path.dirname(__file__), 'assets', 'share-logo.png')


def _brand_share_card(size=(800, 640)):
    """Dark branded card used as the WeChat share thumbnail."""
    from PIL import Image, ImageDraw
    width, height = size
    canvas = Image.new('RGB', (width, height), (6, 24, 23))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([24, 24, width - 25, height - 25], outline=(242, 208, 140), width=2)
    if not os.path.exists(SHARE_LOGO):
        return None
    logo = Image.open(SHARE_LOGO).convert('RGBA')
    ratio = min((width * 0.72) / logo.width, (height * 0.42) / logo.height)
    logo = logo.resize((max(1, int(logo.width * ratio)), max(1, int(logo.height * ratio))), Image.LANCZOS)
    canvas.paste(logo, ((width - logo.width) // 2, (height - logo.height) // 2), logo)
    return canvas


def _crop_cover(image, ratio):
    """Center-crop to the WeChat share-card ratio without distorting."""
    width, height = image.size
    target = ratio
    current = width / max(height, 1)
    if current > target:
        new_width = int(height * target)
        left = (width - new_width) // 2
        image = image.crop((left, 0, left + new_width, height))
    else:
        new_height = int(width / target)
        top = (height - new_height) // 2
        image = image.crop((0, top, width, top + new_height))
    return image


@app.get("/api/v2/share-image/<pid>.jpg")
def share_image(pid):
    """A small, direct, public thumbnail so WeChat never falls back to the logo."""
    if not re.fullmatch(r"p2_[A-Za-z0-9_-]{1,64}", pid):
        return _response(None, "图片不存在", 404)
    p = store.get(pid)
    if not p:
        return _response(None, "图片不存在", 404)
    if retention_expired(p):
        return _response(None, "图片数据保留期已结束", 410)
    if p.get("status") != "completed" or not p.get("portrait_url"):
        return _response(None, "图片不存在", 404)
    portrait = p.get("portrait_url") or ""
    match = re.search(r"/api/v2/portraits/([0-9a-f]{32})", portrait)
    if not match:
        return _response(None, "图片不存在", 404)
    result_id = match.group(1)
    share_asset_id = 'asset_' + hashlib.sha256(
        f'{pid}:{result_id}:share-v2'.encode()).hexdigest()[:32]
    try:
        return redirect(storage_client.asset_url(share_asset_id), code=302)
    except Exception:
        try:
            card = _brand_share_card()
            if card is None:
                from common.storage_client import portrait_url
                direct = portrait_url(result_id)
                with requests.get(direct, timeout=(5, 30)) as response:
                    response.raise_for_status()
                    card = Image.open(BytesIO(response.content))
                    card = ImageOps.exif_transpose(card)
                    card = _crop_cover(card, 5 / 4)
                    card.thumbnail((800, 640))
            output = BytesIO()
            card.convert("RGB").save(output, "JPEG", quality=86)
            storage_client.upload_asset_bytes(
                share_asset_id, output.getvalue(),
                share_asset_id + '.jpg', 'image/jpeg')
        except Exception:
            app.logger.exception("share thumbnail build failed project=%s", pid)
            return _response(None, "图片暂时不可用", 503)
    response = redirect(storage_client.asset_url(share_asset_id), code=302)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/v2/media-ready")
def media_ready():
    token = os.getenv("LOCAL_MEDIA_TOKEN", "")
    return jsonify({"installation": hashlib.sha256(token.encode()).hexdigest()})


@app.get("/assets/<path:asset_path>")
def static_asset(asset_path):
    if Path(asset_path).suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.mp4','.mp3','.wav'):
        return _response(None,'文件不存在',404)
    if asset_path.startswith("phase2-templates/"):
        if SOURCE_ROOT is None:
            return _response(None,'文件不存在',404)
        return send_from_directory(SOURCE_ROOT, asset_path[len("phase2-templates/"):])
    if not STATIC_ROOT.exists():
        return _response(None, "静态资源目录不存在", 404)
    return send_from_directory(STATIC_ROOT, asset_path)


@app.get("/api/v2/scenes")
def scenes():
    names = sorted({
        str(item.get('location_association')).strip()
        for item in _load_templates()
        if item.get('location_association')
    })
    return _response([{"id": name, "name": name} for name in names])


@app.get("/api/v2/templates")
def templates():
    items = _load_templates()
    codes = [i['template_code'] for i in items]
    bases = {}
    if codes:
        marks = ','.join('?' for _ in codes)
        with store.connection() as db:
            bases = {r[0]: (r[1], r[2]) for r in db.execute('SELECT template_code,heat_base,show_heat FROM phase2_template_settings WHERE template_code IN ('+marks+')', codes)}
    heat = {code: bases.get(code, (0, 0))[0] for code in codes}
    try:
        result = select_catalog(items, request.args, heat)
    except ValueError as error:
        return _response(None, str(error), 422)
    result['items'] = [_public_template(item) for item in result['items']]
    from common.management_schema import heat_label
    for item in result['items']:
        baseline, show = bases.get(item['template_code'], (0, 0))
        item['heat_label'] = heat_label(baseline) if show else ''
    return _response(result)


@app.get("/api/v2/templates/<template_code>/runtime")
def template_runtime(template_code):
    """Return the local runtime contract used by the real AI services."""
    if template_code.startswith("P2S-"):
        record = store.get(template_code[4:])
        if record and record.get("runtime_snapshot"):
            return _response(transform(record["runtime_snapshot"]))
        return _response(None, "制作版本不存在", 404)
    item = next((item for item in _load_templates() if item.get("template_code") == template_code), None)
    if not item:
        return _response(None, "模板不存在", 404)
    try:
        return _response(build_runtime_template(item))
    except (OSError, ValueError, FileNotFoundError) as error:
        return _response(None, f"本地模板运行素材不完整: {error}", 422)


@app.get("/api/v2/templates/<template_code>/example.mp4")
def template_example(template_code):
    """Serve sample video through an ASCII path for DevTools compatibility."""
    item = next((item for item in _load_templates() if item.get("template_code") == template_code), None)
    if not item:
        return _response(None, "模板不存在", 404)
    example = str(item.get("example") or "")
    prefix = "local-media:assets/"
    if example.startswith(prefix):
        relative = unquote(example[len(prefix):])
        path = STATIC_ROOT / relative
        if path.is_file():
            return send_file(path, mimetype="video/mp4", conditional=True, max_age=0)
        # Production keeps managed template files on the data/admin node.
        # The installation media URL is reverse-proxied back to that node.
        return redirect(
            PUBLIC_BASE_URL
            + "/assets/"
            + quote(relative, safe="/")
            + "?v="
            + quote(request.args.get("v") or TEMPLATE_PREVIEW_VERSION, safe=""),
            code=302,
        )
    if example.startswith(("http://", "https://")):
        return redirect(example, code=302)
    return _response(None, "示例视频不存在", 404)


@app.get("/api/v2/templates/<template_code>/cover")
def template_cover(template_code):
    """Serve the validated cover through the client-facing API gateway."""
    item = next((item for item in _load_templates() if item.get("template_code") == template_code), None)
    if not item:
        return _response(None, "模板不存在", 404)
    cover = str(item.get("cover") or "")
    prefix = "local-media:assets/"
    if cover.startswith(prefix):
        relative = unquote(cover[len(prefix):])
        path = STATIC_ROOT / relative
        if path.is_file():
            mime = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.webp': 'image/webp',
            }.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return send_file(path, mimetype=mime, conditional=True, max_age=0)
        # Same fallback as example.mp4: production media lives on the admin node.
        return redirect(PUBLIC_BASE_URL + "/assets/" + quote(relative, safe="/"), code=302)
    if cover.startswith(("http://", "https://")):
        return redirect(cover, code=302)
    return _response(None, "模板封面不存在", 404)


@app.get("/api/v2/templates/<template_code>")
def template_detail(template_code):
    item = next((item for item in _load_templates() if item.get("template_code") == template_code), None)
    if not item:
        return _response(None, "模板不存在", 404)
    return _response(_public_template(item))



@app.before_request
def local_only_business():
    env = os.getenv('APP_ENV', 'development')
    allowed = ('local', 'local-real', 'development', 'test', 'staging', 'production')
    if request.path.startswith('/api/v2/') and env not in allowed:
        return _response(None, '二期服务环境配置无效', 503)
    if env == 'production' and request.path.startswith('/api/v2/'):
        if wechat_pay.payment_mode() != 'allinpay':
            return _response(None, '正式环境只允许通联 W06S', 503)
        if os.getenv('PHASE2_DEV_PAYMENT_BYPASS', 'false').lower() == 'true':
            return _response(None, '正式环境禁止跳过支付', 503)

@app.errorhandler(Exception)
def api_error(error):
    if isinstance(error, HTTPException):
        return _response(None, error.description, error.code)
    app.logger.exception('phase2 request failed')
    return _response(None, '服务处理失败，请稍后重试', 500)

@app.get('/api/v2/config')
def development_config():
    mode = wechat_pay.payment_mode()
    payment_enabled = mode in ('wechat', 'allinpay', 'dev-bypass')
    try:
        pricing = public_pricing() if mode in ('wechat', 'allinpay') else {
            'pricing_mode': 'disabled', 'amount_fen': 0,
            'price_tiers': {'1': 0, '2': 0, '3': 0},
            'standard_price_tiers': {},
            'charge_price_tiers': {},
        }
    except ValueError:
        pricing = {'pricing_mode': 'invalid', 'amount_fen': 0, 'price_tiers': {},
                   'standard_price_tiers': {}, 'charge_price_tiers': {}}
    agreement_version = os.getenv('PHASE2_AGREEMENT_VERSION', 'travel-photo-v1')
    try:
        quota = free_quota.status(store)
    except Exception:
        logger.exception('Could not read the free quota')
        quota = {'enabled': False, 'total': 0, 'used': 0, 'remaining': 0}
    quota_note = ('免费名额活动进行中（剩余 %d 个），本次下单若名额未满则不扣款，用完后按标准价支付。'
                  % quota['remaining']) if quota.get('remaining', 0) > 0 else ''
    agreement_text = ('使用照片进行人脸检查、定妆照和视频生成，并保存本次作品。请仅上传本人或已取得授权的照片。生成失败后可选择重新生成或申请全额退款；退款成功后作品归档并停止播放、保存和分享。' + (
        '当前支付功能暂未开放，提交制作暂不可用。' if mode in ('disabled', 'unconfigured')
        else '本次跳过支付，不扣款；云端生成仍可能产生接口费用。' if mode == 'dev-bypass'
        else quota_note or '支付确认后开始制作，支付金额以下单页面为准。'))
    return _response({'payment_mode':mode, 'payment_enabled':payment_enabled, **pricing,
                      'free_quota':quota,
                      'auth_mode':auth.mode(),
                      'ai_enabled':orchestrator.enabled,
                      'agreement_version':agreement_version,
                      'subscribe_template_id':wechat_subscribe.TEMPLATE_ID,
                      'agreement_text':agreement_text})

@app.post('/api/v2/projects')
def create_project():
    if not DEV_RATE_LIMIT_BYPASS and not _project_limiter.allow(
            auth.user(), PROJECT_CREATE_LIMIT, 3600):
        return _response(None, '创建制作过于频繁，请稍后再试', 429)
    body = request.get_json(silent=True) or {}
    template = next((t for t in _load_templates() if t['template_code']==body.get('template_code')),None)
    if not template:
        return _response(None,'模板不存在',404)
    version = _public_template(template)['version']
    if body.get('template_version') and body['template_version'] != version:
        return _response(None,'模板已更新，请重新选择当前样片',409)
    key = str(body.get('request_id') or uuid.uuid4().hex)
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',key):
        return _response(None,'请求编号无效',422)
    pid = 'p2_'+hashlib.sha256((auth.user()+':'+key).encode()).hexdigest()[:32]
    with store.creation_lock(pid), store.transaction() as db:
        old = store.get(pid,db)
        if old:
            if old['template_code'] != template['template_code']:
                return _response(None,'请求编号已用于其他模板',409)
            return _response(_view(old))
        roles = json.loads(json.dumps(template['character_settings']))
        for index,role in enumerate(roles):
            role.update(person_uid=str(role.get('person_uid') or index+1),check_status='empty',uploaded=False)
        created_at = time.time()
        record = {'project_id':pid,'wx_openid':auth.user(),'template_code':template['template_code'],
                  'template':dict(json.loads(json.dumps(_public_template(template))), **({'managed_runtime':template['managed_runtime']} if template.get('managed_runtime') else {})), 'template_version':version,
                  'runtime_code':'P2S-'+pid,'roles':roles,'status':'draft','stage':'portrait',
                  'created_at':created_at,'retention_expires_at':created_at+RETENTION_SECONDS,
                  'retention_cleanup':'pending','attempt':1,'participation_mode':'multi',
                  'selected_person_uid':''}
        store.put(record,db)
    service_log.event('orchestration', '创建制作草稿', context=f"project={pid} template={template['template_code']}")
    return _response(_view(record),code=201)

def _view(p):
    allowed = ('project_id','template_code','template_version','template','roles','status','stage','order_no',
               'created_at','paid_at','title','description','portrait_url','video_url','attempt','payment_mode',
               'refund_status','progress','completed_at','agreement_version','amount_paid','amount_fen',
               'list_price_fen',
               'payment_status','transaction_id','error','participation_mode','selected_person_uid',
               'refund_no','refunded_at','video_progress')
    result = {k:p.get(k) for k in allowed}
    result['ai_enabled'] = orchestrator.enabled
    if result.get('template'):
        result['template']={k:v for k,v in result['template'].items() if k not in ('managed_runtime','source_metadata')}
    result['roles'] = [{k:v for k,v in r.items() if k!='photo_path'} for r in p['roles']]
    selected = {r['person_uid'] for r in user_roles(p)}
    for role in result['roles']:
        role['uses_template'] = role['person_uid'] not in selected
    result['visitor_role_count'] = len(selected)
    result['template_role_count'] = len(p['roles']) - len(selected)
    result['video_name'] = p['template']['video_name']
    result['location_association'] = p['template']['location_association']
    for field in ('portrait_url','video_url'):
        value=result.get(field)
        if not value:
            continue
        if os.getenv('APP_ENV') in ('local','local-real'):
            parsed=urlsplit(value)
            if parsed.hostname in ('127.0.0.1','localhost') and parsed.path.startswith(
                    ('/api/v2/results/','/api/v2/portraits/','/api/v2/assets/')):
                suffix=parsed.path + (('?' + parsed.query) if parsed.query else '')
                result[field]=PUBLIC_API_BASE_URL.rstrip('/')+suffix
                continue
        result[field]=_direct_media(value) or auth.media_url(value, MEDIA_TOKEN_TTL)
    match = re.search(r'/api/v2/results/([0-9a-f]{32})', p.get('video_url') or '')
    if match and result.get('video_url'):
        poster_path='/api/v2/results/' + match.group(1) + '/poster'
        result['video_poster'] = auth.media_url(PUBLIC_API_BASE_URL.rstrip('/') + poster_path, MEDIA_TOKEN_TTL)
    if result.get('portrait_url'):
        result['share_image_url'] = PUBLIC_API_BASE_URL.rstrip("/") + "/api/v2/share-image/" + p['project_id'] + ".jpg?v=2"
    for role in result['roles']:
        if role.get('photo_url'): role['photo_url']=auth.media_url(role['photo_url'], MEDIA_TOKEN_TTL)
    expired = retention_expired(p)
    if expired:
        result['status'] = 'expired'
        result['stage'] = 'expired'
    if result.get('status') in ('refunded', 'expired') or result.get('refund_status') == 'succeeded':
        result['portrait_url'] = ''
        result['video_url'] = ''
        result['video_poster'] = ''
        result['share_image_url'] = ''
        result['title'] = '数据保留期已结束' if expired else '退款成功，作品已归档'
        result['description'] = (
            '用户过程数据、提示词、输入图片和生成媒体已按 90 天策略清理'
            if expired else '全额退款已完成，作品已停止播放、保存和分享')
        for role in result['roles']:
            role['photo_url'] = ''
        if expired:
            result['roles'] = []
            result['visitor_role_count'] = 0
            result['template_role_count'] = 0
    result['retention_expires_at'] = retention_expires_at(p)
    return result

@app.get('/api/v2/projects/<pid>')
@app.get('/api/v2/projects/<pid>/status')
def project_detail(pid):
    p = store.get(pid)
    return _response(_view(p)) if p else _response(None,'制作记录不存在',404)

@app.post('/api/v2/projects/<pid>/participation')
def participation(pid):
    body = request.get_json(silent=True) or {}
    mode, selected = body.get('mode'), str(body.get('person_uid') or '')
    if mode not in ('single', 'multi'):
        return _response(None, '拍摄模式无效', 422)
    with store.transaction() as db:
        p = store.get(pid, db)
        if not p:
            return _response(None, '制作记录不存在', 404)
        if retention_expired(p):
            return _response(None, '制作记录已超过数据保留期，请重新选择模板', 409)
        if p.get('order_no'):
            return _response(None, '已开始制作，不能更换角色', 409)
        if mode == 'single' and len(p['roles']) < 2:
            return _response(None, '单人模板直接上传照片即可', 422)
        if selected and not any(r['person_uid'] == selected for r in p['roles']):
            return _response(None, '角色位置无效', 422)
        p.update(participation_mode=mode, selected_person_uid=selected if mode == 'single' else '')
        if mode == 'single':
            try:
                runtime = build_runtime_template(p['template'])
                if selected:
                    validate_selection(p, runtime)
            except (OSError, ValueError) as error:
                return _response(None, '一人体验不可用：' + str(error), 422)
        store.put(p, db)
    return _response(_view(p))

@app.post('/api/v2/projects/<pid>/assets')
def upload_asset(pid):
    if not DEV_RATE_LIMIT_BYPASS and not _upload_limiter.allow(
            auth.user(), UPLOAD_LIMIT, 600):
        return _response(None, '照片上传过于频繁，请稍后再试', 429)
    p = store.get(pid)
    if not p:
        return _response(None,'制作记录不存在',404)
    if retention_expired(p):
        return _response(None,'制作记录已超过数据保留期，请重新选择模板',409)
    role_id = request.form.get('person_uid','')
    if p.get('order_no') or not any(r['person_uid']==role_id for r in user_roles(p)):
        return _response(None,'已开始制作或角色位置无效',409)
    if request.form.get('photo_consent') != 'true':
        return _response(None,'请确认照片使用授权',422)
    upload = request.files.get('file')
    if not upload:
        return _response(None,'请选择照片',422)
    folder = UPLOAD_ROOT/pid
    folder.mkdir(parents=True, exist_ok=True)
    aid = 'asset_'+uuid.uuid4().hex
    target = folder/(aid+'.jpg')
    try:
        with Image.open(upload.stream) as image:
            image = ImageOps.exif_transpose(image)
            if min(image.size)<301 or max(image.size)>6000 or not 0.4 < image.width/image.height < 2.5:
                return _response(None,'请使用宽高301至6000像素、比例正常的清晰单人照片',422)
            image.convert('RGB').save(target,'JPEG',quality=95)
    except (OSError, ValueError, Image.DecompressionBombError):
        return _response(None,'图片无法读取，请更换照片',422)
    url = PUBLIC_BASE_URL+'/api/v2/assets/'+aid
    stored_in_tos = False
    if LOCAL_REAL:
        try:
            storage_client.upload_asset(aid, target, 'image/jpeg')
            stored_in_tos = True
        except Exception:
            target.unlink(missing_ok=True)
            service_log.event(
                'tos', '角色照片转存失败，已删除本地临时文件',
                context=f'project={pid} asset={aid}', level='error')
            return _response(None,'照片存储暂时不可用，请稍后重试',503)
    try:
        with store.transaction() as db:
            p = store.get(pid,db)
            if p.get('order_no') or not any(r['person_uid']==role_id for r in user_roles(p)):
                target.unlink(missing_ok=True)
                if stored_in_tos:
                    storage_client.delete_asset(aid)
                return _response(None,'该作品已开始制作',409)
            for r in p['roles']:
                if r['person_uid']==role_id:
                    r.update(uploaded=True,photo_asset_id=aid,
                             photo_path='' if stored_in_tos else str(target),
                             photo_url=url,photo_storage='tos' if stored_in_tos else 'local',
                             check_status='checking',check_message='正在检查照片',check_attempts=0,
                             photo_consent_at=time.time())
            p['status']='uploading'
            store.put(p,db)
    except Exception:
        target.unlink(missing_ok=True)
        if stored_in_tos:
            try:
                storage_client.delete_asset(aid)
            except Exception:
                service_log.event(
                    'tos', '上传记录失败后的 TOS 回滚未完成',
                    context=f'project={pid} asset={aid}', level='error')
        raise
    if stored_in_tos:
        try:
            target.unlink(missing_ok=True)
            try:
                folder.rmdir()
            except OSError:
                pass
            service_log.event(
                'tos', '角色照片已转存 TOS 并删除本地缓存',
                context=f'project={pid} asset={aid}')
        except OSError as error:
            service_log.event(
                'tos', '角色照片本地缓存删除失败',
                context=f'project={pid} asset={aid} error={type(error).__name__}',
                level='error')
    service_log.event('file', '上传角色照片', context=f"project={pid} role={role_id}")
    return _response({
        'asset_id': aid, 'url': url, 'check_status': 'checking',
        'storage': 'tos' if stored_in_tos else 'local',
    },code=201)

@app.get('/api/v2/assets/<aid>')
def asset_file(aid):
    if not re.fullmatch(r'asset_[0-9a-f]{32}',aid):
        return _response(None,'文件不存在',404)
    if hasattr(store,'asset_project'):
        pid=store.asset_project(aid)
        project = store.get(pid) if pid else None
        if project and (project.get('status') == 'refunded'
                        or project.get('refund_status') == 'succeeded'
                        or project.get('status') == 'expired'
                        or retention_expired(project)):
            return _response(None,'作品已因退款归档',404)
        matches=[UPLOAD_ROOT/pid/(aid+'.jpg')] if pid else []
        matches=[p for p in matches if p.is_file()]
    else:
        matches=list(UPLOAD_ROOT.glob('p2_*/'+aid+'.jpg'))
    if matches:
        return send_file(matches[0])
    try:
        return redirect(storage_client.asset_url(aid), code=302)
    except Exception:
        service_log.event(
            'tos', 'TOS 角色照片读取失败',
            context=f'asset={aid}', level='error')
        return _response(None,'文件不存在',404)

@app.post('/api/v2/projects/<pid>/order')
def create_order(pid):
    mode = wechat_pay.payment_mode()
    if mode in ('disabled', 'unconfigured'):
        return _response(None,'支付功能已关闭，暂时无法下单',503)
    body=request.get_json(silent=True) or {}
    with store.transaction() as db:
        p=store.get(pid,db)
        if not p:
            return _response(None,'制作记录不存在',404)
        if retention_expired(p):
            return _response(None,'制作记录已超过数据保留期，请重新选择模板',409)
        if p.get('order_no'):
            return _response(_view(p))
        if not orchestrator.enabled and os.getenv('APP_ENV') in ('local','local-real'):
            return _response(None,'AI制作服务未启用，当前不能开始制作',503)
        agreement_version = os.getenv('PHASE2_AGREEMENT_VERSION', 'travel-photo-v1')
        if not body.get('agreement') or body.get('agreement_version') != agreement_version:
            return _response(None,'请阅读并确认照片授权与制作说明',422)
        required = user_roles(p)
        if not required or any(r.get('check_status')!='passed' for r in required):
            return _response(None,'请等待全部角色照片检查通过',422)
        if orchestrator.enabled and any(r.get('check_mode')!='real' for r in required):
            for r in required:
                r['check_status']='checking'
            store.put(p,db)
            return _response(None,'已启用真实制作，照片需要重新检查',409)
        if LOCAL_REAL and os.getenv('LOCAL_CLOUD_ENABLED')!='true':
            return _response(None,'尚未启用真实云端调用',503)
        try:
            runtime=build_runtime_template(p['template'])
            validate_selection(p, runtime)
        except (OSError,ValueError) as error:
            return _response(None,'模板制作素材不完整：'+str(error),422)
        try:
            price_fen=price_for_template(p['template'])
        except ValueError as error:
            return _response(None,str(error),422)
        runtime['template_code']=p['runtime_code']
        subscribe=bool(body.get('subscribe'))
        subscribe_openid=auth.openid() if subscribe else ''
        subscribe_template=wechat_subscribe.TEMPLATE_ID if subscribe else ''
        free_order = False
        if mode in ('wechat', 'allinpay'):
            try:
                client = allinpay.Allinpay() if mode == 'allinpay' else wechat_pay.WeChatPay()
                client.validate()
            except (wechat_pay.WeChatPayError, allinpay.AllinpayError) as error:
                return _response(None, str(error), 503)
            free_order = free_quota.consume(store, db)
        if free_order:
            p.update(order_no='FREE'+uuid.uuid4().hex[:16].upper(),status='portrait_submit',stage='portrait',
                     payment_mode='free',payment_status='paid',paid_at=time.time(),amount_paid=0,
                     amount_fen=0,list_price_fen=price_fen,
                     agreement_version=body['agreement_version'],agreement_at=time.time(),
                     runtime_snapshot=runtime,title='免费名额 · 准备制作',progress=0,
                     subscribe_openid=subscribe_openid,subscribe_template=subscribe_template,subscribe_sent=False)
        elif mode in ('wechat', 'allinpay'):
            p.update(order_no=wechat_pay.generate_order_no(),status='pending_payment',stage='portrait',
                     payment_mode=mode,payment_status='unpaid',amount_fen=price_fen,amount_paid=0,
                     list_price_fen=price_fen,
                     agreement_version=body['agreement_version'],agreement_at=time.time(),
                     runtime_snapshot=runtime,title='等待支付',progress=0,
                     subscribe_openid=subscribe_openid,subscribe_template=subscribe_template,subscribe_sent=False)
        else:
            p.update(order_no='DEV'+uuid.uuid4().hex[:16].upper(),status='portrait_submit',stage='portrait',
                     payment_mode='dev-bypass',payment_status='paid',paid_at=time.time(),amount_paid=0,amount_fen=0,
                     list_price_fen=price_fen,
                     agreement_version=body['agreement_version'],agreement_at=time.time(),
                     runtime_snapshot=runtime,title='已跳过付款，准备制作',progress=0,
                     subscribe_openid=subscribe_openid,subscribe_template=subscribe_template,subscribe_sent=False)
        store.put(p,db)
        selection_sql='INSERT IGNORE' if hasattr(store,'engine') else 'INSERT OR IGNORE'
        db.execute(selection_sql+' INTO phase2_template_selections(template_code,owner_hash,created) VALUES(?,?,?)',
            (p['template_code'],hashlib.sha256(auth.user().encode()).hexdigest(),time.time()))
    service_log.event('payment', f"创建订单 order_no={p['order_no']} 方式={p['payment_mode']}",
                      context=f"project={pid}")
    return _response(_view(p),code=201)

@app.post('/api/v2/projects/<pid>/prepay')
def prepay(pid):
    if wechat_pay.payment_mode() in ('disabled', 'unconfigured'):
        return _response(None,'支付功能已关闭，暂时无法发起支付',503)
    project = store.get(pid)
    if project and project.get('payment_mode') == 'allinpay':
        return allinpay_prepay(pid)
    if wechat_pay.payment_mode() != 'wechat':
        return _response(None,'当前支付方式不支持预下单',503)
    p = store.get(pid)
    if not p:
        return _response(None,'制作记录不存在',404)
    if not p.get('order_no') or p.get('payment_mode') != 'wechat':
        return _response(None,'订单尚未创建',409)
    if p.get('payment_status') == 'paid' or p.get('paid_at'):
        return _response(_view(p))
    openid = auth.openid()
    if not openid:
        return _response(None,'缺少微信 OpenID，请重新登录',422)
    try:
        client = wechat_pay.WeChatPay()
        client.validate()
    except wechat_pay.WeChatPayError as error:
        return _response(None, str(error), 503)
    description = (p.get('template') or {}).get('video_name') or '贺见旅拍AI旅拍影片'
    try:
        prepay_id = client.prepay(p['order_no'], p['amount_fen'], openid, description)
    except wechat_pay.WeChatPayError as error:
        return _response(None, str(error), 502)
    store.update(pid, {'prepay_id': prepay_id, 'payment_status': 'paying'})
    service_log.event('payment', f"预下单成功 order_no={p['order_no']}",
                      context=f"project={pid}")
    return _response({'order_no': p['order_no'], 'amount_fen': p['amount_fen'],
                      'prepay_id': prepay_id, 'pay_params': client.client_pay_params(prepay_id)})


def allinpay_prepay(pid):
    # Serialize cashier creation; callbacks use row transactions independently.
    with store.creation_lock(pid):
        p = store.get(pid)
        if p.get('payment_status') == 'paid':
            return _response({'payment_status': 'paid', 'project_id': pid})
        if p.get('status') != 'pending_payment' or not p.get('order_no'):
            return _response(None, '当前订单不可支付', 409)
        checkout = p.get('allinpay_checkout')
        if checkout and time.time() - p.get('allinpay_checkout_at', 0) < 300:
            return _response(checkout)
        try:
            client = allinpay.Allinpay()
            client.validate()
            checkout = client.prepay(p['order_no'], p['amount_fen'], p['template'].get('video_name') or '贺见旅拍AI旅拍')
        except allinpay.AllinpayError as error:
            return _response(None, str(error), 502)
        with store.transaction() as db:
            current = store.get(pid, db)
            if current.get('payment_status') == 'paid':
                return _response({'payment_status': 'paid', 'project_id': pid})
            if current.get('status') != 'pending_payment':
                return _response(None, '订单状态已变化，请刷新', 409)
            current.update(allinpay_checkout=checkout, allinpay_checkout_at=time.time(),
                           trade_order_no=checkout.get('trade_order_no'), payment_status='paying')
            store.put(current, db)
        return _response(checkout)


@app.route('/api/v2/payments/allinpay/notify', methods=['GET', 'POST'])
def allinpay_notify():
    data = request.values.to_dict(flat=True) or request.get_json(silent=True) or {}
    try:
        notice = allinpay.Allinpay().parse_notify(data)
    except allinpay.AllinpayError:
        return 'error', 400
    project = store.find_by_order_no(notice['order_no'])
    if not project:
        return 'error', 400
    with store.transaction() as db:
        p = store.get(project['project_id'], db)
        if not p or p.get('payment_mode') != 'allinpay':
            return 'error', 400
        if notice['success'] and notice['amount_fen'] != p.get('amount_fen'):
            return 'error', 400
        if p.get('payment_status') == 'paid':
            return 'success', 200
        if not notice['success']:
            return 'success', 200
        if p.get('status') != 'pending_payment':
            return 'error', 409
        p.update(payment_status='paid', transaction_id=notice['transaction_id'],
                 amount_paid=notice['amount_fen'], paid_at=time.time(), updated_at=time.time(),
                 status='portrait_submit', stage='portrait', title='支付成功，准备制作',
                 progress=0, next_run_at=0)
        p.pop('allinpay_checkout', None)
        store.put(p, db)
    service_log.event('payment', '通联支付确认成功', context=f"project={project['project_id']}")
    return 'success', 200

@app.post('/api/v2/payments/callback')
def reserved_payment_callback():
    return _response(None,'支付接口待接入',501)

@app.post('/api/v2/payments/notify')
def wechat_pay_notify():
    raw = request.get_data()
    try:
        client = wechat_pay.WeChatPay()
        data = client.parse_notify(request.headers, raw)
    except wechat_pay.WeChatPayError as error:
        app.logger.warning('微信支付回调处理失败: %s', error)
        return jsonify({'code': 'FAIL', 'message': str(error)}), 400
    if data.get('event_type') != 'TRANSACTION.SUCCESS':
        return jsonify({'code': 'SUCCESS', 'message': '已收到'}), 200
    out_trade_no = data.get('out_trade_no')
    transaction_id = data.get('transaction_id')
    paid_total = (data.get('amount') or {}).get('total')
    if not out_trade_no or not transaction_id or paid_total is None:
        return jsonify({'code': 'FAIL', 'message': '回调字段不完整'}), 400
    project = store.find_by_order_no(out_trade_no)
    if not project:
        return jsonify({'code': 'FAIL', 'message': '订单不存在'}), 400
    if project.get('payment_status') == 'paid':
        return jsonify({'code': 'SUCCESS', 'message': '订单已处理'}), 200
    if int(paid_total) != int(project.get('amount_fen') or 0):
        return jsonify({'code': 'FAIL', 'message': '支付金额校验失败'}), 400
    store.update(project['project_id'], {'payment_status': 'paid', 'transaction_id': transaction_id,
        'paid_at': time.time(), 'status': 'portrait_submit', 'stage': 'portrait',
        'title': '支付成功，准备制作', 'progress': 0, 'prepay_id': ''})
    service_log.event('payment', f"支付回调成功 order_no={out_trade_no}",
                      context=f"project={project['project_id']}")
    return jsonify({'code': 'SUCCESS', 'message': '成功'}), 200

@app.post('/api/v2/projects/<pid>/retry')
def retry_project(pid):
    with store.transaction() as db:
        p = store.get(pid, db)
        if not p:
            return _response(None, '制作记录不存在', 404)
        if p.get('status') != 'terminated':
            return _response(None, '当前状态不可重新生成', 409)
        if not orchestrator.enabled and os.getenv('APP_ENV') in ('local','local-real'):
            return _response(None, 'AI制作服务未启用，当前不能重新生成', 503)
        refund = store.refund_for_project(pid, db)
        if refund:
            return _response(None, '退款已进入处理，不能重新生成', 409)
        stage = p.get('stage') or 'portrait'
        patch = {'run_id': pid + '-r' + str(int(time.time())), 'attempt': 1, 'error': '',
                 'title': '正在重新生成', 'description': '已重新发起生成，请稍候', 'progress': 0,
                 'next_run_at': 0, 'refund_status': '', 'submission_at': time.time()}
        if stage == 'video':
            patch.update(status='video_submit', video_task_id='')
        else:
            patch.update(status='portrait_submit', character_task_id='', portrait_url='',
                         portrait_saved=False, portrait_source_url='', portrait_asset_id='',
                         portrait_version='', role_mapping=[])
        p.update(patch, updated_at=time.time())
        store.put(p, db)
    return _response(_view(store.get(pid)))

@app.post('/api/v2/projects/<pid>/refund')
def refund_project(pid):
    p = store.get(pid)
    if not p:
        return _response(None, '制作记录不存在', 404)
    if p.get('status') == 'completed':
        return _response(None, '作品已完成，不支持退款', 409)
    if p.get('status') == 'refund_pending':
        return _response(_view(p))
    if p.get('status') not in ('terminated',):
        return _response(None, '只有生成失败的订单可以申请退款', 409)
    if p.get('payment_mode') in ('dev-bypass', 'free'):
        with store.transaction() as db:
            current = store.get(pid, db)
            if current.get('status') != 'terminated':
                return _response(None, '制作状态已变化，请刷新后重试', 409)
            current.update(status='archived', refund_status='not_charged',
                           title='已取消，未产生扣款',
                           description='生成失败未产生费用，本次制作已取消',
                           updated_at=time.time())
            store.put(current, db)
        p = current
    else:
        with store.transaction() as db:
            current = store.get(pid, db)
            if current.get('status') != 'terminated':
                return _response(None, '制作状态已变化，请刷新后重试', 409)
            if store.refund_for_project(pid, db):
                return _response(None, '退款已进入处理，不能重复申请', 409)
            current.update(status='refund_pending', refund_status='requested',
                           updated_at=time.time())
            store.put(current, db)
        orchestrator.refund_service.ensure_requested(pid)
        p = store.get(pid)
    return _response(_view(store.get(pid)))

@app.get('/api/v2/works')
def works():
    limit=max(1,min(100,request.args.get('limit',30,type=int)))
    offset=max(0,request.args.get('offset',0,type=int))
    rows,total=store.list_works(auth.user(),limit,offset)
    items=[_view(p) for p in rows]
    unread={r['project_id'] for r in store.unread(auth.user())}
    for item in items:
        item['unread']=item['project_id'] in unread
    return _response({'items':items,'total':total,'next_offset':offset+len(items) if offset+len(items)<total else None})


@app.get('/api/v2/works/watch')
def works_watch():
    """Lightweight foreground state feed without signing media URLs."""
    limit=max(1,min(100,request.args.get('limit',100,type=int)))
    items,total=store.list_watch_states(auth.user(),limit)
    return _response({'items':items,'total':total})

@app.get('/api/v2/messages')
def messages():
    items=store.unread(auth.user())
    return _response({'items':items,'total':len(items)})

@app.post('/api/v2/works/status')
def works_status():
    ids=(request.get_json(silent=True) or {}).get('ids')
    if not isinstance(ids,list) or len(ids)>30 or not all(isinstance(x,str) and re.fullmatch(r'p2_[A-Za-z0-9_-]{1,64}',x) for x in ids):
        return _response(None,'每次最多查询 30 条有效作品编号',422)
    return _response({'items':[_view(p) for p in store.works_by_ids(auth.user(),ids)]})

@app.post('/api/v2/projects/<pid>/read')
def mark_read(pid):
    with store.connection() as db:
        db.execute('UPDATE events SET seen=1 WHERE project_id=? AND seen=0',(pid,))
    return _response({})

auth.install(app, lambda:store)

if __name__ == '__main__':
    orchestrator.start()
    from common.service_app import serve
    serve(app,PORT)
