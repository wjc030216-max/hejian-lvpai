"""Phase-two role binding and idempotent image-service client."""
import os
import time

import requests

from common.internal_auth import local_headers
from common import http_client
from common.template_client import runtime_model, runtime_template
from common.task_journal import CloudFailed, UnknownSubmission
PROMPT_STEP1_BASE = (
    "将图1照片中的主体人物换上图2服装设定三视图的服装、配饰和造型（最大化保持照片中主体人物的脸型、五官等等人脸长相特点）；"
    "同时将图1照片中的主体人物的脸加上下方妆造提示词的妆造内容，对图1照片中的主体人物加上妆造时严格保持参考图照片中人物的五官、脸型、"
    "脸型比例、肤色、发色、发型与原始肤质纹理完全不变，从始至终保持是同一个人、将妆容自然合理地迁移到参考图照片的人脸上，"
    "妆容需自然贴合原图光影与肤色，不改变眼型、鼻型、唇形与面部轮廓，保持高写实人像质感。不要改变长相，不要过度磨皮，不要卡通化。"
    "移除服装设定三视图中的白模假人，补全照片中主体人物显露的部分比如手部、手臂、脚部等等，创建照片中的主体人物换装后的人物设定三视图"
    "（并同时根据照片主体人物的人脸长相体征调整人物设定三视图中人物的身高、比例和体型使其与人脸长相自然适配不违和）。"
    "人物设定三视图为写实摄影风格，采用二分构图，画面左半边是标准胸部以上半身像（带有对应服装配饰），展示人物换装后的头部特写；"
    "画面右半边是人物换装后的全身正侧背三视图，从头到脚展示。"
    "角色面部受光均匀，画面内无任何文字描述，纯白色背景。"
)

PROMPT_STEP1_LEGACY = (
    "将图片1照片中的主体人物换上图片2服装设定三视图的服装、配饰和造型"
    "（最大化保持照片中主体人物的脸型、五官等等人脸长相特点）。"
    "移除服装设定三视图中的白模假人，补全照片中主体人物显露的部分比如手部、手臂、脚部等等，"
    "创建照片中的主体人物换装后的人物设定三视图"
    "（并同时根据照片主体人物的人脸长相体征调整人物设定三视图中人物的身高、比例和体型"
    "使其与人脸长相自然适配不违和）。"
    "人物设定三视图为写实摄影风格，采用二分构图，画面左半边是标准胸部以上半身像，展示人物换装后的头部特写；"
    "画面右半边是人物换装后的全身正侧背三视图，从头到脚展示。"
    "角色面部受光均匀，画面内无任何文字描述，纯白色背景。"
)



PROMPT_STEP1 = PROMPT_STEP1_LEGACY


FACE_LOCK = ('【硬性要求】画面左半边的头部特写是唯一人脸参考；画面右半边三个全身人物的面部统一做高斯模糊处理，'
             '不显示清晰五官与皮肤细节，只保留发型、服装、体型、姿态信息，供换装与体型参考。')


def step1_prompt(makeup=''):
    """新起手式：换装 + 妆造；没有妆造文件时退回旧起手式。"""
    makeup = str(makeup or '').strip()
    if not makeup:
        return PROMPT_STEP1_LEGACY + chr(10) + FACE_LOCK
    return PROMPT_STEP1_BASE + chr(10) + '妆造提示词：' + chr(10) + makeup + chr(10) + FACE_LOCK


def _build_role_bindings(actors, character_ids, costume_urls, default_actor_urls=None):
    """按 character_id 建立不可变角色映射，禁止依赖用户照片数组位置。"""
    if not character_ids:
        raise RuntimeError("模板缺少 character_id，无法安全绑定角色")
    if len(set(character_ids)) != len(character_ids):
        raise RuntimeError(f"模板角色 character_id 重复: {character_ids}")
    if not isinstance(costume_urls, list):
        costume_urls = [costume_urls]
    if len(costume_urls) != len(character_ids):
        raise RuntimeError(
            f"模板服装数量({len(costume_urls)})与角色数量({len(character_ids)})不一致"
        )

    actor_by_id = {}
    actor_without_id = []
    for actor in actors or []:
        if not isinstance(actor, dict) or not actor.get('image'):
            continue
        character_id = str(actor.get('character_id') or '').strip()
        if character_id:
            if character_id in actor_by_id:
                raise RuntimeError(f"用户角色照片重复绑定: {character_id}")
            actor_by_id[character_id] = actor
        else:
            actor_without_id.append(actor)

    if actor_without_id:
        missing_ids = [cid for cid in character_ids if cid not in actor_by_id]
        if len(actor_without_id) == 1 and len(missing_ids) == 1:
            actor_by_id[missing_ids[0]] = actor_without_id[0]
        else:
            raise RuntimeError("用户照片缺少 character_id，无法安全匹配模板角色")

    missing = [cid for cid in character_ids if cid not in actor_by_id]
    if missing:
        raise RuntimeError(f"用户照片缺失，禁止使用模板默认人物: {', '.join(missing)}")

    if set(actor_by_id) != set(character_ids):
        raise RuntimeError('照片包含不属于本模板的角色')
    defaults = dict(zip(character_ids, default_actor_urls or []))
    template_actors = [a for a in actor_by_id.values() if a.get('source') == 'template']
    if template_actors and len(template_actors) != len(character_ids) - 1:
        raise RuntimeError('一人体验必须且只能有一位游客角色')
    for cid, actor in actor_by_id.items():
        if actor.get('source', 'user_face') not in ('template', 'user_face'):
            raise RuntimeError('角色照片来源无效')
        if actor.get('source') == 'template' and (not defaults.get(cid) or actor['image'] != defaults[cid]):
            raise RuntimeError('模板人物必须使用该角色对应的模板素材')

    costume_by_id = dict(zip(character_ids, costume_urls))
    return [
        {
            'role_index': index + 1,
            'character_id': character_id,
            'actor': actor_by_id[character_id],
            'costume_url': costume_by_id[character_id],
        }
        for index, character_id in enumerate(character_ids)
    ]

def fetch_template_by_code(template_code):
    runtime, ids, prompt = runtime_template(template_code)
    return (runtime.get('costume_url') or [], runtime.get('screen_url') or [],
            runtime.get('actor_url') or [], prompt, ids, runtime.get('portrait_prompt') or '')


def fetch_template_model_by_code(template_code):
    """Return all outfit groups without changing the legacy adapter above."""
    return runtime_model(template_code)


def _image_task_state(base, task_id):
    """Read the durable image task so a 409 can be resolved instead of re-submitted."""
    try:
        response = http_client.session().get(
            base + '/api/v1/images/task/' + task_id,
            headers=local_headers('phase2-image'), timeout=(5, 30))
        if response.status_code != 200:
            return None
        return (response.json() or {}).get('data') or None
    except Exception:
        return None


def call_internal_api(user_id, reference_urls, prompt, aspect_ratio=None, max_retries=0, request_id=None, size='1.5K'):
    if not request_id:
        raise ValueError('图片生成必须携带固定请求编号')
    base = os.environ['IMAGE_SERVICE_URL'].rstrip('/')
    payload = {
        'user_id': user_id, 'reference_image_urls': reference_urls, 'prompt': prompt,
        'aspect_ratio': aspect_ratio or '9:16', 'request_id': request_id, 'size': size}
    attempts = 0
    while True:
        attempts += 1
        try:
            response = http_client.session().post(base + '/api/v1/images/generate',
                                                  headers=local_headers('phase2-image'),
                                                  json=payload, timeout=(5, 600))
        except (requests.ConnectionError, requests.Timeout) as error:
            if attempts <= 1:  # 每个链路额外重试 1 次
                time.sleep(2)
                continue
            raise UnknownSubmission('图片服务连接中断，提交结果待核实') from error
        if response.status_code == 409:
            try:
                body = response.json() or {}
            except ValueError:
                body = {}
            task_id = body.get('task_id')
            if task_id:
                deadline = time.time() + int(os.getenv('PHASE2_IMAGE_RESOLVE_SECONDS', '300'))
                while time.time() < deadline:
                    state = _image_task_state(base, task_id)
                    if state:
                        if state.get('status') == 'completed' and state.get('image_url'):
                            return state['image_url']
                        if state.get('status') == 'failed':
                            raise CloudFailed(state.get('error') or '云端已明确拒绝此图片请求')
                    time.sleep(5)
            raise UnknownSubmission('图片提交结果待核实')
        if response.status_code in (502, 503) and attempts <= 1:
            time.sleep(3)
            continue
        if response.status_code == 422:
            try:
                detail = (response.json() or {}).get('message')
            except ValueError:
                detail = None
            raise CloudFailed(detail or '图片服务已明确拒绝请求')
        if response.status_code >= 400:
            from common.error_detail import http_detail
            from common import service_log
            detail = http_detail(response)
            service_log.event('image', '图片服务返回错误', level='warning', context=detail)
            raise RuntimeError('图片服务返回错误：' + detail)
        body = response.json()
        if body.get('code') != 0:
            raise RuntimeError('图片服务未确认结果')
        return body['data']['image_url']
