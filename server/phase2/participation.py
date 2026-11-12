"""Explicit visitor/template role selection. Draft photos survive mode changes."""


def user_roles(project):
    roles = project['roles']
    if project.get('participation_mode', 'multi') != 'single':
        return roles
    return [r for r in roles if r['person_uid'] == project.get('selected_person_uid')]


def template_roles(project):
    selected = {r['person_uid'] for r in user_roles(project)}
    return [r for r in project['roles'] if r['person_uid'] not in selected]


def validate_selection(project, runtime):
    if project.get('participation_mode', 'multi') not in ('single', 'multi'):
        raise ValueError('拍摄模式无效')
    if not user_roles(project):
        raise ValueError('请先选择你要扮演的角色')
    settings = runtime['config_json']['module_1']['character_settings']
    ids = [r['character_id'] for r in settings]
    if len(set(ids)) != len(ids) or ids != [r['character_id'] for r in project['roles']]:
        raise ValueError('模板角色已变化，请重新选择模板')
    defaults = dict(zip(ids, runtime.get('actor_url') or []))
    for role in template_roles(project):
        if not defaults.get(role['character_id']):
            raise ValueError('角色“' + role['display_name'] + '”缺少模板人物素材')
    return defaults


def generation_actors(project):
    defaults = validate_selection(project, project['runtime_snapshot'])
    visitors = {r['person_uid'] for r in user_roles(project)}
    return [{'person_uid': r['person_uid'], 'character_id': r['character_id'],
             'name': r['display_name'],
             'image': r['photo_url'] if r['person_uid'] in visitors else defaults[r['character_id']],
             'source': 'user_face' if r['person_uid'] in visitors else 'template'}
            for r in project['roles']]
