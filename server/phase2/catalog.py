"""Small paginated catalogue ranked by the operator-managed heat value."""

FIELDS = {'scene': 'location_association', 'actor_count': 'actor_count',
          'gender': 'lead_gender_tags', 'relationship': 'relationship_tags'}
FILTER_ORDER = ('scene', 'actor_count', 'gender', 'relationship')


def select_catalog(items, args, heat=None):
    try:
        limit = int(args.get('limit', 8))
        offset = int(args.get('offset', 0))
    except (ValueError, TypeError):
        raise ValueError('分页参数必须是整数')
    if not 1 <= limit <= 30 or offset < 0:
        raise ValueError('每次读取1至30条模板，起点不能为负数')
    def matches(item, excluded='', prefix=False):
        for key, field in FIELDS.items():
            # Earlier choices remain editable even when later choices narrow results.
            if prefix and FILTER_ORDER.index(key) >= FILTER_ORDER.index(excluded):
                continue
            value = str(args.get(key) or '').strip()
            if key == excluded or not value:
                continue
            actual = item.get(field, [])
            if isinstance(actual, list):
                if value not in actual:
                    return False
            elif str(actual) != value:
                return False
        return True
    facets = {}
    for key, field in FIELDS.items():
        values = set()
        for item in items:
            if matches(item, key, prefix=True):
                actual = item.get(field)
                values.update(str(v) for v in (actual if isinstance(actual, list) else [actual]) if v is not None and v != '')
        facets[key] = sorted(values, key=lambda v: int(v) if key == 'actor_count' else v)
    filtered = [item for item in items if matches(item)]
    def sort_key(item):
        hot = heat.get(item['template_code'], 0) if isinstance(heat, dict) else 0
        return (-hot, item['template_code'])
    filtered.sort(key=sort_key)
    scenes = sorted({
        str(item.get('location_association')).strip()
        for item in items
        if item.get('location_association')
    })
    return {'items': filtered[offset:offset + limit], 'total': len(filtered),
            'next_offset': offset + limit if offset + limit < len(filtered) else None,
            'facets': facets, 'scenes': scenes}
