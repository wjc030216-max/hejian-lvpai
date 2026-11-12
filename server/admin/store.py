"""Small operator repository sharing phase-two tables, never phase-one schema."""
import json
import hashlib
import time
import uuid
from sqlalchemy import text
from common.database_schema import engine_for, ensure_schema, template_identity

class AdminStore:
    def __init__(self,url):
        self.engine=engine_for(url,pool_size=3,max_overflow=2)
        ensure_schema(self.engine)

    def audit(self,db,actor,action,target,detail):
        db.execute(text('INSERT INTO phase2_admin_audit(id,actor,action,target,detail,created) VALUES(:id,:actor,:action,:target,:detail,:created)'),
            dict(id=uuid.uuid4().hex,actor=actor,action=action,target=target,detail=json.dumps(detail,ensure_ascii=False),created=time.time()))

    def reserve_login_attempt(self, source, limit=10, window=900):
        """Atomically count a login attempt so parallel requests cannot bypass the limit."""
        now=time.time()
        with self.engine.begin() as db:
            acquired=None
            if self.engine.dialect.name=='mysql':
                name='admin-login:'+hashlib.sha256(source.encode()).hexdigest()[:48]
                acquired=db.execute(text('SELECT GET_LOCK(:name,5)'),dict(name=name)).scalar()==1
                if not acquired: return False
            try:
                db.execute(text('DELETE FROM phase2_admin_attempts WHERE created<:cutoff'),
                           dict(cutoff=now-window))
                count=db.execute(text('SELECT COUNT(*) FROM phase2_admin_attempts WHERE source=:source'),
                                 dict(source=source)).scalar()
                if count>=limit: return False
                db.execute(text('INSERT INTO phase2_admin_attempts(id,source,created) VALUES(:id,:source,:created)'),
                           dict(id=uuid.uuid4().hex,source=source,created=now))
                return True
            finally:
                if acquired:
                    db.execute(text('SELECT RELEASE_LOCK(:name)'),dict(name=name))

    def clear_login_attempts(self, source):
        with self.engine.begin() as db:
            db.execute(text('DELETE FROM phase2_admin_attempts WHERE source=:source'),dict(source=source))

    def _template_filters(self,query='',status=None,actor_count=None,scene=None,duration_seconds=None):
        where=[]; params={}
        if query:
            where.append('payload_json LIKE :q'); params['q']='%'+query+'%'
        if status in (0,1):
            where.append('status=:status'); params['status']=status
        if actor_count in (1,2,3):
            where.append('actor_count=:actor_count'); params['actor_count']=actor_count
        if scene:
            where.append('location_association=:scene'); params['scene']=scene
        if duration_seconds in (15,30):
            if self.engine.dialect.name == 'mysql':
                duration_expr="CAST(JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.duration_seconds')) AS UNSIGNED)"
            else:
                duration_expr="CAST(json_extract(payload_json, '$.duration_seconds') AS INTEGER)"
            where.append(f'COALESCE({duration_expr},15)=:duration')
            params['duration']=duration_seconds
        return ((' WHERE '+' AND '.join(where)) if where else ''), params

    def _template_order(self,sort):
        if sort=='heat':
            return 'COALESCE(s.heat_base,0) DESC, t.updated_at DESC, t.id DESC'
        if sort=='name':
            expr=("JSON_UNQUOTE(JSON_EXTRACT(t.payload_json, '$.video_name'))"
                  if self.engine.dialect.name == 'mysql' else "json_extract(t.payload_json, '$.video_name')")
            return expr+' ASC, t.id ASC'
        return 't.updated_at DESC, t.id DESC'

    def list_templates(self,query='',offset=0,limit=30,status=None,actor_count=None,scene=None,
                       duration_seconds=None,sort='updated'):
        with self.engine.connect() as db:
            clause,filters=self._template_filters(query,status,actor_count,scene,duration_seconds)
            params=dict(filters,offset=offset,limit=limit)
            total=db.execute(text('SELECT COUNT(*) FROM phase2_templates t'+clause),params).scalar()
            rows=list(db.execute(text(
                'SELECT t.template_code,t.status,t.payload_json FROM phase2_templates t '
                'LEFT JOIN phase2_template_settings s ON s.template_code=t.template_code'
                +clause+' ORDER BY '+self._template_order(sort)+' LIMIT :limit OFFSET :offset'),
                params).mappings())
            status_counts={key:db.execute(text('SELECT COUNT(*) FROM phase2_templates'+suffix)).scalar()
                           for key,suffix in (('all',''),('published',' WHERE status=1'),('unpublished',' WHERE status=0'))}
            from sqlalchemy import bindparam
            codes=[r['template_code'] for r in rows]
            settings={r[0]:(r[1],bool(r[2]),r[3]) for r in db.execute(text('SELECT template_code,heat_base,show_heat,revision FROM phase2_template_settings WHERE template_code IN :codes').bindparams(bindparam('codes', expanding=True)),dict(codes=codes))}
            counts={r[0]:r[1] for r in db.execute(text('SELECT template_code,COUNT(*) FROM phase2_template_selections WHERE template_code IN :codes GROUP BY template_code').bindparams(bindparam('codes', expanding=True)),dict(codes=codes))}
        items=[]
        for row in rows:
            item=json.loads(row['payload_json']); code=row['template_code']; base,show,revision=settings.get(code,(0,False,1))
            runtime=item.pop('managed_runtime',None) or {}
            segments=runtime.get('segments') or []
            role_ids=[str(role.get('character_id') or '') for role in (
                (runtime.get('config_json') or {}).get('module_1',{}).get('character_settings') or []
            ) if role.get('character_id')]
            item['duration_seconds']=int(item.get('duration_seconds') or runtime.get('duration_seconds') or 15)
            item['segment_count']=int(item.get('segment_count') or runtime.get('segment_count') or len(segments) or 1)
            item['portrait_group']=runtime.get('portrait_group') or ''
            item['segments']=[{
                'code':str(segment.get('code') or ''),
                'total_seconds':int(segment.get('total_seconds') or 15),
                'character_count':len(segment.get('costume_url') or []),
                'characters':[
                    {'character_id':role_id,
                     'outfit_configured':bool(index < len(segment.get('costume_url') or [])
                                              and (segment.get('costume_url') or [])[index])}
                    for index,role_id in enumerate(role_ids)
                ],
                'has_storyboard':bool(segment.get('storyboard_url')),
            } for segment in segments]
            item.update(status=row['status'],heat_base=base,show_heat=show,real_selections=counts.get(code,0),display_selections=base+counts.get(code,0),edit_revision=revision)
            items.append(item)
        with self.engine.connect() as db:
            scenes=[row[0] for row in db.execute(text(
                "SELECT DISTINCT location_association FROM phase2_templates "
                "WHERE location_association IS NOT NULL AND location_association<>'' "
                "ORDER BY location_association"))]
        return dict(items=items,total=total,
                    next_offset=offset+limit if offset+limit<total else None,
                    scenes=scenes,status_counts=status_counts)

    def list_template_codes(self,query='',status=None,actor_count=None,scene=None,
                            duration_seconds=None,limit=2000):
        """Every template code matching the filters, for cross-page batch actions."""
        clause,filters=self._template_filters(query,status,actor_count,scene,duration_seconds)
        with self.engine.connect() as db:
            rows=db.execute(text('SELECT template_code FROM phase2_templates t'+clause+
                                 ' ORDER BY t.updated_at DESC, t.id DESC LIMIT :limit'),
                            dict(filters,limit=max(1,min(5000,int(limit))))).fetchall()
        return [row[0] for row in rows]

    def find_same_name(self,scene,name):
        with self.engine.connect() as db:
            return db.execute(text('SELECT template_code FROM phase2_templates WHERE identity_hash=:key'),dict(key=template_identity(scene,name))).scalar()
        return None

    def find_by_archive_hash(self,sha256):
        """同一份模板压缩包（内容哈希相同）只允许上传一次。"""
        if not sha256: return None
        with self.engine.connect() as db:
            row=db.execute(text(
                "SELECT template_code, JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.video_name')) "
                "FROM phase2_templates "
                "WHERE JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.source_sha256'))=:sha LIMIT 1"),
                dict(sha=sha256)).first()
        return (row[0], row[1] or '') if row else None

    def save_upload(self,item,actor,replace=True,publish=True):
        old_revision=None
        with self.engine.begin() as db:
            code=item['template_code']
            identity=template_identity(item['location_association'],item['video_name'])
            exists=db.execute(text('SELECT template_code FROM phase2_templates WHERE template_code=:code'),dict(code=code)).first()
            if exists and not replace:
                raise ValueError('模板编号已存在，批量导入已跳过以免覆盖上架版本')
            fields=dict(code=code,scene=item['location_association'],genre=item['video_genre'],count=item['actor_count'],identity=identity,payload=json.dumps(item,ensure_ascii=False))
            previous_status=0; previous_base=0; previous_show=False
            if exists:
                old_row=db.execute(text('SELECT payload_json FROM phase2_templates WHERE template_code=:code'),dict(code=code)).first()
                if old_row:
                    try: old_revision=(json.loads(old_row[0]) or {}).get('asset_revision')
                    except (ValueError,TypeError): old_revision=None
                current=db.execute(text('SELECT status FROM phase2_templates WHERE template_code=:code'),dict(code=code)).first()
                previous_status=current[0] if current else 0
                setting=db.execute(text('SELECT heat_base,show_heat,revision FROM phase2_template_settings WHERE template_code=:code'),dict(code=code)).first()
                if setting:
                    previous_base,previous_show=int(setting[0]),bool(setting[1])
                target_status=1 if publish else previous_status
                target_show=True if publish else previous_show
                db.execute(text('UPDATE phase2_templates SET payload_json=:payload,location_association=:scene,video_genre=:genre,actor_count=:count,identity_hash=:identity,status=:status,updated_at=CURRENT_TIMESTAMP WHERE template_code=:code'),
                           dict(fields,status=target_status))
                if setting:
                    db.execute(text('UPDATE phase2_template_settings SET show_heat=:show,revision=revision+1 WHERE template_code=:code'),dict(code=code,show=int(target_show)))
                else:
                    base=10000 if publish else 0
                    db.execute(text('INSERT INTO phase2_template_settings(template_code,heat_base,show_heat,revision) VALUES(:code,:base,:show,2)'),
                               dict(code=code,base=base,show=int(target_show)))
            else:
                status=1 if publish else 0
                db.execute(text('INSERT INTO phase2_templates(template_code,location_association,video_genre,actor_count,payload_json,identity_hash,status,sort_order,created_at,updated_at) VALUES(:code,:scene,:genre,:count,:payload,:identity,:status,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)'),
                           dict(fields,status=status))
                db.execute(text('INSERT INTO phase2_template_settings(template_code,heat_base,show_heat,revision) VALUES(:code,:base,:show,1)'),
                           dict(code=code,base=10000 if publish else 0,show=int(publish)))
            self.audit(db,actor,'上传模板版本',code,dict(
                asset_revision=item['asset_revision'],name=item['video_name'],
                duration_seconds=int(item.get('duration_seconds') or 15),
                segment_count=int(item.get('segment_count') or 1),
                status='已上架' if publish else '待上架',
                heat_base=10000 if publish else previous_base,
                show_heat=True if publish else previous_show))
        return old_revision

    def edit(self,code,body,actor):
        base=body.get('heat_base'); status=body.get('status',1); show=body.get('show_heat',True); revision=body.get('edit_revision')
        if isinstance(base,bool) or not isinstance(base,int) or not 0<=base<=100000000: raise ValueError('展示基数需为 0 至 1 亿的整数')
        if type(show) is not bool or type(status) is not int or status not in (0,1): raise ValueError('上架/热度开关无效')
        if type(revision) is not int: raise ValueError('缺少编辑版本，请刷新')
        with self.engine.begin() as db:
            lock=' FOR UPDATE' if self.engine.dialect.name=='mysql' else ''
            if not db.execute(text('SELECT template_code FROM phase2_templates WHERE template_code=:code'+lock),dict(code=code)).first(): raise ValueError('模板不存在')
            old=db.execute(text('SELECT heat_base,show_heat,revision FROM phase2_template_settings WHERE template_code=:code'+lock),dict(code=code)).first()
            if revision!=(old[2] if old else 1): raise ValueError('模板已被其他管理员修改，请刷新后再改')
            if old:
                db.execute(text('UPDATE phase2_template_settings SET heat_base=:base,show_heat=:show,revision=revision+1 WHERE template_code=:code'),dict(code=code,base=base,show=int(show)))
            else:
                db.execute(text('INSERT INTO phase2_template_settings(template_code,heat_base,show_heat,revision) VALUES(:code,:base,:show,2)'),dict(code=code,base=base,show=int(show)))
            db.execute(text('UPDATE phase2_templates SET status=:status,updated_at=CURRENT_TIMESTAMP WHERE template_code=:code'),dict(code=code,status=status))
            self.audit(db,actor,'调整上架与展示热度',code,dict(before=dict(heat_base=old[0] if old else 0,show_heat=bool(old[1]) if old else False),after=dict(heat_base=base,show_heat=show,status=status)))

    def batch_heat(self,codes,heat_base,actor):
        from sqlalchemy import bindparam
        codes=list(dict.fromkeys(codes))
        if not codes or len(codes)>200: raise ValueError('每次可选择 1 至 200 个模板')
        if isinstance(heat_base,bool) or not isinstance(heat_base,int) or not 0<=heat_base<=100000000:
            raise ValueError('热度需为 0 至 1 亿的整数')
        with self.engine.begin() as db:
            existing={row[0] for row in db.execute(text(
                'SELECT template_code FROM phase2_templates WHERE template_code IN :codes'
            ).bindparams(bindparam('codes',expanding=True)),dict(codes=codes))}
            missing=[code for code in codes if code not in existing]
            if missing: raise ValueError('部分模板已不存在，请刷新后重试')
            settings={row[0]:(row[1],row[2]) for row in db.execute(text(
                'SELECT template_code,heat_base,show_heat FROM phase2_template_settings '
                'WHERE template_code IN :codes'
            ).bindparams(bindparam('codes',expanding=True)),dict(codes=codes))}
            for code in codes:
                old=settings.get(code)
                if old:
                    db.execute(text(
                        'UPDATE phase2_template_settings SET heat_base=:heat,show_heat=1,revision=revision+1 '
                        'WHERE template_code=:code'
                    ),dict(code=code,heat=heat_base))
                else:
                    db.execute(text(
                        'INSERT INTO phase2_template_settings(template_code,heat_base,show_heat,revision) '
                        'VALUES(:code,:heat,1,1)'
                    ),dict(code=code,heat=heat_base))
                self.audit(db,actor,'批量调整模板热度',code,dict(
                    before=dict(heat_base=old[0] if old else 0,show_heat=bool(old[1]) if old else False),
                    after=dict(heat_base=heat_base,show_heat=True,status=1)))
        return len(codes)

    def batch_status(self,codes,status,actor):
        from sqlalchemy import bindparam
        codes=list(dict.fromkeys(codes))
        if not codes or len(codes)>200: raise ValueError('每次可选择 1 至 200 个模板')
        if type(status) is not int or status not in (0,1): raise ValueError('上架状态无效')
        with self.engine.begin() as db:
            existing={row[0]:row[1] for row in db.execute(text(
                'SELECT template_code,status FROM phase2_templates WHERE template_code IN :codes'
            ).bindparams(bindparam('codes',expanding=True)),dict(codes=codes))}
            missing=[code for code in codes if code not in existing]
            if missing: raise ValueError('部分模板已不存在，请刷新后重试')
            for code in codes:
                db.execute(text(
                    'UPDATE phase2_templates SET status=:status,updated_at=CURRENT_TIMESTAMP '
                    'WHERE template_code=:code'),dict(code=code,status=status))
                self.audit(db,actor,'批量上架模板' if status else '批量下架模板',code,dict(
                    before=dict(status=int(existing[code])),after=dict(status=status)))
        return len(codes)

    def delete_templates(self,codes,actor):
        from sqlalchemy import bindparam
        codes=list(dict.fromkeys(codes))
        with self.engine.begin() as db:
            rows=list(db.execute(text('SELECT template_code,payload_json FROM phase2_templates WHERE template_code IN :codes').bindparams(bindparam('codes',expanding=True)),dict(codes=codes)))
            existing=[r[0] for r in rows]
            if not existing: raise ValueError('所选模板已不存在，请刷新后重试')
            names=[]; revisions=[]
            for code,payload in rows:
                try:
                    data=json.loads(payload) or {}
                    names.append(data.get('video_name') or code)
                    revisions.append(data.get('asset_revision'))
                except (ValueError,TypeError):
                    names.append(code); revisions.append(None)
            db.execute(text('DELETE FROM phase2_template_settings WHERE template_code IN :codes').bindparams(bindparam('codes',expanding=True)),dict(codes=existing))
            db.execute(text('DELETE FROM phase2_template_selections WHERE template_code IN :codes').bindparams(bindparam('codes',expanding=True)),dict(codes=existing))
            db.execute(text('DELETE FROM phase2_templates WHERE template_code IN :codes').bindparams(bindparam('codes',expanding=True)),dict(codes=existing))
            self.audit(db,actor,'删除模板',str(len(existing))+'个模板',dict(codes=existing,names=names,count=len(existing)))
        return len(existing),[r for r in revisions if isinstance(r,str)]

    def is_revision_referenced(self,revision):
        from sqlalchemy import inspect as sqla_inspect
        pattern='%'+revision+'%'
        with self.engine.connect() as db:
            for table,column in [('phase2_projects','payload'),('phase2_tasks','payload')]:
                if not sqla_inspect(db).has_table(table): continue
                if db.execute(text('SELECT 1 FROM '+table+' WHERE '+column+' LIKE :p LIMIT 1'),dict(p=pattern)).first(): return True
        return False
