"""Validate or import template folders and individual ZIPs with a resumable report."""
import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from sqlalchemy import text

from admin.importer import WINDOWS_JUNK, batch_code, import_archive
from admin.store import AdminStore


def template_code(source):
    name = source.stem if source.is_file() else source.name
    return batch_code(name)


def discover(source):
    candidates = []
    for path in sorted(source.rglob('*'), key=lambda p: str(p)):
        if path.is_symlink():
            continue
        if path.is_file() and path.suffix.lower() == '.zip':
            candidates.append(path)
            continue
        if not path.is_dir():
            continue
        folder = path
        files = [p for p in folder.iterdir() if p.is_file()]
        if any(p.name in ('1-基础元数据.txt', 'metadata.txt') for p in files) or (
            any(p.name.startswith('6-') for p in files)
            and any(p.name.startswith('7-') for p in files)
        ):
            candidates.append(folder)
    if any(p.is_file() and p.name in ('1-基础元数据.txt', 'metadata.txt') for p in source.iterdir()):
        candidates.insert(0, source)
    return candidates


def existing_templates(store):
    if store is None:
        return {}, {}
    by_code = {}
    by_identity = {}
    with store.engine.connect() as db:
        rows = db.execute(text('SELECT template_code,payload_json FROM phase2_templates'))
        for code, payload in rows:
            item = json.loads(payload)
            by_code[code] = item
            by_identity[(item.get('location_association'), item.get('video_name'))] = code
    return by_code, by_identity


def import_batch(source, assets, store=None, commit=False, replace=False, actor='batch-import'):
    source = Path(source).resolve(strict=True)
    if not source.is_dir() and not (source.is_file() and source.suffix.lower() == '.zip'):
        raise ValueError('素材来源必须是目录或单个 ZIP')
    if commit and store is None:
        raise ValueError('正式导入需要数据库连接')
    assets = Path(assets)
    by_code, by_identity = existing_templates(store)
    candidates = discover(source) if source.is_dir() else [source]
    relative_root = source if source.is_dir() else source.parent
    if not candidates:
        raise ValueError('未找到模板文件夹或独立 ZIP；RAR 需先解压')
    results = []
    for path in candidates:
        code = template_code(path)
        row = {'source': str(path.relative_to(relative_root)) or '.', 'template_code': code}
        revision_root = assets if commit else None
        item = None
        source_sha256 = ''
        try:
            if path.is_file():
                import hashlib as _hashlib
                source_sha256 = _hashlib.sha256(path.read_bytes()).hexdigest()
                if commit and not replace:
                    same_archive = store.find_by_archive_hash(source_sha256)
                    if same_archive:
                        row.update(status='skipped',
                                   reason='该模板包已上传过：' + same_archive[0] + ' ' + same_archive[1])
                        results.append(row)
                        continue
                archive_file = path.open('rb')
            else:
                if any(p.is_symlink() for p in path.iterdir()):
                    raise ValueError('模板目录不允许文件或目录链接')
                files = [p for p in path.iterdir() if p.is_file()
                         and p.name.casefold() not in WINDOWS_JUNK]
                if any(p.is_dir() for p in path.iterdir()):
                    raise ValueError('单个模板文件夹内不能再嵌套目录')
                archive_file = tempfile.TemporaryFile()
                with zipfile.ZipFile(archive_file, 'w', compression=zipfile.ZIP_STORED) as archive:
                    for asset in files:
                        archive.write(asset, path.name + '/' + asset.name)
                archive_file.seek(0)
            with archive_file:
                if commit:
                    item = import_archive(archive_file, revision_root, code)
                else:
                    with tempfile.TemporaryDirectory(prefix='phase2-template-check-') as check_root:
                        item = import_archive(archive_file, check_root, code)
            if source_sha256:
                item['source_sha256'] = source_sha256
            row['video_name'] = item['video_name']
            row['location_association'] = item['location_association']
            row['duration_seconds'] = int(item.get('duration_seconds') or 15)
            row['segment_count'] = int(item.get('segment_count') or 1)
            identity = (item['location_association'], item['video_name'])
            from common.database_schema import template_identity
            code=by_identity.get(identity) or 'P2B-'+template_identity(*identity)[:24]
            item['template_code']=code; item['managed_runtime']['template_code']=code
            row['template_code']=code
            old_code = by_identity.get(identity)
            if old_code and not replace:
                row.update(status='skipped', reason='同景区同名称模板已存在：' + old_code)
            elif not commit:
                by_identity[identity] = code
                row.update(status='valid', reason='校验通过，未写入数据库')
            else:
                store.save_upload(item, actor, replace=replace, publish=True)
                by_code[code] = item
                by_identity[identity] = code
                row.update(status='imported', reason='已上架，初始展示热度 1 万')
            if commit and row['status'] == 'skipped':
                shutil.rmtree(assets / item['asset_revision'])
        except Exception as exc:
            if commit and item is not None:
                shutil.rmtree(assets / item['asset_revision'], ignore_errors=True)
            row.update(status='failed', reason=str(exc))
        results.append(row)
    summary = {status: sum(row['status'] == status for row in results)
               for status in ('valid', 'imported', 'skipped', 'failed')}
    return {'source': str(source), 'mode': 'commit' if commit else 'dry-run',
            'summary': summary, 'templates': results}


def main():
    parser = argparse.ArgumentParser(description='Batch-check or import phase-two template folders')
    parser.add_argument('--source', required=True, help='Directory containing template folders')
    parser.add_argument('--report', required=True, help='JSON report path')
    parser.add_argument('--commit', action='store_true', help='Write valid templates to the database')
    parser.add_argument('--replace', action='store_true', help='Replace matching batch template codes and publish the new version')
    args = parser.parse_args()
    if args.replace and not args.commit:
        parser.error('--replace requires --commit')
    store = AdminStore(os.environ['AITRAVEL_DATABASE_URL']) if args.commit else None
    try:
        report = import_batch(args.source,
                              os.getenv('ADMIN_TEMPLATE_ROOT', '/var/lib/hejian-lvpai/templates'),
                              store=store, commit=args.commit, replace=args.replace)
    finally:
        if store:
            store.engine.dispose()
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False))
    print('Report:', report_path)
    return 1 if report['summary']['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
