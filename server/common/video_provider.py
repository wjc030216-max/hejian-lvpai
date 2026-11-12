"""Seedance 2.0 mini and its own-account asset library; no phase-one defaults."""
import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import time
from urllib.parse import quote
import requests
from volcenginesdkarkruntime import Ark
from common.template_client import runtime_model, runtime_template
from common.local_guard import require_cloud_enabled, validate_media_url
from common import http_client
logger=logging.getLogger(__name__)
SERVICE='ark'
REGION='cn-beijing'
HOST='ark.cn-beijing.volcengineapi.com'
CONTENT_TYPE='application/json'
PATH='/'
VERSION='2024-01-01'
ASSET_POLL_TIMEOUT=120
ASSET_POLL_INTERVAL=5
ASSET_GROUP_ID=os.getenv('SEEDANCE_ASSET_GROUP_ID','')
ASSET_PROJECT_NAME=os.getenv('SEEDANCE_ASSET_PROJECT_NAME','default')
SEEDANCE_ASSET_ACCESS_KEY_ID=os.getenv('SEEDANCE_ASSET_ACCESS_KEY_ID','')
SEEDANCE_ASSET_ACCESS_KEY_SECRET=os.getenv('SEEDANCE_ASSET_ACCESS_KEY_SECRET','')
SEEDANCE_API_KEY=os.getenv('SEEDANCE_API_KEY','')
VOLC_MODEL=os.getenv('SEEDANCE_MODEL','doubao-seedance-2-0-fast-260128').strip()
VIDEO_RATIO='9:16'
VIDEO_DURATION=15
VIDEO_GENERATE_AUDIO=True
VIDEO_WATERMARK=True

def norm_query(params: dict) -> str:
    query = ""
    for key in sorted(params.keys()):
        if isinstance(params[key], list):
            for k in params[key]:
                query += quote(key, safe="-_.~") + "=" + quote(str(k), safe="-_.~") + "&"
        else:
            query += quote(key, safe="-_.~") + "=" + quote(str(params[key]), safe="-_.~") + "&"
    return query[:-1].replace("+", "%20")

def hmac_sha256(key: bytes, content: str) -> bytes:
    return hmac.new(key, content.encode("utf-8"), hashlib.sha256).digest()

def hash_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def request_api(method: str, date: datetime.datetime, query: dict, header: dict,
                ak: str, sk: str, body: str = "",
                action: str = None, version: str = None,
                timeout: int = 60) -> requests.Response:
    if not ak or not sk:
        raise RuntimeError("火山 OpenAPI 凭证未设置 (SEEDANCE_ASSET_ACCESS_KEY_ID / SEEDANCE_ASSET_ACCESS_KEY_SECRET)")

    final_action = action if action else "GetAsset"
    final_version = version if version else VERSION

    credential = {
        "access_key_id": ak,
        "secret_access_key": sk,
        "service": SERVICE,
        "region": REGION,
    }

    final_query = {"Action": final_action, "Version": final_version}
    if query:
        final_query.update(query)

    request_param = {
        "body": body if body else "",
        "host": HOST,
        "path": PATH,
        "method": method.upper(),
        "content_type": CONTENT_TYPE,
        "date": date,
        "query": final_query,
    }

    x_date = request_param["date"].strftime("%Y%m%dT%H%M%SZ")
    short_x_date = x_date[:8]
    x_content_sha256 = hash_sha256(request_param["body"])

    sign_result = {
        "Host": request_param["host"],
        "X-Content-Sha256": x_content_sha256,
        "X-Date": x_date,
        "Content-Type": request_param["content_type"],
    }

    signed_headers_str = ";".join(["content-type", "host", "x-content-sha256", "x-date"])

    canonical_request_str = "\n".join([
        request_param["method"],
        request_param["path"],
        norm_query(request_param["query"]),
        "\n".join([
            "content-type:" + request_param["content_type"],
            "host:" + request_param["host"],
            "x-content-sha256:" + x_content_sha256,
            "x-date:" + x_date,
        ]),
        "",
        signed_headers_str,
        x_content_sha256,
    ])

    hashed_canonical_request = hash_sha256(canonical_request_str)
    credential_scope = "/".join([short_x_date, credential["region"], credential["service"], "request"])

    string_to_sign = "\n".join(["HMAC-SHA256", x_date, credential_scope, hashed_canonical_request])

    k_date = hmac_sha256(credential["secret_access_key"].encode("utf-8"), short_x_date)
    k_region = hmac_sha256(k_date, credential["region"])
    k_service = hmac_sha256(k_region, credential["service"])
    k_signing = hmac_sha256(k_service, "request")
    signature = hmac_sha256(k_signing, string_to_sign).hex()

    sign_result["Authorization"] = "HMAC-SHA256 Credential={}, SignedHeaders={}, Signature={}".format(
        credential["access_key_id"] + "/" + credential_scope,
        signed_headers_str,
        signature,
    )

    final_headers = header.copy() if header else {}
    final_headers.update(sign_result)

    response = http_client.session().request(
        method=request_param["method"],
        url="https://{}{}".format(request_param["host"], request_param["path"]),
        headers=final_headers,
        params=request_param["query"],
        data=request_param["body"],
        timeout=timeout
    )
    return response

def get_asset_status(asset_id: str):
    logger.debug("🔍 查询资产状态: asset_id=%s", asset_id)
    body = json.dumps({"Id": asset_id, "ProjectName": ASSET_PROJECT_NAME})
    date = datetime.datetime.utcnow()
    resp = request_api(
        method="POST",
        date=date,
        query={},
        header={},
        ak=SEEDANCE_ASSET_ACCESS_KEY_ID,
        sk=SEEDANCE_ASSET_ACCESS_KEY_SECRET,
        body=body,
        action="GetAsset",
        version=VERSION
    )
    if resp.status_code != 200:
        from common.error_detail import http_detail
        detail = http_detail(resp)
        logger.warning("⚠️ GetAsset HTTP 失败: %s", detail)
        service_log.event("asset", "素材状态查询失败", level="warning", context=detail)
        return "", "", detail
    result = resp.json()
    status = result.get("Result", {}).get("Status") or result.get("Status")
    url = result.get("Result", {}).get("URL") or result.get("URL")
    error = result.get("Result", {}).get("Error") or result.get("Error")
    logger.debug("资产状态: asset_id=%s, status=%s", asset_id, status)
    return status, url, error

def wait_asset_active(asset_id: str) -> str:
    from common.task_journal import TaskPending, CloudFailed
    from common import service_log
    status, url, error = get_asset_status(asset_id)
    service_log.event("asset", "素材状态轮询",
                      context=f"asset_id={asset_id} status={status or '-'} error={error or '-'}")
    if status == 'Active' and url:
        service_log.event("asset", "素材已激活", context=f"asset_id={asset_id}")
        return url
    if status in ('Failed', 'Deleted', 'Expired'):
        service_log.event("asset", "素材状态失败", context=f"asset_id={asset_id} status={status}")
        raise CloudFailed('视频参考素材不可用')
    if not status:
        raise RuntimeError('素材状态查询失败')
    raise TaskPending(5)


def call_volcano_video(content, generate_audio=True, ratio="9:16", duration=15, watermark=False):
    logger.info("🎬 提交火山视频生成任务: ratio=%s, duration=%d, audio=%s", ratio, duration, generate_audio)
    start_time = time.time()
    
    if not SEEDANCE_API_KEY:
        raise RuntimeError("视频生成服务密钥未配置")
    import json
    from common import service_log
    texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    refs = [c.get("image_url", {}).get("url", "") for c in content
            if isinstance(c, dict) and c.get("type") == "image_url"]
    service_log.event("video", "视频生成请求", context=json.dumps(
        {"ratio": ratio, "duration": duration, "audio": generate_audio,
         "prompt_sha256": hashlib.sha256(" ".join(texts).encode("utf-8")).hexdigest(),
         "prompt_chars": len(" ".join(texts)), "reference_count": len(refs)},
        ensure_ascii=False))
    client = Ark(base_url="https://ark.cn-beijing.volces.com/api/v3", api_key=SEEDANCE_API_KEY, max_retries=0, timeout=30)
    try:
        create_result = client.content_generation.tasks.create(
            model=VOLC_MODEL,
            content=content,
            generate_audio=generate_audio,
            ratio=ratio,
            duration=duration,
            watermark=watermark,
        )
    except Exception as error:
        from common.error_detail import exception_detail
        service_log.event('video', '视频任务提交失败', level='warning', context=exception_detail(error))
        raise
    elapsed = time.time() - start_time
    logger.info("✅ 火山任务提交成功: task_id=%s, 耗时=%.2fs", create_result.id, elapsed)
    from common import service_log
    service_log.event("video", "视频任务提交响应", context=json.dumps(
        {"task_id": create_result.id, "elapsed": round(elapsed, 2)}, ensure_ascii=False))
    return create_result.id

def poll_volcano_task(volcano_task_id: str) -> str:
    from common.task_journal import TaskPending, CloudFailed
    from common import service_log
    client = Ark(base_url='https://ark.cn-beijing.volces.com/api/v3', api_key=SEEDANCE_API_KEY,
                 max_retries=0, timeout=30)
    try:
        result = client.content_generation.tasks.get(task_id=volcano_task_id)
    except Exception as error:
        from common.error_detail import exception_detail
        service_log.event('video', '视频任务查询失败', level='warning', context=exception_detail(error))
        raise
    if result.status == 'succeeded':
        content = getattr(result, 'content', None)
        url = getattr(content, 'video_url', None)
        if not url and hasattr(result, 'to_dict'):
            url = (result.to_dict().get('content') or {}).get('video_url')
        if not url:
            raise RuntimeError('云端已完成但没有视频地址')
        service_log.event("video", "视频轮询完成", context=json.dumps(
            {"task_id": volcano_task_id, "has_url": bool(url)}, ensure_ascii=False))
        return url
    if result.status in ('failed', 'cancelled', 'expired'):
        reason = getattr(result, 'error', None)
        if not reason and hasattr(result, 'to_dict'):
            data = result.to_dict() or {}
            reason = data.get('error') or data.get('failure_reason') or data.get('fail_reason')
        code = ''
        if isinstance(reason, dict):
            code = str(reason.get('code') or reason.get('type') or '').strip()
        elif reason:
            code = str(reason).strip()
        from common import service_log as _log
        _log.event('video', '视频任务云端失败', level='warning',
                   context=f"task={volcano_task_id} status={result.status} code={code or '-'} reason={str(reason)[:300]}")
        logger.warning(
            "视频任务确认失败 task_id=%s status=%s reason=%s",
            volcano_task_id, result.status, str(reason)[:1000],
        )
        from common.ark_errors import ark_error_message
        message, _recoverable = ark_error_message("视频制作", 0, code)
        service_log.event("video", message, context=f"task_id={volcano_task_id} status={result.status}")
        raise CloudFailed(message)
    raise TaskPending(15)


def fetch_template_prompt(template_code):
    runtime,ids,prompt=runtime_template(template_code)
    return prompt,runtime.get('bgm_url') or '',ids,runtime.get('actor_url') or [],runtime.get('costume_url') or [],runtime.get('screen_url') or [],runtime.get('storyboard_url') or ''


def fetch_template_model(template_code):
    """Return every 15-second group and its outfit-specific references."""
    return runtime_model(template_code)


def _direct_tos_url(image_url):
    """Ark 不跟随 302 时签名会失效；把本机媒体地址直接换成 TOS 签名地址。"""
    import re as _re
    match=_re.search(r'/api/v2/assets/((?:asset|file)_[0-9a-f]{32})', str(image_url or ''))
    if not match:
        return image_url
    try:
        from common.storage_client import asset_url
        return asset_url(match.group(1))
    except Exception:
        return image_url


def _compliant_reference(image_url):
    """Ark rejects reference images whose aspect ratio leaves 0.4..2.5.

    Panoramic scene boards are common in templates, so pad such images onto a
    square white canvas and hand Ark a private asset URL instead of the source.
    """
    try:
        response = requests.get(image_url, timeout=(5, 25))
        response.raise_for_status()
    except requests.RequestException:
        return image_url
    import io as _io
    import uuid
    from PIL import Image
    try:
        image = Image.open(_io.BytesIO(response.content))
    except Exception:
        return image_url
    ratio = image.width / max(1, image.height)
    if 0.4 <= ratio <= 2.5 and max(image.size) <= 2048:
        return image_url
    side = min(1280, max(image.size))
    canvas = Image.new('RGB', (side, side), (255, 255, 255))
    picture = image.convert('RGB')
    picture.thumbnail((side, side), Image.LANCZOS)
    canvas.paste(picture, ((side - picture.width) // 2, (side - picture.height) // 2))
    buffer = _io.BytesIO()
    canvas.save(buffer, format='JPEG', quality=88)
    import hashlib
    from common import storage_client
    from common.media_refs import media_token
    asset_id = 'asset_' + hashlib.md5(str(image_url).split('?')[0].encode()).hexdigest()
    storage_client.upload_asset_bytes(asset_id, buffer.getvalue(),
                                      'reference.jpg', 'image/jpeg')
    return _direct_tos_url('/api/v2/assets/' + asset_id)


def create_asset(image_url):
    require_cloud_enabled(); validate_media_url(image_url)
    image_url = _direct_tos_url(image_url)
    image_url = _compliant_reference(image_url)
    if not ASSET_GROUP_ID.startswith('group-'): raise ValueError('未设置视频素材组')
    from common import service_log
    service_log.event("asset", "素材库注册请求", context=json.dumps(
        {"url_scheme": str(image_url).split(":")[0] if isinstance(image_url, str) else "?"},
        ensure_ascii=False))
    payload=json.dumps({'GroupId':ASSET_GROUP_ID,
        'ProjectName':ASSET_PROJECT_NAME,'URL':image_url,'AssetType':'Image'})
    response=request_api('POST',datetime.datetime.utcnow(),{}, {},SEEDANCE_ASSET_ACCESS_KEY_ID,
        SEEDANCE_ASSET_ACCESS_KEY_SECRET, payload, action='CreateAsset')
    if response.status_code >= 500:
        from common import service_log
        service_log.event('asset','素材注册失败，重试一次', level='warning',
                          context=f'status={response.status_code}')
        time.sleep(2)
        response=request_api('POST',datetime.datetime.utcnow(),{}, {},SEEDANCE_ASSET_ACCESS_KEY_ID,
            SEEDANCE_ASSET_ACCESS_KEY_SECRET, payload, action='CreateAsset')
    if response.status_code == 404:
        try:
            error_code = ((response.json() or {}).get('ResponseMetadata') or {}).get('Error', {}).get('Code', '')
        except (ValueError, AttributeError):
            error_code = ''
        if error_code.startswith('NotFound.group_id'):
            raise ValueError(
                '视频素材组不存在，请检查 SEEDANCE_ASSET_GROUP_ID；'
                '不会自动重复提交视频任务')
    if response.status_code >= 400:
        from common.error_detail import http_detail
        detail=http_detail(response)
        service_log.event('asset','素材注册失败', level='warning', context=detail)
        raise RuntimeError('素材注册失败：' + detail)
    body=response.json()
    result=body.get('Result') or body
    asset_id=result.get('Id') or result.get('ID') or result.get('AssetId')
    if not asset_id: raise RuntimeError('素材库未返回素材编号')
    service_log.event("asset", "素材库注册响应", context=json.dumps({"asset_id": asset_id}, ensure_ascii=False))
    return asset_id


def delete_asset(asset_id):
    require_cloud_enabled()
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', str(asset_id or '')):
        raise ValueError('视频素材编号无效')
    response = request_api(
        'POST', datetime.datetime.utcnow(), {}, {},
        SEEDANCE_ASSET_ACCESS_KEY_ID, SEEDANCE_ASSET_ACCESS_KEY_SECRET,
        json.dumps({'Id': asset_id, 'ProjectName': ASSET_PROJECT_NAME}),
        action='DeleteAsset', version=VERSION,
    )
    if response.status_code not in (200, 204, 404):
        raise RuntimeError('视频参考素材删除失败')
    from common import service_log
    service_log.event('asset', '视频参考素材已删除', context=f'asset_id={asset_id}')
