"""Versioned runtime contract shared by image and video workers.

The template snapshot exposes a canonical list of 15-second segments. Older
single-clip snapshots are normalized into exactly the same shape in memory, so
generation code does not need two separate business paths.
"""
from __future__ import annotations


def _duration(value, default):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = int(default)
    if seconds <= 0:
        raise ValueError("视频片段时长无效")
    return seconds


def normalize_runtime(runtime: dict) -> dict:
    config = runtime.get("config_json") or {}
    settings = (config.get("module_1") or {}).get("character_settings") or []
    ids = [str(item.get("character_id") or "").strip() for item in settings]
    if not ids or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("模板角色或视频描述缺失")

    scene_urls = list(runtime.get("screen_url") or [])
    if not scene_urls or not scene_urls[0]:
        raise ValueError("模板缺少场景参考图")

    segments = runtime.get("segments") or []
    if not segments:
        prompt_groups = config.get("groups") or []
        if len(prompt_groups) != 1:
            raise ValueError("旧版模板必须包含一个完整提示词组")
        prompt = (prompt_groups[0] or {}).get("Prompt", "")
        segments = [{
            "code": "A",
            "total_seconds": 15,
            "prompt": prompt,
            "storyboard_url": runtime.get("storyboard_url") or "",
            "actor_url": list(runtime.get("actor_url") or []),
            "costume_url": list(runtime.get("costume_url") or []),
            "role_bindings": [],
        }]

    normalized = []
    codes = set()
    for index, raw in enumerate(segments):
        if not isinstance(raw, dict):
            raise ValueError("视频片段组格式无效")
        code = str(raw.get("code") or raw.get("group") or chr(65 + index)).strip().upper()
        if not code or code in codes:
            raise ValueError("视频片段组编号缺失或重复")
        codes.add(code)
        seconds = _duration(raw.get("total_seconds"), 15)
        if seconds != 15:
            raise ValueError("每个视频片段必须为 15 秒")
        prompt = raw.get("prompt") or raw.get("Prompt") or ""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"片段组 {code} 缺少视频提示词")
        actor_urls = list(raw.get("actor_url") or [])
        costume_urls = list(raw.get("costume_url") or [])
        if len(actor_urls) != len(ids) or len(costume_urls) != len(ids):
            raise ValueError(f"片段组 {code} 的角色素材数量不一致")
        storyboard_url = raw.get("storyboard_url") or ""
        if not storyboard_url:
            raise ValueError(f"片段组 {code} 缺少分镜图")
        normalized.append({
            "code": code,
            "total_seconds": seconds,
            "prompt": prompt,
            "storyboard_url": storyboard_url,
            "actor_url": actor_urls,
            "costume_url": costume_urls,
            "role_bindings": list(raw.get("role_bindings") or []),
            "makeup": dict(raw.get("makeup") or {}),
        })

    if len(normalized) > 2:
        raise ValueError("当前最多支持两个 15 秒片段组")
    portrait_group = str(runtime.get("portrait_group") or normalized[-1]["code"]).strip().upper()
    if portrait_group not in codes:
        raise ValueError("定妆照片段组不存在")
    return {
        "schema_version": int(runtime.get("schema_version") or 1),
        "ids": ids,
        "groups": normalized,
        "scene_urls": scene_urls,
        "bgm_url": runtime.get("bgm_url") or "",
        "portrait_prompt": runtime.get("portrait_prompt") or "",
        "portrait_group": portrait_group,
        "duration_seconds": sum(group["total_seconds"] for group in normalized),
    }
