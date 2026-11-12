"""Official Volcengine Ark adapter for Seedream 5.0 Pro.

The model ID and size limits follow the official ModelArk tutorial:
https://www.volcengine.com/docs/82379/1824121 (mainland Ark)
and its BytePlus mirror:
https://docs.byteplus.com/en/docs/ModelArk/seedream-5-0-pro.

The public image-service contract in this project is kept independent from
the provider.  Ark returns an image URL; this adapter downloads it and
normalises it to the legacy ``data[0].b64_json`` shape so existing file
upload code can be reused safely.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from typing import Iterable, Optional

from common import http_client


logger = logging.getLogger(__name__)


ARK_ENDPOINT = os.getenv(
    "SEEDREAM_API_URL",
    "https://ark.cn-beijing.volces.com/api/v3/images/generations",
).rstrip("/")
SEEDREAM_MODEL = os.getenv("SEEDREAM_MODEL", "doubao-seedream-5-0-pro-260628")
# Ark uses `size` for dimensions; `aspect_ratio` is our internal API field.
SEEDREAM_MIN_SIZE = os.getenv("SEEDREAM_MIN_SIZE", "1K")
SEEDREAM_TIMEOUT = float(os.getenv("SEEDREAM_TIMEOUT_SECONDS", "180"))
MAX_REFERENCE_IMAGES = int(os.getenv("SEEDREAM_MAX_REFERENCE_IMAGES", "10"))

# Official Seedream 5.0 Pro size table, ordered 1K / 1.5K / 2K.
RATIO_SIZES = {
    "1:1": ("1024x1024", "1536x1536", "2048x2048"),
    "4:3": ("1152x864", "1792x1344", "2368x1776"),
    "3:4": ("864x1152", "1344x1792", "1776x2368"),
    "16:9": ("1424x800", "2048x1152", "2816x1584"),
    "9:16": ("800x1424", "1152x2048", "1584x2816"),
    "3:2": ("1248x832", "1872x1248", "2496x1664"),
    "2:3": ("832x1248", "1248x1872", "1664x2496"),
    "21:9": ("1568x672", "2352x1008", "3136x1344"),
}


def resolve_image_size(size: str, aspect_ratio: str | None) -> str:
    if not aspect_ratio:
        return size
    if aspect_ratio not in RATIO_SIZES:
        raise ValueError("不支持的图片比例")
    if size not in ("1K", "1.5K", "2K"):
        raise ValueError("指定图片比例时，size 必须为 1K、1.5K 或 2K")
    return RATIO_SIZES[aspect_ratio][("1K", "1.5K", "2K").index(size)]


def _normalise_images(images: Optional[Iterable[str]]) -> list[str]:
    if images is None:
        return []
    if not isinstance(images, (list, tuple)):
        raise ValueError("reference_image_urls 必须是 URL 数组")
    values = [str(item).strip() for item in images if str(item).strip()]
    if len(values) > MAX_REFERENCE_IMAGES:
        raise ValueError(f"参考图最多支持 {MAX_REFERENCE_IMAGES} 张")
    for value in values:
        if not value.startswith(("http://", "https://", "data:image/")):
            raise ValueError("参考图仅支持 HTTP(S) URL 或图片 Data URI")
    return values


def _extract_error_code(body) -> str:
    """Best-effort Ark error code without ever exposing the raw payload."""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or error.get("type") or "").strip()
    return str(body.get("code") or "").strip()


def generate_seedream(prompt: str, reference_images=None, *, size: str | None = None,
                     aspect_ratio: str | None = None, download=True) -> dict:
    """Call Ark's ``images/generations`` endpoint and return a legacy shape."""
    from common.local_guard import require_cloud_enabled, validate_media_url
    require_cloud_enabled()
    from common.provider_credentials import credential
    api_key = credential("SEEDREAM_API_KEY")
    if not api_key:
        raise RuntimeError("服务端未配置 SEEDREAM_API_KEY（图片生成专用）")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt 不能为空")

    images = _normalise_images(reference_images)
    for value in images:
        validate_media_url(value)
    # Explicit pixel dimensions prevent landscape reference images from
    # determining the portrait output orientation.
    requested_ratio = aspect_ratio or os.getenv('SEEDREAM_OUTPUT_RATIO', '')
    requested_size = size or os.getenv("SEEDREAM_MIN_SIZE", SEEDREAM_MIN_SIZE)
    payload = {
        "model": os.getenv("SEEDREAM_MODEL", SEEDREAM_MODEL),
        "prompt": prompt.strip(),
        "size": resolve_image_size(requested_size, requested_ratio),
        "watermark": False,
        "response_format": "url",
    }
    if images:
        # Ark accepts one URL as a string and multi-reference input as an
        # array; using the documented single-image form avoids 400s on edits.
        payload["image"] = images[0] if len(images) == 1 else images
    from common import service_log
    service_log.event("image", "生图请求", context=json.dumps(
        {"size": payload["size"],
         "prompt_sha256": hashlib.sha256(payload["prompt"].encode("utf-8")).hexdigest(),
         "prompt_chars": len(payload["prompt"]), "reference_count": len(images)},
        ensure_ascii=False))
    response = http_client.session().post(
        os.getenv("SEEDREAM_API_URL", ARK_ENDPOINT),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=SEEDREAM_TIMEOUT,
    )
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:1000]}
    if response.status_code >= 400:
        code = _extract_error_code(body)
        logger.warning(
            "图片生成上游失败 status=%s code=%s body=%s",
            response.status_code, code or "-", str(body)[:1000],
        )
        from common.ark_errors import ark_error
        error = ark_error("图片生成", response.status_code, code)
        from common import service_log
        from common.error_detail import redact
        service_log.event("image", str(error),
                          context=f"status={response.status_code} code={code or '-'} body={redact(body, 600)}")
        raise error

    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError("图片生成服务未返回图片")
    item = items[0] or {}
    image_url = item.get("url") or item.get("image_url")
    image_b64 = item.get("b64_json")
    if not download:
        if not image_url:
            raise RuntimeError('图片生成服务未返回可保存的图片地址')
        return {'provider_url':image_url,'model':payload['model'],'size':payload['size']}
    if not image_b64 and image_url:
        image_response = http_client.session().get(image_url, timeout=60)
        image_response.raise_for_status()
        image_b64 = base64.b64encode(image_response.content).decode("ascii")
    if not image_b64:
        raise RuntimeError("图片生成服务未返回可下载图片")
    from common import service_log
    service_log.event("image", "生图响应", context=json.dumps(
        {"size": payload["size"], "bytes": len(image_b64)}, ensure_ascii=False))
    return {
        "created": body.get("created"),
        "model": payload["model"],
        "size": payload["size"],
        "data": [{"b64_json": image_b64}],
        "provider_url": image_url,
        "usage": body.get("usage", {}),
    }

