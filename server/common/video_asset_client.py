"""Internal client for deleting Seedance reference assets during retention."""
import os
import re
from urllib.parse import quote

from common import http_client
from common.internal_auth import local_headers


def delete_video_asset(asset_id):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', str(asset_id or '')):
        raise ValueError('视频素材编号无效')
    base = os.getenv('SEEDDANCE_SERVICE_URL', '').rstrip('/')
    if not base:
        return
    response = http_client.session().delete(
        base + '/api/v1/video/assets/' + quote(asset_id, safe=''),
        headers=local_headers('local-phase2'),
        timeout=(5, 30),
    )
    if response.status_code not in (200, 204, 404):
        raise RuntimeError('视频素材清理失败')
