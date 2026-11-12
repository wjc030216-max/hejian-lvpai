"""Generate self-contained template fixtures for clean-checkout tests."""
from __future__ import annotations

import json
import subprocess
import wave
import zipfile
from pathlib import Path

from PIL import Image


def managed_templates():
    templates = []
    for count in (1, 2, 3):
        ids = [f"角色{i}" for i in range(1, count + 1)]
        roles = [
            {"person_uid": str(index), "character_id": character_id,
             "display_name": f"角色{index}"}
            for index, character_id in enumerate(ids, start=1)
        ]
        revision = f"{count:032x}"
        runtime = {
            "template_code": f"TEST-{count}",
            "config_json": {
                "module_1": {"character_settings": roles},
                "groups": [{"Prompt": f"{count}人漓江旅拍动作提示词"}],
            },
            "costume_url": [f"local-media:assets/managed/{revision}/costume-{i}.png" for i in ids],
            "actor_url": [f"local-media:assets/managed/{revision}/actor-{i}.png" for i in ids],
            "screen_url": [f"local-media:assets/managed/{revision}/scene.png"],
            "storyboard_url": f"local-media:assets/managed/{revision}/storyboard.png",
            "bgm_url": f"local-media:assets/managed/{revision}/music.wav",
            "portrait_prompt": "生成高质量定妆照",
        }
        templates.append({
            "template_code": f"TEST-{count}",
            "video_name": f"测试旅拍{count}人",
            "story_hook": "自动生成的测试模板",
            "actor_count": count,
            "character_settings": roles,
            "relationship_tags": ["朋友"] if count > 1 else [],
            "lead_gender_tags": ["不限"],
            "age_tags": ["青年"],
            "video_genre": "休闲旅拍",
            "location_association": "漓江",
            "video_specification": "15秒",
            "asset_revision": revision,
            "managed_runtime": runtime,
        })
    return templates


def write_managed_catalog(destination: Path):
    destination.write_text(json.dumps(managed_templates(), ensure_ascii=False), encoding="utf-8")
    return destination


def create_template_folder(parent: Path, name="测试模板", actor_count=2):
    folder = Path(parent) / name
    folder.mkdir(parents=True, exist_ok=True)
    ids = ["男主角A", "女主角A", "女主角B"][:actor_count]
    roles = [
        {"person_uid": str(index), "character_id": character_id,
         "display_name": f"角色{index}"}
        for index, character_id in enumerate(ids, start=1)
    ]
    metadata = {
        "module_1": {
            "video_name": name,
            "story_hook": "自动生成的测试模板",
            "actor_count": actor_count,
            "character_settings": roles,
        },
        "module_2": {
            "actor_count": str(actor_count),
            "video_genre": "休闲旅拍",
            "relationship_tags": ["朋友"] if actor_count > 1 else [],
            "video_specification": "15秒",
            "recommendation_persona": {
                "location_association": "漓江",
                "recommended_gender": "不限",
                "recommended_age": "青年",
            },
        },
    }
    (folder / "1-基础元数据.txt").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    (folder / "5-分镜提示词.txt").write_text(
        json.dumps({"groups": [{"Prompt": "测试动作提示词"}]}, ensure_ascii=False),
        encoding="utf-8")
    (folder / "11-定妆照提示词.txt").write_text(
        json.dumps({"prompt": "[角色1]生成测试定妆照"}, ensure_ascii=False),
        encoding="utf-8")
    Image.new("RGB", (90, 160), (40, 80, 60)).save(folder / "6-封面图.jpg")
    Image.new("RGB", (64, 64), (80, 40, 60)).save(folder / "2.1-场景设定图.png")
    Image.new("RGB", (64, 64), (60, 80, 40)).save(folder / "10-模板分镜图.png")
    for index, character_id in enumerate(ids, start=1):
        Image.new("RGB", (64, 64), (30 + index, 70, 90)).save(
            folder / f"3.{index}-角色设定图-{character_id}.png")
        Image.new("RGB", (64, 64), (90, 30 + index, 70)).save(
            folder / f"9.{index}-角色服装图-{character_id}.png")
    _write_wav(folder / "8-模板配乐.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "color=c=black:s=90x160:d=15", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(folder / "7-成片案例.mp4")],
        check=True, timeout=30)
    return folder


def create_multi_segment_template_folder(parent: Path, name="双段测试模板"):
    folder = create_template_folder(parent, name, actor_count=2)
    metadata_path = folder / "1-基础元数据.txt"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["module_2"]["video_specification"] = "30秒"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "color=c=black:s=90x160:d=30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(folder / "7-成片案例.mp4")],
        check=True, timeout=30)

    settings = metadata["module_1"]["character_settings"]
    for index, setting in enumerate(settings, start=1):
        role = setting["character_id"]
        actor = folder / f"3.{index}-角色设定图-{role}.png"
        costume = folder / f"9.{index}-角色服装图-{role}.png"
        actor.rename(folder / f"3.{index}.A-角色设定图-{role}.png")
        costume.rename(folder / f"9.{index}.A-角色服装图-{role}.png")
        Image.new("RGB", (64, 64), (50 + index, 80, 120)).save(
            folder / f"3.{index}.B-角色设定图-{role}.png")
        Image.new("RGB", (64, 64), (120, 50 + index, 90)).save(
            folder / f"9.{index}.B-角色服装图-{role}.png")

    storyboard = folder / "10-模板分镜图.png"
    storyboard.rename(folder / "10.1-模板分镜图-A.png")
    Image.new("RGB", (90, 160), (40, 100, 70)).save(folder / "10.2-模板分镜图-B.png")
    groups = []
    for group in ("A", "B"):
        groups.append({
            "group": group,
            "total_seconds": 15,
            "reference_images": {
                "role_bindings": [
                    {
                        "role_order": index,
                        "character_id": setting["character_id"],
                        "role_image": f"3.{index}.{group}",
                        "outfit_image": f"9.{index}.{group}",
                    }
                    for index, setting in enumerate(settings, start=1)
                ],
            },
            "Prompt": f"{group} 组动作提示词",
        })
    (folder / "5-分镜提示词.txt").write_text(
        json.dumps({"groups": groups}, ensure_ascii=False), encoding="utf-8")
    (folder / "11-定妆照提示词.txt").write_text(json.dumps({
        "reference_images": {"source_group": "B"},
        "prompt": "生成 B 组定妆照",
    }, ensure_ascii=False), encoding="utf-8")
    return folder


def archive_template(folder: Path, filename="template.zip"):
    output = folder.parent / filename
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.iterdir()):
            if path.is_file():
                archive.write(path, folder.name + "/" + path.name)
    return output


def _write_wav(path):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\0\0" * 800)
