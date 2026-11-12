"""Ninety-day user-data retention and media cleanup."""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
from pathlib import Path

from common import service_log


RETENTION_DAYS = max(1, int(os.getenv('PHASE2_USER_RETENTION_DAYS', '90')))
RETENTION_SECONDS = RETENTION_DAYS * 86400
RETENTION_BATCH_SIZE = max(1, int(os.getenv('PHASE2_RETENTION_BATCH_SIZE', '100')))
RETENTION_INTERVAL_SECONDS = max(30, int(os.getenv('PHASE2_RETENTION_INTERVAL_SECONDS', '300')))
RETENTION_TIME_BUDGET_SECONDS = max(30, int(os.getenv('PHASE2_RETENTION_TIME_BUDGET_SECONDS', '240')))


def expires_at(project):
    explicit = project.get('retention_expires_at')
    if explicit:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass
    anchor = (project.get('completed_at') or project.get('paid_at')
              or project.get('created_at') or time.time())
    return float(anchor) + RETENTION_SECONDS


def expired(project, now=None):
    return expires_at(project) <= (time.time() if now is None else now)


def url_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from url_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from url_strings(item)
    elif isinstance(value, str) and (
            'http://' in value or 'https://' in value or value.startswith('local-media:')):
        yield value


class RetentionService:
    def __init__(self, store, upload_root, share_cache):
        self.store = store
        self.upload_root = Path(upload_root)
        self.share_cache = Path(share_cache)
        self.stop = threading.Event()
        self._tick_lock = threading.Lock()
        self.last_run = 0.0

    @staticmethod
    def _media_id(field, url_field, pattern, project):
        value = project.get(field)
        if value:
            return str(value)
        match = re.search(pattern, str(project.get(url_field) or ''))
        return match.group(1) if match else ''

    def _delete_generated_files(self, urls):
        from common.storage_client import delete_file_url
        errors = []
        for url in sorted(set(urls)):
            if '/api/v1/file/download/' not in url:
                continue
            try:
                delete_file_url(url)
            except Exception as error:
                errors.append(type(error).__name__)
        return errors

    @staticmethod
    def _tos_asset_ids(urls):
        values = set()
        for url in urls:
            match = re.search(r'/api/v2/assets/((?:asset|file)_[0-9a-f]{32})', str(url))
            if match:
                values.add(match.group(1))
        return values

    @staticmethod
    def _asset_ids(tasks):
        values = set()
        for task in tasks:
            for name, step in (task.get('steps') or {}).items():
                result = (step or {}).get('result')
                if name.startswith('asset:') and isinstance(result, str):
                    values.add(result)
            for item in task.get('reference_plan') or []:
                asset_id = item.get('asset_id')
                if isinstance(asset_id, str) and asset_id:
                    values.add(asset_id)
            for item in (task.get('video_process') or {}).get('inputs') or []:
                asset_id = item.get('asset_id')
                if isinstance(asset_id, str) and asset_id:
                    values.add(asset_id)
        return values

    def cleanup(self, project):
        from common.storage_client import (
            delete_asset, delete_local_video, delete_portrait, delete_result,
        )
        from common.video_asset_client import delete_video_asset

        pid = project['project_id']
        runtime_code = project.get('runtime_code') or ''
        tasks = self.store.task_records_for_project(pid, runtime_code)
        urls = list(url_strings(project))
        for task in tasks:
            urls.extend(url_strings(task))

        errors = []
        for result_id, operation in (
            (self._media_id('portrait_asset_id', 'portrait_url',
                            r'/api/v2/portraits/([0-9a-f]{32})', project), delete_portrait),
            (self._media_id('video_task_id', 'video_url',
                            r'/api/v2/results/([0-9a-f]{32})', project), delete_result),
        ):
            if not result_id:
                continue
            try:
                operation(result_id)
            except Exception as error:
                errors.append(type(error).__name__)

        video_id = self._media_id('video_task_id', 'video_url',
                                  r'/api/v2/results/([0-9a-f]{32})', project)
        if video_id:
            try:
                delete_local_video(project.get('wx_openid') or '', video_id)
            except Exception as error:
                errors.append(type(error).__name__)

        try:
            folder = (self.upload_root / pid).resolve()
            if folder.parent == self.upload_root.resolve() and folder.exists():
                shutil.rmtree(folder)
                if folder.exists():
                    raise OSError('上传目录未删除')
        except OSError:
            errors.append('UploadFolderError')

        errors.extend(self._delete_generated_files(urls))
        for asset_id in sorted(self._tos_asset_ids(urls)):
            try:
                delete_asset(asset_id)
            except Exception as error:
                errors.append('Asset:' + type(error).__name__)
        for asset_id in sorted(self._asset_ids(tasks)):
            try:
                delete_video_asset(asset_id)
            except Exception as error:
                errors.append('VideoAsset:' + type(error).__name__)
        for cache in self.share_cache.glob(pid + '-*.jpg'):
            try:
                cache.unlink(missing_ok=True)
            except OSError:
                errors.append('ShareCacheError')

        if errors:
            self.store.mark_retention_result(pid, False, ','.join(errors))
            service_log.event('system', '90天数据清理待重试',
                              context=f"project={pid} errors={','.join(errors)}")
            return False

        with self.store.transaction() as db:
            record = self.store.get(pid, db)
            if not record:
                return False
            self.store.delete_project_process(pid, runtime_code, tasks, db)
            template = record.get('template') if isinstance(record.get('template'), dict) else {}
            safe_template = {
                key: template[key] for key in (
                    'video_name', 'actor_count', 'video_genre', 'location_association',
                    'video_specification',
                ) if key in template
            }
            record.update(
                status='expired',
                stage='expired',
                title='数据保留期已结束',
                description='用户过程数据、提示词、输入图片和生成媒体已按 90 天策略清理',
                roles=[],
                portrait_url='',
                video_url='',
                portrait_asset_id='',
                video_task_id='',
                portrait_source_url='',
                role_mapping=[],
                runtime_snapshot={},
                runtime_code='',
                template=safe_template,
                template_version='',
                late_video_url='',
                allinpay_checkout=None,
                prepay_id='',
                trade_order_no='',
                error='',
                subscribe_sent=False,
                subscribe_openid='',
                subscribe_template='',
                retention_cleanup='done',
                retention_cleanup_error='',
                retention_cleaned_at=time.time(),
                retention_next_at=0,
                media_cleanup='done',
                updated_at=time.time(),
            )
            self.store.put(record, db)
            self.store.event(record, 'retention', '数据保留期结束，用户过程数据已清理', db)
        service_log.event('system', '90天用户数据已清理', context=f'project={pid}')
        return True

    def run_once(self):
        total = 0
        deadline = time.monotonic() + RETENTION_TIME_BUDGET_SECONDS
        while time.monotonic() < deadline:
            projects = self.store.projects_due_retention(RETENTION_BATCH_SIZE)
            if not projects:
                break
            for project in projects:
                self.cleanup(project)
                total += 1
                if time.monotonic() >= deadline:
                    break
            if len(projects) < RETENTION_BATCH_SIZE:
                break
        return total

    def start(self):
        def loop():
            while not self.stop.wait(30):
                if time.time() - self.last_run < RETENTION_INTERVAL_SECONDS:
                    continue
                if not self._tick_lock.acquire(blocking=False):
                    continue
                try:
                    self.run_once()
                    self.last_run = time.time()
                except Exception:
                    service_log.event('system', '90天数据清理调度异常', context='retry scheduled')
                finally:
                    self._tick_lock.release()

        threading.Thread(target=loop, daemon=True, name='phase2-retention').start()
