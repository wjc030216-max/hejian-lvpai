"""Cold, complete node-volume backup. Stop all three application nodes before using it."""
import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path


def docker(*args):
    with tempfile.TemporaryFile() as output:
        subprocess.run(['docker',*args],stdout=output,stderr=subprocess.STDOUT,check=True,timeout=12)
        output.seek(0)
        return output.read().decode()


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): value.update(block)
    return value.hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('node',choices=('main','data','ai'))
    p.add_argument('directory',type=Path)
    p.add_argument('--app-env',choices=('staging','production'),default=os.getenv('APP_ENV','staging'))
    p.add_argument('--restore',action='store_true',help='Restore only into empty existing Docker volumes')
    p.add_argument('--verify',action='store_true',help='Verify hashes without touching Docker')
    a=p.parse_args(); folder=a.directory.resolve()
    prefix='hejian-lvpai-test' if a.app_env=='staging' else 'hejian-lvpai-production'
    project=prefix+'-'+a.node
    if a.verify:
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        for item in manifest['volumes']:
            path=folder/item['archive']
            if path.parent!=folder or digest(path)!=item['sha256']: raise RuntimeError('Backup checksum mismatch')
        print('Backup checksums valid');return
    running=docker('ps','-q','--filter','label=com.docker.compose.project='+project).strip()
    if running: raise RuntimeError('Stop this node first; a live MySQL file copy is not a valid backup')
    names=docker('volume','ls','-q','--filter','label=com.docker.compose.project='+project).split()
    if not names: raise RuntimeError('No existing volumes for '+project)
    mounts={name:Path(json.loads(docker('volume','inspect',name))[0]['Mountpoint']).resolve() for name in names}
    if a.restore:
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        if manifest['project']!=project: raise RuntimeError('Backup belongs to a different node')
        for item in manifest['volumes']:
            mount=mounts[item['name']]; archive=folder/item['archive']
            if archive.parent!=folder or digest(archive)!=item['sha256']: raise RuntimeError('Backup checksum mismatch')
            if any(mount.iterdir()): raise RuntimeError('Restore target must be empty: '+str(mount))
            with tarfile.open(archive,'r:gz') as source:
                for member in source.getmembers():
                    target=(mount/member.name).resolve()
                    if target!=mount and mount not in target.parents: raise RuntimeError('Unsafe backup path')
                    if not member.isdir() and not member.isfile(): raise RuntimeError('Backup contains a link or special file')
                source.extractall(mount)
        print('Volumes restored; start data, AI and main nodes in that order');return
    folder.mkdir(parents=True,exist_ok=False)
    manifest=dict(project=project,volumes=[])
    for name,mount in mounts.items():
        archive=folder/(name+'.tar.gz')
        with tarfile.open(archive,'w:gz') as target:
            target.add(mount,arcname='.',recursive=True)
        manifest['volumes'].append(dict(name=name,archive=archive.name,sha256=digest(archive)))
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('Backed up',len(mounts),'volumes. Keep matching code, private environment and TLS separately.')


if __name__=='__main__': main()
