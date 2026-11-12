"""Create allowlisted deploy archives; private env, keys, backups and phase-one code never enter."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import io
import re
import subprocess
import tarfile
import time
import zipfile

ROOT=Path(__file__).resolve().parents[2]
FORBIDDEN_PARTS={'__pycache__','node_modules','.git','uploads','instance','data','logs','archive','local-backups','deploy-config','tls'}
FORBIDDEN_SUFFIXES={'.pem','.key','.crt','.p12','.pfx','.pyc','.db','.sqlite3','.log','.zip','.gz','.rar','.7z','.tar'}

def allowed(path):
    rel=path.relative_to(ROOT)
    if any(p in FORBIDDEN_PARTS for p in rel.parts) or path.suffix.lower() in FORBIDDEN_SUFFIXES: return False
    if path.name.startswith('.env') or path.name=='project.private.config.json': return False
    return path.is_file()

def collect(paths):
    files=set()
    for relative in paths:
        path=ROOT/relative
        if not path.exists(): raise FileNotFoundError(relative)
        for f in path.rglob('*') if path.is_dir() else [path]:
            if allowed(f): files.add(f)
    return sorted(files)


def source_revision():
    try:
        commit = subprocess.check_output(
            ['git', 'rev-parse', '--short=12', 'HEAD'], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = bool(subprocess.check_output(
            ['git', 'status', '--porcelain'], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip())
        return commit + ('-dirty' if dirty else '')
    except (OSError, subprocess.SubprocessError):
        return 'unknown'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',choices=('main','ai','data'),help='Only package one server node')
    selected=parser.parse_args().node
    output=ROOT/'dist'; output.mkdir(exist_ok=True)
    revision = source_revision()
    built_at = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    common=['.dockerignore','infra/staging','server/common','server/phase2/requirements.txt',
        'server/local/requirements-real.txt','server/local/requirements-tos.txt','server/local/reconcile_task.py','server/staging']
    nodes={
        'main':common+['server/phase2','server/logcollector'],
        'ai':common+['server/charactergen','server/personattr/personattr_app.py',
            'server/aitask/seeddance_video_app.py','server/ai2pic/seedream_app.py','server/local/results.py'],
        'data':common+['server/admin','server/phase2','server/file/file_app.py','server/local/tos_app.py','server/local/tos_storage.py']}
    manifests={}
    for node,paths in nodes.items():
        if selected and node!=selected: continue
        filename='phase2-'+node+'.tar.gz'; files=collect(paths)
        with tarfile.open(output/filename,'w:gz') as tar:
            for path in files: tar.add(path,arcname=str(path.relative_to(ROOT)).replace('\\','/'),recursive=False)
            info = json.dumps({
                'node': node, 'source_revision': revision, 'built_at': built_at,
            }, ensure_ascii=False).encode('utf-8')
            member = tarfile.TarInfo('release_info.json')
            member.size = len(info)
            member.mode = 0o644
            member.mtime = int(time.time())
            tar.addfile(member, io.BytesIO(info))
        manifests[filename]={'files':len(files),'bytes':(output/filename).stat().st_size,
            'sha256':hashlib.sha256((output/filename).read_bytes()).hexdigest()}
    if not selected or selected=='main':
        release_api=os.getenv('PHASE2_RELEASE_API_BASE_URL','').strip().rstrip('/')
        release_appid=os.getenv('PHASE2_RELEASE_APP_ID','').strip()
        if not release_api.startswith('https://'):
            raise RuntimeError('Set PHASE2_RELEASE_API_BASE_URL to the production HTTPS API origin')
        if not re.fullmatch(r'wx[a-zA-Z0-9]{16}', release_appid) or release_appid == 'wxaa62b76ca42a9db5':
            raise RuntimeError('Set PHASE2_RELEASE_APP_ID to the new Hejian mini program AppID')
        files=collect(['client/app.js','client/app.json','client/app.wxss','client/sitemap.json','client/project.config.json',
                       'client/config','client/pages/phase2','client/state','client/styles','client/utils','client/assets'])
        with zipfile.ZipFile(output/'phase2-client.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                relative=str(path.relative_to(ROOT)).replace('\\','/')
                if relative=='client/config/target.js':
                    payload=json.dumps(dict(environment='production',apiBaseUrl=release_api),
                                       ensure_ascii=False,separators=(',',':'))
                    archive.writestr(relative,'module.exports = '+payload+'\n')
                elif relative=='client/project.config.json':
                    project=json.loads(path.read_text(encoding='utf-8'))
                    project['appid']=release_appid
                    archive.writestr(relative,json.dumps(project,ensure_ascii=False,indent=2)+'\n')
                else:
                    archive.write(path,relative)
        manifests['phase2-client.zip']={'files':len(files),'bytes':(output/'phase2-client.zip').stat().st_size,
            'sha256':hashlib.sha256((output/'phase2-client.zip').read_bytes()).hexdigest()}
    (output/'manifest.json').write_text(json.dumps(manifests,ensure_ascii=False,indent=2),encoding='utf-8')
    for name,info in manifests.items(): print(name+': '+str(info['files'])+' files, '+str(round(info['bytes']/1024/1024,2))+' MB')

if __name__=='__main__': main()
