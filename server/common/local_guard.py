"""Fail closed for the dedicated local stack; other deployments are unchanged."""
import os
from urllib.parse import urlsplit


def require_cloud_enabled():
    if os.getenv("APP_ENV") in ("local-real", "staging", "production") and os.getenv("LOCAL_CLOUD_ENABLED") != "true":
        raise RuntimeError("Cloud calls are disabled. Configure dedicated credentials before using this environment.")


def validate_media_url(url):
    if os.getenv("APP_ENV") not in ("local-real", "staging", "production"):
        return
    if not isinstance(url, str):
        raise ValueError("Generation only accepts this installation's media URLs")
    base = os.environ["EXTERNAL_DOMAIN"].rstrip("/") + "/"
    if url.startswith(base):
        return
    api_base = (os.getenv("PUBLIC_API_BASE_URL") or "").rstrip("/") + "/"
    if api_base != "/" and url.startswith(api_base):
        parsed = urlsplit(url)
        local_auth = os.getenv("PHASE2_AUTH_MODE", "").strip().lower() == "local"
        if (parsed.path.startswith(("/api/v2/assets/", "/api/v2/portraits/", "/api/v2/results/"))
                and ("access=" in parsed.query or local_auth)):
            return
    raise ValueError("Generation only accepts this installation's media URLs")
