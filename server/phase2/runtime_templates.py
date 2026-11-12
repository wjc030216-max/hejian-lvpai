"""Build immutable phase-two template snapshots from supplied metadata/assets."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = Path(os.getenv("PHASE2_STATIC_ROOT", ROOT.parent.parent / "client/assets"))
_source_root = os.getenv("PHASE2_TEMPLATE_SOURCE_ROOT", "").strip()
SOURCE_ROOT = Path(_source_root) if _source_root else None
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:10020").rstrip("/")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} 无法读取或不是有效的 JSON：{exc}") from exc


def _find_folder(template: dict) -> Path | None:
    if SOURCE_ROOT is None:
        return None
    candidates = [SOURCE_ROOT]
    wanted = str(template.get("video_name") or "").strip()
    code = str(template.get("template_code") or "").strip()
    for root in candidates:
        if not root.exists():
            continue
        for folder in root.iterdir():
            if not folder.is_dir():
                continue
            metadata = folder / "1-基础元数据.txt"
            if not metadata.exists():
                metadata = folder / "metadata.txt"
            if not metadata.exists():
                continue
            try:
                data = _read_json(metadata)
            except (OSError, ValueError):
                continue
            module = data.get("module_1", {})
            if wanted and module.get("video_name") == wanted:
                return folder
            if code and folder.name == code:
                return folder
    return None


def _asset_url(folder: Path, filename: str) -> str:
    relative = folder.name + "/" + filename
    return f"{PUBLIC_BASE_URL}/assets/phase2-templates/{quote(relative, safe='/')}"


def normalize_template(template: dict) -> dict:
    """Read browse metadata from the same source as generation roles."""
    result = dict(template)
    folder = None if template.get('managed_runtime') else _find_folder(template)
    if folder:
        path = folder / '1-基础元数据.txt'
        data = _read_json(path if path.exists() else folder / 'metadata.txt')
        one, two = data.get('module_1', {}), data.get('module_2', {})
        persona = two.get('recommendation_persona') or {}
        result.update({k: one[k] for k in ('video_name', 'story_hook', 'character_settings') if k in one})
        result['actor_count'] = int(one['actor_count'])
        result.update({k: two[k] for k in ('relationship_tags', 'video_genre', 'video_specification') if k in two})
        result['location_association'] = persona.get('location_association', result.get('location_association', ''))
        result['lead_gender_tags'] = [persona['recommended_gender']] if persona.get('recommended_gender') else []
        result['age_tags'] = [persona['recommended_age']] if persona.get('recommended_age') else []
    roles = [dict(r, person_uid=str(r.get('person_uid') or i + 1))
             for i, r in enumerate(result['character_settings'])]
    if int(result['actor_count']) not in (1, 2, 3):
        raise ValueError('当前只支持 1 至 3 人模板')
    if len(roles) != int(result['actor_count']) or len({r['character_id'] for r in roles}) != len(roles) or len({r['person_uid'] for r in roles}) != len(roles):
        raise ValueError('模板人数与角色列表不一致或角色编号重复')
    result['character_settings'] = roles
    if result['actor_count'] == 1:
        result['relationship_tags'] = [v for v in result.get('relationship_tags', []) if v not in ('男', '女', '男性', '女性')]
    return result


def _first(folder: Path, prefix: str, extensions=('.png','.jpg','.jpeg','.webp')) -> str:
    matches = sorted(item for item in folder.iterdir() if item.is_file() and item.name.startswith(prefix) and item.suffix.lower() in extensions)
    if len(matches)>1: raise ValueError(prefix+' 对应文件必须唯一')
    return matches[0].name if matches else ""


def parse_duration_seconds(value, default: int | None = None) -> int:
    """Accept the metadata spellings used by template producers."""
    if value is None or value == "":
        if default is None:
            raise ValueError("缺少视频时长")
        return int(default)
    if isinstance(value, bool):
        raise ValueError("视频时长格式无效")
    if isinstance(value, (int, float)):
        seconds = int(value)
    else:
        match = re.fullmatch(r"\s*(\d+)\s*(?:秒|s|sec|seconds?)?\s*", str(value), re.I)
        if not match:
            raise ValueError("视频时长格式无效")
        seconds = int(match.group(1))
    if seconds <= 0 or seconds > 60:
        raise ValueError("视频时长需为 1 至 60 秒")
    return seconds


def _makeup_section(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return '；'.join(str(item).strip() for item in value.values() if str(item).strip())
    return ''


def _read_makeup(folder: Path, role_index: int, group_code: str, character_id: str):
    """Load 12.<role>.<group>-妆造提示词.txt for one role, if present."""
    candidates = sorted(
        item for item in folder.iterdir()
        if item.is_file() and item.suffix.lower() == '.txt'
        and item.name.startswith(f'12.{role_index}.') and '妆造' in item.name
    )
    if not candidates:
        return None
    preferred = [item for item in candidates if item.name.startswith(f'12.{role_index}.{group_code}')]
    path = (preferred or candidates)[0]
    raw = path.read_bytes()
    data = None
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            data = json.loads(raw.decode(encoding))
            break
        except (UnicodeError, json.JSONDecodeError):
            continue
    if not isinstance(data, dict):
        raise ValueError(f'{path.name} 不是有效的 JSON')
    owner = str(data.get('character_id') or '').strip()
    if owner and owner != character_id:
        raise ValueError(f'{path.name} 的 character_id({owner}) 与角色({character_id})不一致')
    parts = []
    makeup = _makeup_section(data.get('makeup') or data.get('面部妆容'))
    if makeup:
        parts.append('面部妆容：' + makeup)
    skin = _makeup_section(data.get('skin_photography') or data.get('面部皮肤摄影效果'))
    if skin:
        parts.append('面部皮肤摄影效果：' + skin)
    if not parts:
        raise ValueError(f'{path.name} 缺少 makeup / skin_photography 内容')
    return {
        'character_id': character_id,
        'text': chr(10).join(parts),
        'summary': str(data.get('video_prompt_summary') or '').strip(),
        'source': path.name,
    }


def _resolve_asset(folder: Path, reference: str, label: str,
                   extensions=('.png','.jpg','.jpeg','.webp')) -> str:
    """Resolve a metadata asset id such as ``9.1.A`` without relying on order."""
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError(f"{label}缺少素材编号")
    raw = reference.strip()
    base = Path(raw).stem if Path(raw).suffix.lower() in extensions else raw
    matches = sorted(
        item for item in folder.iterdir()
        if item.is_file() and item.suffix.lower() in extensions
        and (item.stem == base or item.stem.startswith(base + "-"))
    )
    if len(matches) != 1:
        raise ValueError(f"{label}素材 {base} 必须唯一且存在")
    return matches[0].name


def _find_role_asset(folder: Path, prefix: str, role_index: int,
                     character_id: str, label: str) -> str:
    numbered = sorted(
        item for item in folder.iterdir()
        if item.is_file() and item.suffix.lower() in ('.png','.jpg','.jpeg','.webp')
        and item.name.startswith(f"{prefix}.{role_index}")
    )
    if len(numbered) == 1:
        return numbered[0].name
    by_role = sorted(
        item for item in folder.iterdir()
        if item.is_file() and item.suffix.lower() in ('.png','.jpg','.jpeg','.webp')
        and item.name.startswith(prefix + ".") and item.stem.rstrip().endswith("-" + character_id)
    )
    if len(by_role) != 1:
        raise ValueError(f"角色 {character_id} 必须有唯一对应的{label}")
    return by_role[0].name


def _normalize_markers(text: str) -> str:
    """Strip whitespace inside [角色标记] so prompts always match character_id."""
    if not text:
        return text
    return re.sub(r"\[([^\[\]]+)\]", lambda m: "[" + re.sub(r"\s+", "", m.group(1)) + "]", text)


def build_runtime_template(template: dict) -> dict:
    if template.get('managed_runtime'):
        from common.media_refs import transform
        return transform(template['managed_runtime'])
    folder = _find_folder(template)
    if folder is None:
        raise FileNotFoundError(f"本地模板素材目录不存在: {template.get('template_code')}")

    return runtime_from_folder(template,folder)


def runtime_from_folder(template,folder,asset_url=_asset_url):
    metadata_path = folder / "1-基础元数据.txt"
    if not metadata_path.exists():
        metadata_path = folder / "metadata.txt"
    metadata = _read_json(metadata_path)
    module = metadata.get("module_1", {})
    settings = module.get("character_settings") or template.get("character_settings") or []

    prompt_path = folder / "5-分镜提示词.txt"
    portrait_path = folder / "11-定妆照提示词.txt"
    prompt_data = _read_json(prompt_path) if prompt_path.exists() else {"groups": []}
    portrait_data = _read_json(portrait_path) if portrait_path.exists() else {"prompt": ""}

    scene = _first(folder, "2.1-") or _first(folder, "2-")
    bgm = _first(folder, "8-", ('.mp3','.wav'))
    prompt_groups = prompt_data.get("groups") or []
    prompt_groups = [dict(g, Prompt=_normalize_markers(g.get("Prompt", ""))) for g in prompt_groups]
    portrait_prompt = _normalize_markers(portrait_data.get("prompt", ""))
    if not settings or len(settings) != int(module.get("actor_count") or template.get("actor_count") or 0):
        raise ValueError("角色数量与 actor_count 不匹配")
    if not scene:
        raise ValueError("缺少 2- 或 2.1- 场景设定图")
    if not isinstance(prompt_groups,list) or not prompt_groups or not all(
            isinstance(group,dict) and isinstance(group.get('Prompt'),str) and group['Prompt'].strip()
            for group in prompt_groups):
        raise ValueError("5-分镜提示词.txt 缺少视频动作提示词")
    if len(prompt_groups) > 2:
        raise ValueError("当前最多支持两个 15 秒片段组")
    if not portrait_prompt:
        raise ValueError("11-定妆照提示词.txt 缺少 prompt")

    declared_duration = parse_duration_seconds(
        template.get("video_specification")
        or (metadata.get("module_2") or {}).get("video_specification"),
        default=15 * len(prompt_groups),
    )
    expected_groups = declared_duration // 15 if declared_duration % 15 == 0 else 0
    if expected_groups != len(prompt_groups):
        raise ValueError("视频时长必须等于各片段 15 秒之和")

    ids = [str(setting.get("character_id") or "").strip() for setting in settings]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("角色编号缺失或重复")

    groups = []
    used_codes = set()
    for group_index, source_group in enumerate(prompt_groups):
        code = str(source_group.get("group") or chr(65 + group_index)).strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,15}", code) or code in used_codes:
            raise ValueError("片段组编号无效或重复")
        used_codes.add(code)
        segment_seconds = parse_duration_seconds(source_group.get("total_seconds"), default=15)
        if segment_seconds != 15:
            raise ValueError("每个视频片段必须为 15 秒")

        explicit_storyboard = source_group.get("storyboard_image")
        if explicit_storyboard:
            storyboard = _resolve_asset(folder, explicit_storyboard, "分镜图")
        else:
            storyboard_candidates = sorted(
                item for item in folder.iterdir()
                if item.is_file() and item.suffix.lower() in ('.png','.jpg','.jpeg','.webp')
                and item.name.startswith(f"10.{group_index + 1}-")
            )
            if not storyboard_candidates and len(prompt_groups) == 1:
                legacy = _first(folder, "10-")
                storyboard_candidates = [folder / legacy] if legacy else []
            if len(storyboard_candidates) != 1:
                raise ValueError(f"片段组 {code} 必须有唯一的分镜图")
            storyboard = storyboard_candidates[0].name

        reference = source_group.get("reference_images") or {}
        bindings = reference.get("role_bindings") or []
        binding_by_role = {}
        for binding in bindings:
            if not isinstance(binding, dict):
                raise ValueError("role_bindings 必须是对象列表")
            role_id = str(binding.get("character_id") or "").strip()
            if role_id:
                if role_id in binding_by_role:
                    raise ValueError(f"片段组 {code} 角色绑定重复")
                binding_by_role[role_id] = binding
        if bindings and set(binding_by_role) != set(ids):
            raise ValueError(f"片段组 {code} 的角色绑定与模板角色不一致")

        actor_urls = []
        costume_urls = []
        role_bindings = []
        makeup_map = {}
        for role_index, setting in enumerate(settings, start=1):
            character_id = str(setting.get("character_id") or "").strip()
            binding = binding_by_role.get(character_id)
            if binding:
                actor = _resolve_asset(folder, binding.get("role_image"), f"角色 {character_id} 角色图")
                costume = _resolve_asset(folder, binding.get("outfit_image"), f"角色 {character_id} 服装图")
            else:
                actor = _find_role_asset(folder, "3", role_index, character_id, "角色图")
                costume = _find_role_asset(folder, "9", role_index, character_id, "服装图")
            actor_url = asset_url(folder, actor)
            costume_url = asset_url(folder, costume)
            actor_urls.append(actor_url)
            costume_urls.append(costume_url)
            role_bindings.append({
                "role_order": role_index,
                "character_id": character_id,
                "role_image": actor_url,
                "outfit_image": costume_url,
            })
            makeup = _read_makeup(folder, role_index, code, character_id)
            if makeup:
                makeup_map[character_id] = makeup

        groups.append({
            "code": code,
            "total_seconds": segment_seconds,
            "prompt": source_group["Prompt"],
            "storyboard_url": asset_url(folder, storyboard),
            "actor_url": actor_urls,
            "costume_url": costume_urls,
            "role_bindings": role_bindings,
            "makeup": makeup_map,
        })

    portrait_source = (
        (portrait_data.get("reference_images") or {}).get("source_group")
        or groups[-1]["code"]
    )
    portrait_group = str(portrait_source).strip().upper()
    if portrait_group not in used_codes:
        raise ValueError("定妆照引用的片段组不存在")

    config_groups = []
    for item in groups:
        group = dict(item)
        group["Prompt"] = group.pop("prompt")
        group["group"] = group["code"]
        config_groups.append(group)
    return {
        "template_code": template.get("template_code"),
        "schema_version": 2,
        "duration_seconds": declared_duration,
        "segment_count": len(groups),
        "segments": groups,
        "portrait_group": portrait_group,
        "config_json": {
            "module_1": {"character_settings": settings},
            "groups": config_groups,
        },
        # Legacy fields remain populated with the primary portrait group so
        # older workers and clients can keep reading the same snapshot.
        "costume_url": next(group["costume_url"] for group in groups if group["code"] == portrait_group),
        "actor_url": next(group["actor_url"] for group in groups if group["code"] == portrait_group),
        "screen_url": [asset_url(folder, scene)] if scene else [],
        "storyboard_url": asset_url(folder, storyboard) if storyboard else "",
        "bgm_url": asset_url(folder, bgm) if bgm else "",
        "portrait_prompt": portrait_prompt,
    }
