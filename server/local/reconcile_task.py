"""Operator-only recovery. Attach an existing result; never creates a cloud task."""
import argparse
import os
import time
from sqlalchemy import text
from common.task_journal import TaskJournal
from common.media_refs import transform


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--task'); parser.add_argument('--cloud-task'); parser.add_argument('--image-file-url')
    args=parser.parse_args(); journal=TaskJournal()
    if not args.task:
        with journal.engine.connect() as db:
            for row in db.execute(text("SELECT id,kind,run_id,status FROM phase2_tasks WHERE status='needs_review'")):
                print(*row)
        return
    with journal.lock(args.task) as acquired:
        if not acquired: raise RuntimeError('任务正在处理')
        p=journal.get(args.task)
        if not p or p['status']!='needs_review': raise ValueError('任务不存在或不是待核实状态')
        if args.cloud_task and p['type']=='video':
            from volcenginesdkarkruntime import Ark
            api=Ark(api_key=os.environ['SEEDANCE_API_KEY'],base_url='https://ark.cn-beijing.volces.com/api/v3',max_retries=0)
            api.content_generation.tasks.get(task_id=args.cloud_task)  # Read-only, verify it exists in this account.
            p['steps']['video_submit']={'status':'done','result':args.cloud_task,'operator_verified_at':time.time()}
        elif args.image_file_url and p['type']=='image':
            from common.local_guard import validate_media_url
            import requests
            validate_media_url(args.image_file_url)
            if '/aitravel/api/v1/file/download/' not in args.image_file_url: raise ValueError('必须为本项目文件服务中的生成图片')
            response=requests.head(args.image_file_url,timeout=15,allow_redirects=False); response.raise_for_status()
            p['steps']['provider']={'status':'done','result':{'provider_url':'','model':'recovered','size':'1K'}}
            p['steps']['file']={'status':'done','result':transform(args.image_file_url,True),'operator_verified_at':time.time()}
        else:
            raise ValueError('视频请提供 --cloud-task；图片请提供已核实的 --image-file-url')
        p.update(status='recovering',next_run=0,error='',reconciled_at=time.time()); journal.put(p)
    if p['type']=='image':
        parent_id=p['request']['request_id'].split(':')[0]
        with journal.lock(parent_id) as acquired:
            parent=journal.get(parent_id)
            if acquired and parent and parent['status']=='needs_review':
                parent.update(status='recovering',next_run=0); journal.put(parent)
    print('原任务已恢复；没有新提交模型任务。')


if __name__=='__main__': main()
