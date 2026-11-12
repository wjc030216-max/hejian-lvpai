"""Internal storage client: holds no TOS credentials, uploads only local MP4 files."""
import os
import re
import json
import requests
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse
from common.internal_auth import local_headers
from common import http_client
from common import service_log


def _base():
    base = os.getenv('TOS_SERVICE_URL', '').rstrip('/')
    if os.getenv('APP_ENV') in ('staging', 'production'):
        from ipaddress import ip_address
        parsed=urlparse(base)
        if (parsed.scheme!='http' or parsed.path or parsed.username
                or not parsed.hostname or not ip_address(parsed.hostname).is_private):
            raise RuntimeError('存储服务必须使用本次部署的私网地址')
    elif base != 'http://tos-storage:10012':
        raise RuntimeError('Local TOS service must be http://tos-storage:10012')
    return base


def _result_path(result_id):
    if not re.fullmatch(r'[0-9a-f]{32}', result_id):
        raise ValueError('Invalid result ID')
    return '/internal/results/' + result_id


def _delete_local_cache(local_path, asset_id=''):
    path = Path(local_path)
    try:
        path.unlink(missing_ok=True)
        service_log.event(
            'tos', '本地缓存已删除',
            context=json.dumps(
                {'asset_id': asset_id, 'file': path.name}, ensure_ascii=False))
        return True
    except OSError as error:
        service_log.event(
            'tos', '本地缓存删除失败',
            context=json.dumps(
                {'asset_id': asset_id, 'file': path.name,
                 'error_type': type(error).__name__}, ensure_ascii=False),
            level='error')
        return False


def upload_result(result_id, local_path, delete_local=True):
    with open(local_path, 'rb') as stream:
        response = http_client.session().post(_base() + _result_path(result_id),
                                 headers=local_headers('local-video-storage'),
                                 files={'file': ('result.mp4', stream, 'video/mp4')}, timeout=(10, 600))
    if response.status_code != 201:
        raise RuntimeError('TOS result upload failed; the composed video remains on local disk')
    if delete_local:
        _delete_local_cache(local_path, result_id)
    url = os.getenv('PUBLIC_API_BASE_URL','http://127.0.0.1:8080').rstrip('/')+'/api/v2/results/' + result_id
    service_log.event("tos", "视频转存成功", context=json.dumps(
        {"result_id": result_id, "url": url}, ensure_ascii=False))
    return url


def upload_asset(asset_id, local_path, mime):
    path = Path(local_path)
    with path.open('rb') as stream:
        response = http_client.session().post(
            _base() + '/internal/assets/' + asset_id,
            headers=local_headers('local-phase2-storage'),
            files={'file': (path.name, stream, mime)},
            timeout=(10, 600),
        )
    if response.status_code != 201:
        raise RuntimeError('TOS asset upload failed')
    url = (
        os.getenv('PUBLIC_API_BASE_URL', 'http://127.0.0.1:8080').rstrip('/')
        + '/api/v2/assets/' + asset_id
    )
    service_log.event(
        'tos', '素材转存成功',
        context=json.dumps(
            {'asset_id': asset_id, 'mime': mime, 'url': url},
            ensure_ascii=False))
    return url


def upload_asset_bytes(asset_id, content, filename, mime):
    response = http_client.session().post(
        _base() + '/internal/assets/' + asset_id,
        headers=local_headers('local-phase2-storage'),
        files={'file': (filename, content, mime)},
        timeout=(10, 600),
    )
    if response.status_code != 201:
        raise RuntimeError('TOS asset upload failed')
    url = (
        os.getenv('PUBLIC_API_BASE_URL', 'http://127.0.0.1:8080').rstrip('/')
        + '/api/v2/assets/' + asset_id
    )
    service_log.event(
        'tos', '生成素材转存成功',
        context=json.dumps(
            {'asset_id': asset_id, 'mime': mime, 'url': url},
            ensure_ascii=False))
    return url


def asset_url(asset_id):
    response = http_client.session().get(
        _base() + '/internal/assets/' + asset_id + '/url',
        headers=local_headers('local-phase2-storage'),
        timeout=(5, 15),
    )
    if response.status_code != 200:
        raise RuntimeError('TOS asset link unavailable')
    return response.json()['url']


def delete_asset(asset_id):
    response = http_client.session().delete(
        _base() + '/internal/assets/' + asset_id,
        headers=local_headers('local-profile-lifecycle'),
        timeout=(5, 30),
    )
    if response.status_code not in (200, 204, 404):
        raise RuntimeError('TOS asset deletion failed')


def result_url(result_id):
    response = http_client.session().get(_base() + _result_path(result_id) + '/url',
                            headers=local_headers('local-phase2-storage'), timeout=(5, 15))
    if response.status_code != 200:
        raise RuntimeError('TOS result link unavailable; check the local storage service')
    return response.json()['url']


def result_poster(result_id):
    response = http_client.session().get(
        _base() + _result_path(result_id) + '/poster',
        headers=local_headers('local-phase2-storage'), timeout=(5, 120))
    if response.status_code != 200:
        raise RuntimeError('poster unavailable')
    return response.content


def save_portrait(project_id, character_task_id, source_url):
    """Persist a display-only portrait without handing it to the video provider."""
    from common.media_refs import transform
    source_url = transform(source_url)
    result_id = uuid.uuid5(uuid.NAMESPACE_URL, project_id + '/' + character_task_id + '/portrait').hex
    parsed = urlparse(source_url)
    # 兼容两种形式：/api/v2/assets/<id> 与 /media/<token>/api/v2/assets/<id>
    asset_match = re.search(r'/api/v2/assets/((?:asset|file)_[0-9a-f]{32})$', parsed.path)
    if asset_match:
        url = asset_url(asset_match.group(1))
    else:
        base = os.environ['PUBLIC_BASE_URL'].rstrip('/') + '/'
        if not source_url.startswith(base):
            raise ValueError('定妆照来源不属于当前本地项目')
        relative = source_url[len(base):]
        if not relative.startswith('aitravel/api/v1/file/download/') or urlparse(relative).query:
            raise ValueError('定妆照来源不是本地生成文件')
        url = os.getenv('FILE_SERVICE_URL','http://file:10004').rstrip('/')+'/' + relative.removeprefix('aitravel/')
    try:
        with http_client.session().get(url, stream=True, timeout=(5, 60), allow_redirects=False) as source:
            source.raise_for_status()
            content = bytearray()
            for chunk in source.iter_content(65536):
                content.extend(chunk)
                if len(content) > 32 * 1024 * 1024:
                    raise RuntimeError('定妆照文件超过 32MB')
        response = http_client.session().post(_base() + '/internal/portraits/' + result_id,
            headers=local_headers('local-phase2-storage'),
            files={'file': ('portrait', bytes(content), 'application/octet-stream')}, timeout=(10, 120))
        if response.status_code != 201:
            raise RuntimeError('定妆照保存到 TOS 失败，已保留本地图片，重试不会重新生成')
    except requests.RequestException as exc:
        raise RuntimeError('定妆照存储连接失败，已保留本地图片，请稍后重试保存') from exc
    return result_id, os.getenv('PUBLIC_API_BASE_URL','http://127.0.0.1:8080').rstrip('/')+'/api/v2/portraits/' + result_id


def portrait_url(result_id):
    _result_path(result_id)  # Validate the identifier before composing the internal URL.
    response = http_client.session().get(_base() + '/internal/portraits/' + result_id + '/url',
                            headers=local_headers('local-phase2-storage'), timeout=(5, 15))
    if response.status_code != 200:
        raise RuntimeError('定妆照暂时无法读取')
    return response.json()['url']


def delete_result(result_id):
    response = http_client.session().delete(
        _base() + _result_path(result_id),
        headers=local_headers('local-profile-lifecycle'),
        timeout=(5, 30),
    )
    if response.status_code not in (200, 204, 404):
        raise RuntimeError('TOS result deletion failed')


def delete_portrait(result_id):
    _result_path(result_id)
    response = http_client.session().delete(
        _base() + '/internal/portraits/' + result_id,
        headers=local_headers('local-profile-lifecycle'),
        timeout=(5, 30),
    )
    if response.status_code not in (200, 204, 404):
        raise RuntimeError('TOS portrait deletion failed')


def delete_file_url(url):
    if str(url).startswith('local-media:'):
        path = unquote(str(url)[len('local-media:'):].split('?', 1)[0])
    else:
        path = unquote(urlparse(url).path)
    marker = '/api/v1/file/download/'
    if marker not in path:
        raise ValueError('Unsupported generated file URL')
    relative = path.split(marker, 1)[1].strip('/')
    parts = relative.split('/')
    if len(parts) != 2 or not all(parts):
        raise ValueError('Invalid generated file path')
    base = os.getenv('FILE_SERVICE_URL', 'http://file:10004').rstrip('/')
    response = http_client.session().delete(
        base + '/api/v1/file/delete/' + relative,
        headers=local_headers('phase2-file'),
        timeout=(5, 30),
    )
    if response.status_code not in (200, 204, 404):
        raise RuntimeError('Generated file deletion failed')


def delete_local_video(user_id, task_id):
    base = os.getenv('SEEDDANCE_SERVICE_URL', '').rstrip('/')
    if not base:
        return
    response = http_client.session().delete(
        base + '/api/v1/video/result/' + task_id,
        params={'wx_openid': user_id},
        headers=local_headers('local-phase2'),
        timeout=(5, 30),
    )
    if response.status_code not in (200, 204, 404):
        raise RuntimeError('Local video result deletion failed')
