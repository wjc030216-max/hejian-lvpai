"""Shared Flask contract for all image-generation entry points."""
from __future__ import annotations

import base64
import json
import os
import uuid
import requests
from flask import Flask, jsonify, request

from common.seedream_provider import generate_seedream, SEEDREAM_MIN_SIZE
from common import storage_client

_app_env = os.getenv("APP_ENV", "")
if _app_env == "local-real":
    if not os.getenv("TOS_SERVICE_URL", "").startswith("http://tos-storage:"):
        raise RuntimeError("local-real Seedream 服务必须使用本地 TOS 服务")
if _app_env == "production":
    from ipaddress import ip_address
    from urllib.parse import urlparse
    tos_url = urlparse(os.getenv("TOS_SERVICE_URL", ""))
    if (tos_url.scheme != "http" or tos_url.path or tos_url.username
            or not tos_url.hostname or not ip_address(tos_url.hostname).is_private):
        raise RuntimeError("正式环境 Seedream 服务必须使用本次部署的私网 TOS 服务")


def _api_error(message: str, status: int = 400):
    return jsonify({"code": status, "message": message}), status


def _upload_generated_image(user_id: str, image_b64: str) -> str:
    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except Exception as exc:
        raise RuntimeError("生成图片解码失败") from exc
    if not image_bytes:
        raise RuntimeError("生成服务返回空图片")
    if image_bytes.startswith(b"\x89PNG"):
        suffix, mime = "png", "image/png"
    elif image_bytes.startswith(b"\xff\xd8\xff"):
        suffix, mime = "jpg", "image/jpeg"
    elif image_bytes[:4] == b"RIFF":
        suffix, mime = "webp", "image/webp"
    else:
        suffix, mime = "png", "image/png"
    asset_id = "asset_" + uuid.uuid4().hex
    return storage_client.upload_asset_bytes(
        asset_id, image_bytes, f"{asset_id}.{suffix}", mime)


def create_app(service_name: str = "seedream_image") -> Flask:
    app = Flask(service_name)
    from common.service_app import protect
    protect(app, {'phase2-image'}, public={'/api/v1/images/health'})

    @app.post("/api/v1/images/generate")
    def generate_image():
        body = request.get_json(silent=True) or {}
        user_id = body.get("user_id")
        prompt = body.get("prompt")
        refs = body.get("reference_image_urls")
        single = body.get("reference_image_url") or body.get("reference_url")
        if single and not refs:
            refs = [single]
        if not isinstance(user_id, str) or not user_id.strip():
            return _api_error("user_id 不能为空")
        if not isinstance(prompt, str) or not prompt.strip():
            return _api_error("prompt 不能为空")
        if refs is not None and (not isinstance(refs, list) or not refs):
            return _api_error("reference_image_urls 必须是 URL 数组且不能为空")
        requested_size = str(body.get("size") or os.getenv("SEEDREAM_MIN_SIZE", SEEDREAM_MIN_SIZE)).upper()
        if requested_size not in ("1K", "1.5K", "2K"):
            requested_size = os.getenv("SEEDREAM_MIN_SIZE", SEEDREAM_MIN_SIZE)
        try:
            provider_response = generate_seedream(
                prompt, refs,
                size=requested_size,
                aspect_ratio=body.get("aspect_ratio"),
            )
            image_url = _upload_generated_image(
                user_id, provider_response["data"][0]["b64_json"]
            )
            from common import service_log
            service_log.event("image", "生图转存完成", context=json.dumps(
                {"url": image_url}, ensure_ascii=False))
            return jsonify({
                "code": 0,
                "message": "success",
                "data": {
                    "mode": "image_to_image" if refs else "text_to_image",
                    "image_url": image_url,
                    "size": requested_size,
                    "output_size": provider_response.get("size"),
                    "aspect_ratio": body.get("aspect_ratio"),
                    "request_id": uuid.uuid4().hex[:8],
                    "reference_images_used": len(refs or []),
                },
            })
        except ValueError as exc:
            return _api_error(str(exc), 400)
        except RuntimeError as exc:
            from common.task_journal import CloudFailed
            from common.error_detail import exception_detail
            from common import service_log
            service_log.event("image", "图片生成异常", level="warning",
                              context=exception_detail(exc))
            if isinstance(exc, CloudFailed):
                return _api_error(str(exc), 422)
            app.logger.exception("图片生成请求失败")
            return _api_error(str(exc), 502)
        except requests.RequestException as exc:
            from common.error_detail import exception_detail
            from common import service_log
            service_log.event("image", "图片生成网络异常", level="warning",
                              context=exception_detail(exc))
            app.logger.exception("图片生成请求失败")
            return _api_error("图片生成服务暂时不可用，请稍后重试", 502)

    @app.get("/api/v1/images/health")
    def health_check():
        return jsonify({"status": "ok", "service": service_name})

    if os.getenv('PHASE2_DURABLE_TASKS') == 'true':
        from common.durable_images import install
        install(app, generate_seedream, _upload_generated_image)
    return app
