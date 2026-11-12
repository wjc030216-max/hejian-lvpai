"""Short-lived local service tokens, independent from cloud credentials."""
import base64
import hashlib
import hmac
import json
import os
import time


def local_headers(subject):
    def enc(value):
        return base64.urlsafe_b64encode(value).rstrip(b'=')
    header = enc(b'{"alg":"HS256","typ":"JWT"}')
    payload = enc(json.dumps({'sub': subject, 'iat': int(time.time()), 'exp': int(time.time()) + 300}).encode())
    message = header + b'.' + payload
    signature = enc(hmac.new(os.environ['AITRAVEL_JWT_SECRET'].encode(), message, hashlib.sha256).digest())
    headers = {'Authorization': 'Bearer ' + (message + b'.' + signature).decode()}
    try:
        from common.service_log import current_context
        request_id = current_context().get('request_id')
        if request_id:
            headers['X-Request-ID'] = request_id
    except Exception:
        pass
    return headers
