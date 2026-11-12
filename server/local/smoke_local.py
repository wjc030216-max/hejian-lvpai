"""Read-only local acceptance. Does not submit photos, payments, or cloud jobs."""
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit


def fetch(url, method='GET'):
    req = urllib.request.Request(quote(url, safe=':/%?=&'), method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main():
    base = 'http://127.0.0.1:8080'
    root = Path(__file__).resolve().parents[2]
    config = dict(line.split('=', 1) for line in (root / '.env.local.real').read_text(encoding='utf-8-sig').splitlines()
                  if '=' in line and not line.startswith('#'))
    token = config['LOCAL_MEDIA_TOKEN']
    media = 'http://127.0.0.1:8081/media/' + token
    status, raw = fetch(base + '/api/v2/health')
    health = json.loads(raw)['data']
    assert status == 200 and health['environment'] == 'local-real' and health['redis']
    assert health['payment_mode'] == 'dev-bypass'
    status, raw = fetch(media + '/ready')
    assert status == 200 and json.loads(raw)['installation'] == hashlib.sha256(token.encode()).hexdigest()
    assert fetch('http://127.0.0.1:8081/media/wrong/ready')[0] == 404
    for path in ['/api/v2/projects', '/api/v2/health', '/api/v1/file/upload', '/api/v1/file/delete']:
        for method in ['GET', 'POST', 'DELETE']:
            assert fetch(media + path, method)[0] == 404, 'Media gateway exposed a business endpoint'
    assert fetch(media + '/ready', 'POST')[0] == 403
    status, raw = fetch(base + '/api/v2/templates?limit=1&seed=acceptance')
    first = json.loads(raw)['data']
    assert status == 200 and len(first['items']) <= 1
    assert first['scenes'] and all(isinstance(name, str) and name for name in first['scenes'])
    items = list(first['items'])
    offset = first['next_offset']
    while offset is not None:
        status, raw = fetch(base + '/api/v2/templates?limit=1&seed=acceptance&offset=' + str(offset))
        page = json.loads(raw)['data']
        items.extend(page['items']); offset = page['next_offset']
    assert len(items) == first['total'] == len({i['template_code'] for i in items})
    for item in items:
        status, raw = fetch(base + '/api/v2/templates/' + item['template_code'] + '/runtime')
        assert status == 200, 'Template runtime is incomplete'
        runtime = json.loads(raw)['data']
        assert runtime['portrait_prompt']
        urls = runtime['costume_url'] + runtime['actor_url'] + runtime['screen_url'] + [runtime['bgm_url'], item['cover'], item['example']]
        for url in urls:
            path = urlsplit(url).path
            assert path.startswith('/media/' + token + '/'), 'Unexpected media installation'
            assert fetch('http://127.0.0.1:8081' + path, 'HEAD')[0] == 200, 'Template asset missing'
    for endpoint in ['/api/v2/works', '/api/v2/config']:
        assert fetch(base + endpoint)[0] == 200
    assert fetch(base + '/api/v2/benefits')[0] == 404
    assert fetch(base + '/api/v2/benefits/draw', 'POST')[0] == 404
    print('PASS: local health, dev payment mode, stable pagination, template assets, works, media-only isolation. No generation submitted.')


if __name__ == '__main__':
    main()
