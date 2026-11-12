"""Remove credentials that may appear in URLs, headers, or exception text."""
import re


_SENSITIVE_QUERY = re.compile(
    r'([?&](?:access|token|signature|X-Amz-Credential|X-Amz-Signature|'
    r'X-Tos-Credential|X-Tos-Signature)=)[^&\s]+',
    re.IGNORECASE,
)
_MEDIA_PATH_TOKEN = re.compile(
    r'(/media/)[A-Za-z0-9_-]{32,}(?=/|[?#\s]|$)',
    re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(
    r'(Authorization:\s*Bearer\s+)[^\s]+',
    re.IGNORECASE,
)


def redact_sensitive_text(value):
    """Return text safe for local files, MySQL logs, and the admin console."""
    text = str(value or '')
    text = _SENSITIVE_QUERY.sub(r'\1<redacted>', text)
    text = _MEDIA_PATH_TOKEN.sub(r'\1<redacted>', text)
    return _BEARER_TOKEN.sub(r'\1<redacted>', text)
