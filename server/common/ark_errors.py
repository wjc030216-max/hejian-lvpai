"""Friendly, model-agnostic error mapping for Volcengine Ark services.

Cloud errors are translated into actionable Chinese messages for operators and
users. Model names and raw upstream payloads are never exposed; full diagnostics
stay in server logs only.
"""


def ark_error_message(kind: str, status_code, code: str = "") -> tuple:
    """Return ``(friendly_message, recoverable)``.

    ``recoverable`` is True for transient failures (rate limit, upstream 5xx,
    network) where a retry may succeed, and False for confirmed rejections that
    will not succeed by retrying.
    """
    code = str(code or "").lower()
    try:
        status = int(status_code or 0)
    except (TypeError, ValueError):
        status = 0

    if status == 401 or "auth" in code or "apikey" in code or "apikeyinval" in code:
        return f"{kind}服务鉴权失败，请检查服务端密钥配置", False
    if status == 403 or "permission" in code or "denied" in code:
        return f"{kind}服务权限不足，请检查账号权限", False
    if status == 404 or "model" in code or "notfound" in code:
        return f"{kind}模型不可用，请检查服务配置", False
    if "quota" in code or "balance" in code or "insufficient" in code:
        return f"{kind}服务额度或余额不足", False
    if status == 429 or "ratelimit" in code or "rpm" in code or "tpm" in code:
        return f"{kind}请求过于频繁，请稍后重试", True
    if status == 413 or "toolarge" in code:
        return f"{kind}素材过大，请压缩后重试", False
    if "content" in code or "sensitive" in code or "moderation" in code or "violation" in code:
        return f"{kind}内容未通过安全审核，请更换素材", False
    if status in (400, 422) or "param" in code or "invalid" in code or "badrequest" in code:
        return f"{kind}请求参数或素材不合法，请检查后重试", False
    if status >= 500 or status == 0:
        return f"{kind}服务暂时不可用，请稍后重试", True
    return f"{kind}生成失败，请稍后重试", True


def ark_error(kind: str, status_code, code: str = "") -> Exception:
    """Build a typed exception from the friendly mapping.

    Recoverable failures (rate limit, upstream 5xx, network) become a plain
    ``RuntimeError`` so callers keep retrying; confirmed rejections become
    ``CloudFailed`` so durable tasks are marked terminal.
    """
    message, recoverable = ark_error_message(kind, status_code, code)
    if recoverable:
        return RuntimeError(message)
    from common.task_journal import CloudFailed
    return CloudFailed(message)
