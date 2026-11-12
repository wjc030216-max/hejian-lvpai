"""Consistent local-only backup and an optional isolated MySQL restore rehearsal."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import time
import uuid

PREFIX='aitravel-local-real-'
SERVICES=['phase2-api','charactergen','seeddance-video','seedream','tos-storage','file']
VOLUMES=['aitravel_uploads_real','aitravel_file_real','aitravel_tos_records_real']


def run(args,**kwargs):
    if kwargs.pop('capture_output',False): kwargs['stdout']=subprocess.PIPE
    return subprocess.run(['docker']+args,check=True,stderr=subprocess.PIPE,**kwargs)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--drill',action='store_true'); args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    folder=root/'local-backups'/time.strftime('%Y%m%d-%H%M%S'); folder.mkdir(parents=True,exist_ok=False)
    paused=[]
    try:
        for service in SERVICES:
            name=PREFIX+service+'-1'; run(['pause',name],stdout=subprocess.DEVNULL); paused.append(name)
        mysql=['exec',PREFIX+'mysql-1','mysql','-uroot','-plocal_root_password','-N']
        has_tasks=int(run(mysql+['-e',"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='aitravel_dev' AND table_name='phase2_tasks'"],capture_output=True).stdout)
        if has_tasks:
            active=int(run(mysql+['-e',"SELECT COUNT(*) FROM aitravel_dev.phase2_tasks WHERE status IN ('pending','running','recovering') AND kind IN ('character','video','image')"],capture_output=True).stdout)
            if active: raise RuntimeError('存在执行中的任务，请完成后再备份')
        has_projects=int(run(mysql+['-e',"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='aitravel_dev' AND table_name='phase2_projects'"],capture_output=True).stdout)
        if has_projects:
            active=int(run(mysql+['-e',"SELECT COUNT(*) FROM aitravel_dev.phase2_projects WHERE order_no IS NOT NULL AND status NOT IN ('completed','terminated','refund_pending','archived','needs_review')"],capture_output=True).stdout)
            if active: raise RuntimeError('存在待执行订单，请完成后再备份')
        with (folder/'mysql.sql').open('wb') as out:
            run(['exec',PREFIX+'mysql-1','mysqldump','-uroot','-plocal_root_password','--single-transaction',
                '--no-tablespaces','--set-gtid-purged=OFF','aitravel_dev'],stdout=out)
        mysql=['exec',PREFIX+'mysql-1','mysql','-uroot','-plocal_root_password','-N']
        tables=run(mysql+['-e',"SELECT table_name FROM information_schema.tables WHERE table_schema='aitravel_dev'"],capture_output=True).stdout.decode().splitlines()
        expected={}
        for table in tables:
            if not re.fullmatch(r'[A-Za-z0-9_]+',table): raise RuntimeError('Unexpected table identifier')
            expected[table]=int(run(mysql+['-e','SELECT COUNT(*) FROM aitravel_dev.'+table],capture_output=True).stdout)
        (folder/'counts.json').write_text(json.dumps(expected,indent=2),encoding='utf-8')
        for volume in VOLUMES:
            with (folder/(volume+'.tar')).open('wb') as out:
                run(['run','--rm','--network','none','--mount',
                    'type=volume,source=aitravel-local-real_'+volume+',target=/backup-source,readonly',
                    'aitravel-local-real-charactergen','tar','-C','/backup-source','-cf','-','.'],stdout=out)
    finally:
        for name in reversed(paused): run(['unpause',name],stdout=subprocess.DEVNULL)
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    if args.drill:
        schema='aitravel_restore_'+uuid.uuid4().hex[:12]
        assert re.fullmatch('aitravel_restore_[a-f0-9]{12}',schema)
        mysql=['exec','-i',PREFIX+'mysql-1','mysql','-uroot','-plocal_root_password']
        run(mysql+['-e','CREATE DATABASE '+schema+' CHARACTER SET utf8mb4'],stdout=subprocess.DEVNULL)
        try:
            with (folder/'mysql.sql').open('rb') as source: run(mysql+[schema],stdin=source,stdout=subprocess.DEVNULL)
            count=run(mysql+['-N','-e',"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='"+schema+"'"],capture_output=True).stdout.decode().strip()
            for table,expected_rows in expected.items():
                actual=int(run(mysql+['-N','-e','SELECT COUNT(*) FROM '+schema+'.'+table],capture_output=True).stdout)
                if actual!=expected_rows: raise RuntimeError('Restored row count mismatch: '+table)
            for archive in folder.glob('*.tar'):
                with tarfile.open(archive) as data:
                    for member in data:
                        if member.isfile():
                            stream=data.extractfile(member)
                            while stream.read(1024*1024): pass
            print('Restore rehearsal passed:',count,'tables; all asset archives readable')
        finally:
            run(mysql+['-e','DROP DATABASE '+schema],stdout=subprocess.DEVNULL)
    print('Backup saved:',folder)


if __name__=='__main__': main()
