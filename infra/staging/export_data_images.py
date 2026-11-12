"""Build from allowlisted releases and export images; defaults to the data node."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
RECIPES = {
    'data': {'external': ['mysql:8.4', 'redis:7.4-alpine'],
             'builds': {'data': ['file', 'tos'], 'admin': ['admin']}},
    'main': {'external': ['nginx:1.27.5-alpine'],
             'builds': {'main': ['phase2-api']}},
    'ai': {'external': [],
           'builds': {'ai': ['personattr', 'seedream', 'charactergen', 'video']}},
}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', choices=RECIPES, default='data')
    node = parser.parse_args().node
    recipe = RECIPES[node]
    image_tag = lambda service: 'hejian-lvpai-test-' + node + '-' + service + ':latest'
    images = recipe['external'] + [image_tag(service)
              for services in recipe['builds'].values() for service in services]
    release = ROOT / ('dist/phase2-' + node + '.tar.gz')
    output = ROOT / ('dist/phase2-' + node + '-images.tar.gz')
    # Refuse incompatible cached images rather than silently shipping another architecture.
    for name in recipe['external']:
        info = json.loads(run('docker', 'image', 'inspect', name,
                              capture_output=True, text=True).stdout)[0]
        if (info['Os'], info['Architecture']) != ('linux', 'amd64'):
            raise RuntimeError('Expected linux/amd64: ' + name)
    with tempfile.TemporaryDirectory(prefix='phase2-offline-') as tmp:
        work = Path(tmp)
        context = work / 'source'
        with tarfile.open(release) as archive:
            for member in archive.getmembers():
                target = (context / member.name).resolve()
                if context.resolve() not in target.parents or not member.isfile():
                    raise ValueError('Unsafe release archive member')
            archive.extractall(context)
        for dockerfile, services in recipe['builds'].items():
            primary = image_tag(services[0])
            run('docker', 'build', '--platform', 'linux/amd64', '-f',
                'server/staging/Dockerfile-' + dockerfile, '-t', primary, '.', cwd=context)
            for service in services[1:]:
                run('docker', 'tag', primary, image_tag(service))
        image_info = []
        for name in images:
            info = json.loads(run('docker', 'image', 'inspect', name,
                                  capture_output=True, text=True).stdout)[0]
            if (info['Os'], info['Architecture']) != ('linux', 'amd64'):
                raise RuntimeError('Expected linux/amd64: ' + name)
            image_info.append({'tag': name, 'id': info['Id'],
                               'os': info['Os'], 'architecture': info['Architecture']})
        raw = work / 'images.tar'
        run('docker', 'image', 'save', '-o', str(raw), *images)
        with tarfile.open(raw) as archive:
            manifest = json.load(archive.extractfile('manifest.json'))
            tags = {tag for row in manifest for tag in row.get('RepoTags', [])}
            if not set(images).issubset(tags):
                raise RuntimeError('Missing image tags in export')
        partial = output.with_suffix('.partial')
        with raw.open('rb') as source, gzip.open(partial, 'wb', compresslevel=3) as dest:
            shutil.copyfileobj(source, dest, length=1024 * 1024)
        partial.replace(output)
    digest = hashlib.sha256()
    with output.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    # Linux sha256sum treats a Windows CR as part of the archive filename.
    (ROOT / ('dist/phase2-' + node + '-images.sha256')).write_bytes(
        (digest.hexdigest() + '  ' + output.name + '\n').encode('ascii'))
    (ROOT / ('dist/phase2-' + node + '-images.json')).write_text(
        json.dumps(image_info, indent=2), encoding='utf-8')
    print('PASS: %s exported all %d image tags; %.1f MiB' %
          (node, len(images), output.stat().st_size / 1024**2))


if __name__ == '__main__':
    main()
