"""Automatic full refunds for paid generation failures."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid

from common import service_log
from phase2.allinpay import Allinpay, AllinpayError, AllinpayResponseError


SUCCESS_STATES = {'2', 'SUCCESS', 'TRX_SUCCESS', 'REFUND_SUCCESS'}
FAILED_STATES = {'1', 'FAILED', 'FAIL', 'TRX_FAILED', 'REFUND_FAILED'}


class RefundService:
    def __init__(self, store):
        self.store = store
        self.stop = threading.Event()
        self._tick_lock = threading.Lock()

    @staticmethod
    def _refund_no(project_id):
        return 'RF' + time.strftime('%Y%m%d%H%M%S') + uuid.uuid4().hex[:10].upper()

    def ensure_requested(self, project_id):
        with self.store.transaction() as db:
            project = self.store.get(project_id, db)
            if not project or project.get('status') != 'refund_pending':
                return self.store.refund_for_project(project_id, db)
            existing = self.store.refund_for_project(project_id, db)
            if existing:
                return existing
            if project.get('payment_mode') == 'dev-bypass':
                project.update(refund_status='not_charged', status='terminated',
                               title='制作未完成，未产生扣款')
                self.store.put(project, db)
                return None
            now = time.time()

            def unresolved_refund(message):
                refund = {
                    'refund_no': self._refund_no(project_id),
                    'project_id': project_id,
                    'order_no': project.get('order_no') or '',
                    'amount_fen': max(0, int(project.get('amount_paid') or 0)),
                    'status': 'needs_review',
                    'attempts': 0,
                    'next_run': 0,
                    'provider_order_no': None,
                    'provider_trxid': None,
                    'provider_trxstatus': None,
                    'error_code': None,
                    'error_message': message,
                    'request_payload': None,
                    'response_payload': None,
                    'requested_at': now,
                    'updated_at': now,
                    'finished_at': None,
                    'operator': 'user',
                }
                project.update(refund_status='needs_review', refund_no=refund['refund_no'],
                               description=message)
                self.store.put(project, db)
                self.store.put_refund(refund, db)
                return refund

            if project.get('payment_mode') != 'allinpay':
                return unresolved_refund('支付渠道不是通联，退款待人工核对')
            if project.get('payment_status') != 'paid' or not project.get('order_no'):
                return unresolved_refund('原支付信息不完整，退款待人工核对')
            amount = int(project.get('amount_paid') or 0)
            if amount <= 0:
                return unresolved_refund('实付金额无效，退款待人工核对')
            refund = {
                'refund_no': self._refund_no(project_id),
                'project_id': project_id,
                'order_no': project['order_no'],
                'amount_fen': amount,
                'status': 'pending',
                'attempts': 0,
                'next_run': 0,
                'provider_order_no': None,
                'provider_trxid': None,
                'provider_trxstatus': None,
                'error_code': None,
                'error_message': None,
                'request_payload': json.dumps({
                    'order_no': project['order_no'],
                    'amount_fen': amount,
                    'refund_no': None,
                }, ensure_ascii=False),
                'response_payload': None,
                'requested_at': now,
                'updated_at': now,
                'finished_at': None,
                'operator': 'user',
            }
            refund['request_payload'] = json.dumps({
                'order_no': project['order_no'],
                'amount_fen': amount,
                'refund_no': refund['refund_no'],
            }, ensure_ascii=False)
            project.update(refund_status='requested', refund_no=refund['refund_no'],
                           title='退款处理中',
                           description='系统正在发起全额原路退款，请稍候',
                           updated_at=now)
            self.store.put(project, db)
            self.store.put_refund(refund, db)
            return refund

    def retry(self, refund_no, operator='admin'):
        record = self.store.get_refund(refund_no)
        if not record or record.get('status') not in ('needs_review', 'failed'):
            raise ValueError('当前退款状态不可重试')
        record.update(status='pending', next_run=0, error_code=None,
                      error_message=None, operator=operator, updated_at=time.time())
        self.store.put_refund(record)
        project = self.store.get(record['project_id'])
        if project:
            project.update(status='refund_pending', refund_status='requested',
                           title='退款处理中', description='正在使用原退款单号重新查询或提交')
            self.store.put(project)
        service_log.event('payment', '退款单进入重试', context='refund=' + refund_no)
        return record

    def resolve(self, refund_no, status, operator, provider_order_no='', provider_trxid='',
                note=''):
        if status not in ('succeeded', 'failed', 'needs_review'):
            raise ValueError('退款处理状态无效')
        with self.store.transaction() as db:
            record = self.store.get_refund(refund_no, db)
            if not record:
                raise ValueError('退款记录不存在')
            project = self.store.get(record['project_id'], db)
            now = time.time()
            record.update(status=status, operator=str(operator or '')[:80],
                          provider_order_no=provider_order_no or record.get('provider_order_no'),
                          provider_trxid=provider_trxid or record.get('provider_trxid'),
                          updated_at=now,
                          finished_at=now if status in ('succeeded', 'failed') else None)
            if note:
                record['error_message'] = str(note)[:2000]
            if status == 'succeeded':
                project.update(status='refunded', refund_status='succeeded',
                               title='退款成功，作品已归档',
                               description='全额退款已完成，作品已停止播放、保存和分享',
                               refunded_at=now, next_run_at=0,
                               media_cleanup='pending', media_cleanup_attempts=0,
                               media_cleanup_error='')
                self.store.event(project, 'refund', '生成失败退款成功，作品已归档', db)
            elif status == 'failed':
                project.update(status='refund_failed', refund_status='failed',
                               title='退款失败', description='退款未完成，请联系客服处理',
                               next_run_at=0)
                self.store.event(project, 'refund', '退款失败，需要人工处理', db)
            else:
                project.update(status='refund_pending', refund_status='needs_review',
                               title='退款状态待核实',
                               description='退款请求结果需人工核对，请勿重复提交')
            self.store.put(project, db)
            self.store.put_refund(record, db)
            return record

    def process(self, refund_no):
        with self.store.creation_lock('refund:' + refund_no):
            with self.store.transaction() as db:
                record = self.store.get_refund(refund_no, db)
                if not record or record.get('status') not in ('pending', 'retry', 'processing'):
                    return
                reconciling = record.get('status') == 'processing'
                record.update(status='submitting', attempts=int(record.get('attempts') or 0) + 1,
                              next_run=time.time() + 600, updated_at=time.time())
                self.store.put_refund(record, db)
            self._submit(record, reconciling)

    def _submit(self, record, reconciling=False):
        refund_no = record['refund_no']
        try:
            result = Allinpay().refund(
                record['order_no'], record['amount_fen'], refund_no,
                '生成失败全额退款',
            )
        except AllinpayResponseError as error:
            with self.store.transaction() as db:
                current = self.store.get_refund(refund_no, db)
                if not current:
                    return
                final_status = 'needs_review' if reconciling else 'failed'
                current.update(status=final_status, error_code=getattr(error, 'code', '') or None,
                               error_message=str(error)[:2000], updated_at=time.time(),
                               finished_at=None if reconciling else time.time())
                project = self.store.get(current['project_id'], db)
                if project:
                    if reconciling:
                        project.update(status='refund_pending', refund_status='needs_review',
                                       title='退款状态待核实',
                                       description='原退款单号核对未得到明确结果，请人工核对',
                                       next_run_at=0)
                    else:
                        project.update(status='refund_failed', refund_status='failed',
                                       title='退款失败',
                                       description='通联明确拒绝退款，请联系客服处理',
                                       next_run_at=0)
                        self.store.event(project, 'refund', '退款失败，需要人工处理', db)
                    self.store.put(project, db)
                self.store.put_refund(current, db)
            service_log.event('payment', '退款失败', context=f'refund={refund_no}')
            return
        except AllinpayError as error:
            with self.store.transaction() as db:
                current = self.store.get_refund(refund_no, db)
                if not current or current.get('status') != 'submitting':
                    return
                current.update(status='needs_review', error_message=str(error)[:2000],
                               updated_at=time.time())
                project = self.store.get(current['project_id'], db)
                if project:
                    project.update(status='refund_pending', refund_status='needs_review',
                                   title='退款状态待核实',
                                   description='通联退款结果未确认，请勿重复提交')
                    self.store.put(project, db)
                self.store.put_refund(current, db)
            service_log.event('payment', '退款待核实', context=f'refund={refund_no}')
            return
        trxstatus = str(result.get('trxstatus') or '').upper()
        succeeded = trxstatus in SUCCESS_STATES or (not trxstatus and bool(result.get('fintime')))
        final = 'succeeded' if succeeded else 'failed' if trxstatus in FAILED_STATES else 'processing'
        with self.store.transaction() as db:
            current = self.store.get_refund(refund_no, db)
            if not current:
                return
            current.update(
                status=final,
                provider_order_no=str(result.get('orderNo') or ''),
                provider_trxid=str(result.get('trxid') or ''),
                provider_trxstatus=trxstatus,
                error_code=None,
                error_message=None,
                response_payload=json.dumps(result, ensure_ascii=False),
                updated_at=time.time(),
                finished_at=time.time() if final in ('succeeded', 'failed') else None,
            )
            project = self.store.get(current['project_id'], db)
            if final == 'succeeded':
                project.update(status='refunded', refund_status='succeeded',
                               title='退款成功，作品已归档',
                               description='全额退款已完成，作品已停止播放、保存和分享',
                               refunded_at=time.time(), next_run_at=0,
                               media_cleanup='pending', media_cleanup_attempts=0,
                               media_cleanup_error='')
                self.store.event(project, 'refund', '生成失败退款成功，作品已归档', db)
            elif final == 'failed':
                project.update(status='refund_failed', refund_status='failed',
                               title='退款失败', description='退款未完成，请联系客服处理',
                               next_run_at=0)
                self.store.event(project, 'refund', '退款失败，需要人工处理', db)
            else:
                project.update(status='refund_pending', refund_status='processing',
                               title='退款处理中', description='通联已受理全额退款，等待最终结果',
                               next_run_at=time.time())
                current['next_run'] = time.time() + 300
            self.store.put(project, db)
            self.store.put_refund(current, db)
        service_log.event('payment', '退款请求已处理', context=f'refund={refund_no} result={final}')

    def cleanup_media(self, project):
        from common.storage_client import (
            delete_asset, delete_file_url, delete_local_video,
            delete_portrait, delete_result,
        )
        from common.video_asset_client import delete_video_asset
        from phase2.retention_service import RetentionService, url_strings
        errors = []
        def media_id(field, url_field, pattern):
            value = project.get(field)
            if value:
                return str(value)
            match = re.search(pattern, str(project.get(url_field) or ''))
            return match.group(1) if match else ''
        for kind, result_id, operation in (
            ('portrait', media_id(
                'portrait_asset_id', 'portrait_url', r'/api/v2/portraits/([0-9a-f]{32})'),
             delete_portrait),
            ('video', media_id(
                'video_task_id', 'video_url', r'/api/v2/results/([0-9a-f]{32})'),
             delete_result),
        ):
            if not result_id:
                continue
            try:
                operation(result_id)
            except Exception as error:
                errors.append(f'{kind}:{type(error).__name__}')
        runtime_code = project.get('runtime_code') or ''
        tasks = self.store.task_records_for_project(project['project_id'], runtime_code)
        urls = list(url_strings(project))
        for task in tasks:
            urls.extend(url_strings(task))
        asset_ids = {
            role.get('photo_asset_id') for role in project.get('roles') or []
            if str(role.get('photo_asset_id') or '').startswith(('asset_', 'file_'))
        }
        asset_ids.update(RetentionService._tos_asset_ids(urls))
        for asset_id in sorted(asset_ids):
            try:
                delete_asset(asset_id)
            except Exception as error:
                errors.append(f'asset:{type(error).__name__}')
        for url in sorted(set(urls)):
            if '/api/v1/file/download/' not in url:
                continue
            try:
                delete_file_url(url)
            except Exception as error:
                errors.append(f'file:{type(error).__name__}')
        for asset_id in sorted(RetentionService._asset_ids(tasks)):
            try:
                delete_video_asset(asset_id)
            except Exception as error:
                errors.append(f'video-asset:{type(error).__name__}')
        video_id = media_id(
            'video_task_id', 'video_url', r'/api/v2/results/([0-9a-f]{32})')
        if video_id:
            try:
                delete_local_video(project.get('wx_openid') or '', video_id)
            except Exception as error:
                errors.append(f'local-video:{type(error).__name__}')
        if errors:
            self.store.mark_media_cleanup(project['project_id'], False, ','.join(errors))
            service_log.event('payment', '退款媒体清理待重试',
                              context=f"project={project['project_id']} errors={','.join(errors)}")
        else:
            self.store.mark_media_cleanup(project['project_id'], True)
            service_log.event('payment', '退款媒体对象已删除',
                              context=f"project={project['project_id']}")

    def start(self):
        def loop():
            while not self.stop.wait(3):
                if not self._tick_lock.acquire(blocking=False):
                    continue
                try:
                    self.store.recover_stale_refunds(600)
                    for project in self.store.projects_awaiting_refund(20):
                        self.ensure_requested(project['project_id'])
                    for record in self.store.due_refunds(4):
                        self.process(record['refund_no'])
                    for project in self.store.pending_media_cleanup(2):
                        self.cleanup_media(project)
                except Exception:
                    service_log.event('payment', '退款调度异常', context='retry scheduled')
                finally:
                    self._tick_lock.release()
        threading.Thread(target=loop, daemon=True, name='phase2-refunds').start()
