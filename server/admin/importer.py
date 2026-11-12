"""Validate an immutable template revision before it can enter the public catalogue."""
import json
import hashlib
import os
import re
import shutil
import stat
import uuid
import zipfile
from pathlib import Path,PurePosixPath
from urllib.parse import quote
from PIL import Image
from phase2.runtime_templates import parse_duration_seconds, runtime_from_folder

MEDIA={'.jpg','.jpeg','.png','.webp','.mp4','.mp3','.wav'}
ALLOWED=MEDIA|{'.txt','.json'}
WINDOWS_JUNK={'thumbs.db','desktop.ini'}

class _SeekableUpload:
    def __init__(self,stream): self.stream=stream
    def seekable(self): return True
    def __getattr__(self,name): return getattr(self.stream,name)

def batch_code(name):
    filename=Path(str(name).replace('\\','/')).name
    stem=filename[:-4] if filename.lower().endswith('.zip') else filename
    return 'P2B-'+hashlib.sha256(stem.encode('utf-8')).hexdigest()[:24]

def _validate_makeup_files(folder, settings):
    """Validate optional 12.<role>.<group>-妆造提示词.txt files before publishing."""
    import json as _json
    files = sorted(
        item for item in folder.iterdir()
        if item.is_file() and item.suffix.lower() == '.txt'
        and item.name.startswith('12.') and '妆造' in item.name
    )
    if not files:
        return
    ordered_ids = [str(setting.get('character_id') or '').strip() for setting in settings]
    ids = set(ordered_ids)
    seen = set()
    for path in files:
        match = re.match(r'12\.(\d+)\.([A-Za-z0-9]+)-.*\.txt$', path.name)
        if not match:
            raise ValueError('妆造文件命名需为 12.<角色序号>.<片段组>-妆造提示词.txt：' + path.name)
        index, group = int(match.group(1)), match.group(2).upper()
        if not 1 <= index <= len(ordered_ids):
            raise ValueError(f'{path.name} 的角色序号超出模板角色数量')
        if (index, group) in seen:
            raise ValueError(f'角色 {index} 的片段组 {group} 妆造文件重复')
        seen.add((index, group))
        expected_id = ordered_ids[index - 1]
        raw = path.read_bytes()
        data = None
        for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
            try:
                data = _json.loads(raw.decode(encoding))
                break
            except (UnicodeError, _json.JSONDecodeError):
                continue
        if not isinstance(data, dict):
            raise ValueError(path.name + ' 不是有效的 JSON')
        owner = str(data.get('character_id') or '').strip()
        if owner and owner not in ids:
            raise ValueError(path.name + ' 的 character_id 与模板角色不一致')
        if owner and owner != expected_id:
            raise ValueError(f'{path.name} 对应第 {index} 个角色({expected_id})，但文件内是 {owner}')
        makeup = data.get('makeup') or data.get('面部妆容')
        if not isinstance(makeup, (dict, str)) or not makeup:
            raise ValueError(path.name + ' 缺少 makeup 内容')


def import_archive(stream,root,template_code=None,optimize=True):
    if not hasattr(stream,'seekable'):
        stream=_SeekableUpload(stream)
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    revision=uuid.uuid4().hex; folder=root/revision; folder.mkdir()
    try:
        with zipfile.ZipFile(stream) as archive:
            files=[]; size=0; names=set()
            for item in archive.infolist():
                if item.is_dir(): continue
                # Older Windows archivers sometimes omit the UTF-8 flag and
                # store Chinese names as CP437 bytes. Try GB18030 before
                # applying the path safety checks.
                name=item.filename
                if not item.flag_bits & 0x800:
                    try: name=name.encode('cp437').decode('gb18030')
                    except UnicodeError: pass
                path=PurePosixPath(name)
                if '__MACOSX' in path.parts or path.name.startswith('.') or path.name.casefold() in WINDOWS_JUNK: continue
                if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or stat.S_ISLNK(item.external_attr>>16):
                    raise ValueError('压缩包中有不安全的文件路径')
                if path.suffix.lower() not in ALLOWED: raise ValueError('模板含不支持的文件类型：'+path.suffix)
                if len(path.parts)>2: raise ValueError('请将单个模板文件夹打包，不要嵌套多个文件夹')
                if path.name.casefold() in names: raise ValueError('模板中有重名文件')
                names.add(path.name.casefold()); size+=item.file_size; files.append((item,path))
                if size>256*1024*1024 or len(files)>100: raise ValueError('解压后最多 256MB、100 个文件')
            parents={str(path.parent) for _,path in files}
            if len(parents)!=1: raise ValueError('每次只能上传一个模板文件夹')
            for item,path in files:
                target=folder/path.name
                with archive.open(item) as src,target.open('wb') as dst:
                    total=0
                    while True:
                        chunk=src.read(1024*1024)
                        if not chunk: break
                        total+=len(chunk)
                        if total>item.file_size or total>128*1024*1024: raise ValueError('模板单个文件过大')
                        dst.write(chunk)
                if target.suffix.lower() in {'.jpg','.jpeg','.png','.webp'}:
                    with Image.open(target) as image: image.verify()
        path=folder/'1-基础元数据.txt'
        if not path.exists(): path=folder/'metadata.txt'
        if not path.exists(): raise ValueError('缺少 1-基础元数据.txt')
        try: data=json.loads(path.read_text(encoding='utf-8-sig'))
        except (UnicodeError,json.JSONDecodeError) as exc: raise ValueError('基础元数据不是有效的 UTF-8 JSON：'+str(exc)) from exc
        if not isinstance(data,dict): raise ValueError('基础元数据顶层必须是对象')
        one=data.get('module_1') or {}; two=data.get('module_2') or {}; persona=two.get('recommendation_persona') or {}
        if not all(isinstance(v,dict) for v in (one,two,persona)):
            raise ValueError('module_1、module_2、recommendation_persona 必须是对象')
        count=one.get('actor_count'); settings=one.get('character_settings') or []
        if not isinstance(settings,list) or not all(isinstance(v,dict) for v in settings):
            raise ValueError('character_settings 必须是角色对象列表')
        if isinstance(count,str) and count.isdigit(): count=int(count)
        if not isinstance(count,int) or isinstance(count,bool) or not 1<=count<=3 or len(settings)!=count: raise ValueError('模板人数需为 1 至 3，与角色数一致')
        ids=[s.get('character_id') for s in settings]
        if not all(isinstance(x,str) and x.strip() for x in ids) or len(set(ids))!=count: raise ValueError('角色编号缺失或重复')
        for key in ('video_name','story_hook'):
            if not isinstance(one.get(key),str) or not one[key].strip(): raise ValueError('基础元数据缺少 '+key)
            if len(one[key])>500: raise ValueError(key+' 最多 500 字')
        for role in settings:
            if not isinstance(role.get('display_name'),str) or not 1<=len(role['display_name'].strip())<=80:
                raise ValueError('每个角色需要 1 至 80 字的 display_name')
            if len(role['character_id'])>128: raise ValueError('角色编号最多 128 字')
        for key in ('recommended_gender','recommended_age'):
            if key in persona and (not isinstance(persona[key],str) or len(persona[key])>80):
                raise ValueError(key+' 必须是 80 字以内的文字')
        import re
        declared=str(two.get('actor_count',count)).replace('人','').strip()
        match=re.fullmatch(r'(\d+)(?:[-～~](\d+))?',declared)
        if not match or not int(match[1])<=count<=int(match[2] or match[1]):
            raise ValueError('人数分类不包含实际角色数量')
        try:
            duration_seconds = parse_duration_seconds(two.get('video_specification', '15秒'))
        except ValueError as exc:
            raise ValueError('视频规格无效：' + str(exc)) from None
        if duration_seconds not in (15, 30):
            raise ValueError('当前仅支持 15 秒或 30 秒（两个 15 秒片段）视频')
        scenic=persona.get('location_association')
        if not isinstance(scenic,str) or not scenic.strip() or len(scenic)>128:
            raise ValueError('景区名称必须为 1 至 128 字')
        if not isinstance(two.get('video_genre'),str) or not two['video_genre']: raise ValueError('缺少视频类型')
        if not isinstance(two.get('relationship_tags'),list) or len(two['relationship_tags'])>20 or not all(isinstance(x,str) and 1<=len(x.strip())<=80 for x in two['relationship_tags']): raise ValueError('关系标签最多 20 个，每个为 1 至 80 字文字')
        def media_url(_folder,name): return 'local-media:assets/managed/'+revision+'/'+quote(name,safe='')
        code=template_code or 'P2T-'+uuid.uuid4().hex[:20]
        template=dict(template_code=code,video_name=one['video_name'],story_hook=one['story_hook'],actor_count=count,
            character_settings=[dict(x,person_uid=str(x.get('person_uid') or i+1)) for i,x in enumerate(settings)],
            video_genre=two['video_genre'],relationship_tags=two['relationship_tags'],location_association=scenic,
            video_specification=two.get('video_specification','15秒'),duration_seconds=duration_seconds,
            segment_count=duration_seconds//15,lead_gender_tags=[persona.get('recommended_gender','不限')],
            age_tags=[persona.get('recommended_age','不限')],asset_revision=revision,source_metadata=data)
        if len({x['person_uid'] for x in template['character_settings']})!=count: raise ValueError('角色坑位编号重复')
        def first(prefix,extensions):
            matches=[p for p in folder.iterdir() if p.name.startswith(prefix) and p.suffix.lower() in extensions]
            if len(matches)!=1: raise ValueError('需要唯一的 '+prefix+' 文件')
            return matches[0]
        _validate_makeup_files(folder,settings)
        cover=first('6-', {'.png','.jpg','.jpeg','.webp'}); video=first('7-',{'.mp4'})
        if optimize:
            # 视频保持原样交付，只有封面图做压缩。
            video_optimize = os.getenv('PHASE2_TEMPLATE_VIDEO_OPTIMIZE', 'false').lower() == 'true'
            if video_optimize:
                from admin.optimize_template_previews import PreviewError,optimize_template_preview
                try: template['preview_media'] = optimize_template_preview(video)
                except (PreviewError,OSError) as error: template['preview_media']={'status':'failed','error':str(error)[:300]}
            else:
                template['preview_media'] = {'status': 'skipped', 'reason': 'video optimization disabled'}
            if os.getenv('PHASE2_TEMPLATE_COVER_OPTIMIZE', 'true').lower() != 'false':
                from admin.optimize_template_previews import optimize_cover
                try: template['cover_media'] = optimize_cover(cover)
                except (OSError,ValueError) as error: template['cover_media']={'status':'failed','error':str(error)[:300]}
        with Image.open(cover) as image:
            if image.width>=image.height or abs(image.width/image.height-9/16)>0.04:
                raise ValueError('封面必须为 9:16 竖屏图片')
        if os.getenv('PHASE2_SKIP_MEDIA_VALIDATION') == 'true':
            if os.getenv('APP_ENV') in ('staging', 'production'):
                raise ValueError('正式环境禁止跳过示例视频校验')
            template['sample_media'] = {'validation_skipped': True}
        else:
            from common.media_validation import inspect_video
            template['sample_media']=inspect_video(video)
            sample=template['sample_media']
            if sample.get('width') and sample.get('height'):
                ratio=sample['width']/sample['height']
                if abs(ratio-9/16)>0.04:
                    raise ValueError('示例视频必须为 9:16 竖屏')
            actual_duration=float(sample.get('duration') or 0)
            if actual_duration<=0 or abs(actual_duration-duration_seconds)>1.0:
                raise ValueError(
                    f'示例视频时长 {actual_duration:.2f} 秒与模板声明的 {duration_seconds} 秒不一致')
        template.update(cover=media_url(folder,cover.name),example=media_url(folder,video.name),
                        managed_runtime=runtime_from_folder(template,folder,media_url))
        return template
    except Exception:
        # This path was created from our UUID under the configured template volume only.
        shutil.rmtree(folder)
        raise
