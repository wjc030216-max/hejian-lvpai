"""Reference contract: user selfies, this task's three views, template roles,
storyboard and scene.

The reference list is always derived from the template ``character_settings``
order and keyed by ``character_id``, never by array position. User roles get a
selfie and a generated three-view; template roles get one appearance image so
the same asset is never registered twice.
"""


def build_references(character_ids, role_mapping, scene_urls, storyboard_url):
    """Return an explicit, order-stable reference plan.

    Each item's ``index`` is the official ``图片N`` number (Volcengine prompt
    guide). ``asset_id`` is filled by the caller after the URL is registered in
    the Seedance asset library.
    """
    mapping = {item.get('character_id'): item for item in role_mapping}
    if not character_ids or len(set(character_ids)) != len(character_ids) or len(mapping) != len(role_mapping) or set(mapping) != set(character_ids):
        raise ValueError('视频角色与模板角色不一致')

    visitor_seq = {}
    seq = 0
    for character_id in character_ids:
        if mapping[character_id].get('source') != 'template':
            seq += 1
            visitor_seq[character_id] = seq

    plan = []
    index = 0

    # 1. 用户自拍照
    for character_id in character_ids:
        role = mapping[character_id]
        if role.get('source') == 'template':
            continue
        url = role.get('user_face_url')
        if not url:
            raise ValueError('角色 ' + character_id + ' 缺少人脸参考')
        index += 1
        plan.append({
            'index': index,
            'kind': 'face',
            'character_id': character_id,
            'source': 'user_face',
            'url': url,
            'note': f'用户{visitor_seq[character_id]}自拍照',
        })

    # 2. 用户三视图
    for character_id in character_ids:
        role = mapping[character_id]
        if role.get('source') == 'template':
            continue
        url = role.get('three_view_url')
        if not url:
            raise ValueError('角色 ' + character_id + ' 缺少三视图参考')
        index += 1
        plan.append({
            'index': index,
            'kind': 'three_view',
            'character_id': character_id,
            'source': 'generated',
            'url': url,
            'note': f'用户{visitor_seq[character_id]}人物设定三视图',
        })

    # 3. 模板角色：只传一张整体形象图，不重复传人脸
    for character_id in character_ids:
        role = mapping[character_id]
        if role.get('source') != 'template':
            continue
        url = role.get('template_face_url') or role.get('three_view_url')
        if not url:
            raise ValueError('角色 ' + character_id + ' 缺少模板人物素材')
        index += 1
        plan.append({
            'index': index,
            'kind': 'template_character',
            'character_id': character_id,
            'source': 'template',
            'url': url,
            'note': '模板人物设定图',
        })

    # 4. 模板分镜图
    if not storyboard_url:
        raise ValueError('模板缺少分镜图')
    index += 1
    plan.append({
        'index': index,
        'kind': 'storyboard',
        'character_id': None,
        'source': 'template',
        'url': storyboard_url,
        'note': '模板分镜图',
    })

    # 5. 模板场景图
    if not scene_urls or not scene_urls[0]:
        raise ValueError('模板缺少场景参考图')
    index += 1
    plan.append({
        'index': index,
        'kind': 'scene',
        'character_id': None,
        'source': 'template',
        'url': scene_urls[0],
        'note': '模板场景图',
    })

    if len(plan) > 9:
        raise ValueError('视频参考图超过接口上限 9 张')
    return plan


def build_prompt(plan, character_ids):
    """Render the official ``图片N`` wording, aligned with template prompts."""
    faces = {item['character_id']: item for item in plan if item['kind'] == 'face'}
    views = {item['character_id']: item for item in plan if item['kind'] == 'three_view'}
    templates = {item['character_id']: item for item in plan if item['kind'] == 'template_character'}
    storyboard = next(item for item in plan if item['kind'] == 'storyboard')
    scene = next(item for item in plan if item['kind'] == 'scene')
    parts = []
    for character_id in character_ids:
        if character_id in templates:
            item = templates[character_id]
            parts.append(f"[{character_id}]整体形象参考图片{item['index']}")
        else:
            face = faces[character_id]
            view = views[character_id]
            parts.append(
                f"[{character_id}]整体形象、身高体型参考图片{view['index']}、"
                f"人脸长相参考图片{face['index']}"
            )
    parts.append(f"镜头调度参考图片{storyboard['index']}")
    parts.append(f"场景参考图片{scene['index']}")
    return '；'.join(parts) + '。'
