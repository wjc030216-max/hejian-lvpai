"""Only immutable phase-two template snapshots may enter generation."""
import os
import re
from common.internal_auth import local_headers
from common import http_client
from common.template_contract import normalize_runtime


def runtime_template(code):
    if not re.fullmatch(r'P2S-[a-zA-Z0-9_-]+', code):
        raise ValueError('制作必须使用二期模板快照')
    response=http_client.session().get(os.environ['PHASE2_TEMPLATE_API_URL'].rstrip('/')+'/'+code+'/runtime',
                          headers=local_headers('local-phase2'),timeout=(5,15))
    response.raise_for_status()
    body=response.json()
    if body.get('code') != 200: raise RuntimeError('模板快照读取失败')
    runtime=body['data']; config=runtime.get('config_json') or {}
    ids=[str(x['character_id']) for x in config.get('module_1',{}).get('character_settings',[]) if x.get('character_id')]
    groups=config.get('groups') or []
    if not 1 <= len(groups) <= 2:
        raise ValueError('视频模板必须包含一至两个 15 秒提示词组')
    prompt=groups[0].get('Prompt','') if groups else ''
    if not ids or not prompt: raise ValueError('模板角色或视频描述缺失')
    return runtime,ids,prompt


def runtime_model(code):
    """Return the complete canonical segment model used by generation workers."""
    runtime, _ids, _prompt = runtime_template(code)
    model = normalize_runtime(runtime)
    model["runtime"] = runtime
    return model
