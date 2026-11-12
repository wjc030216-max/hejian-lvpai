"""Render upstream API errors for logs without leaking signed tokens."""
import re

_TOKEN = re.compile(r'(access=)[^&\s"\']+')


def redact(text, limit=600):
    value = str(text or '')
    value = _TOKEN.sub(r'\1***', value)
    value = value.replace('\n', ' ').replace('\r', ' ')
    return value[:limit]


def http_detail(response, limit=600):
    """Return 'HTTP <status>: <body>' for a requests response."""
    status = getattr(response, 'status_code', '?')
    try:
        body = response.text or ''
    except Exception:
        body = ''
    return f"HTTP {status}: {redact(body, limit)}"


def exception_detail(error, limit=600):
    response = getattr(error, 'response', None)
    if response is not None:
        return f"{type(error).__name__}: {http_detail(response, limit)}"
    return f"{type(error).__name__}: {redact(error, limit)}"
