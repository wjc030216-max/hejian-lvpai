"""Create lightweight 720p template previews while retaining recoverable originals."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


TARGET_WIDTH = 720
TARGET_VIDEO_BITRATE = "700k"
MAX_VIDEO_BITRATE = 900_000
MAX_FILE_BYTES = 6 * 1024 * 1024
COVER_WIDTH = 540
COVER_QUALITY = 72
COVER_MAX_BYTES = 180_000


class PreviewError(RuntimeError):
    pass


def probe_video(path):
    command = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size,bit_rate",
        "-show_entries", "stream=codec_type,codec_name,width,height,bit_rate",
        "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError as error:
        raise PreviewError("服务器未安装 ffprobe") from error
    except subprocess.CalledProcessError as error:
        raise PreviewError("ffprobe 无法读取视频：" + (error.stderr or "").strip()[:300]) from error
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    fmt = data.get("format") or {}
    size = int(fmt.get("size") or Path(path).stat().st_size)
    bit_rate = int(fmt.get("bit_rate") or 0)
    return {
        "duration": float(fmt.get("duration") or 0),
        "size": size,
        "bit_rate": bit_rate,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "codec": video.get("codec_name") or "",
    }


def needs_optimization(info):
    return (
        info["bit_rate"] > MAX_VIDEO_BITRATE
        or max(info["width"], info["height"]) > 1280
        or (info["bit_rate"] <= 0 and info["size"] > MAX_FILE_BYTES)
    )


def optimize_cover(path, width=COVER_WIDTH, quality=COVER_QUALITY):
    """Shrink one template cover in place; returns a small status dict."""
    path = Path(path)
    before = path.stat().st_size
    if before <= COVER_MAX_BYTES:
        return {"status": "skipped", "path": str(path), "before": before, "after": before}
    from PIL import Image
    with Image.open(path) as image:
        ratio = image.width / image.height
        target = (width, max(1, round(width / ratio)))
        converted = image.convert("RGB").resize(target, Image.LANCZOS)
    temporary = path.with_suffix(".tmp.webp")
    try:
        converted.save(temporary, format="WEBP", quality=quality, method=6)
        after = temporary.stat().st_size
        if after <= 0 or after >= before:
            return {"status": "skipped", "path": str(path), "before": before, "after": before}
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "optimized", "path": str(path), "before": before, "after": after}


def optimize_template_preview(path, force=False):
    """Replace one sample MP4 with a small H.264 preview.

    The caller is responsible for keeping a recoverable copy of the original.
    """
    path = Path(path)
    before = probe_video(path)
    if not force and not needs_optimization(before):
        return {"status": "skipped", "path": str(path), "before": before, "after": before}

    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix="." + path.stem + "-preview-",
        suffix=".mp4",
        dir=path.parent,
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    command = [
        "ffmpeg", "-nostdin", "-y", "-v", "error",
        "-i", str(path),
        "-map", "0:v:0", "-an",
        "-vf", "scale=720:-2:flags=lanczos",
        "-r", "24",
        "-c:v", "libx264",
        "-preset", "medium",
        "-profile:v", "main",
        "-pix_fmt", "yuv420p",
        "-b:v", TARGET_VIDEO_BITRATE,
        "-maxrate", "820k",
        "-bufsize", "1600k",
        "-movflags", "+faststart",
        str(temporary),
    ]
    try:
        try:
            subprocess.run(command, capture_output=True, text=True, check=True)
        except FileNotFoundError as error:
            raise PreviewError("服务器未安装 ffmpeg") from error
        except subprocess.CalledProcessError as error:
            raise PreviewError("ffmpeg 预览转码失败：" + (error.stderr or "").strip()[:500]) from error
        after = probe_video(temporary)
        if after["size"] <= 0 or after["duration"] < before["duration"] - 1:
            raise PreviewError("预览文件校验失败")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "optimized", "path": str(path), "before": before, "after": after}


def discover_samples(root):
    root = Path(root)
    return sorted(
        path for path in root.rglob("7-*.mp4")
        if "_originals" not in path.parts and ".source" not in path.stem.lower()
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.getenv("ADMIN_TEMPLATE_ROOT", "/var/lib/hejian-lvpai/templates"))
    parser.add_argument("--apply", action="store_true", help="Replace samples; without this flag only report")
    parser.add_argument("--force", action="store_true", help="Re-encode previews that are already small")
    parser.add_argument("--rollback", action="store_true", help="Restore originals from _originals")
    parser.add_argument("--report", help="Write a JSON report to this path")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit("Template root does not exist: " + str(root))
    backup_root = root / "_originals"
    results = []

    if args.rollback:
        for source in sorted(backup_root.rglob("7-*.mp4")):
            relative = source.relative_to(backup_root)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            results.append({"status": "restored", "path": str(target), "source": str(source)})
    else:
        for sample in discover_samples(root):
            try:
                if not args.apply:
                    info = probe_video(sample)
                    results.append({
                        "status": "would-optimize" if needs_optimization(info) else "skipped",
                        "path": str(sample),
                        "before": info,
                    })
                    continue
                info = probe_video(sample)
                if not args.force and not needs_optimization(info):
                    results.append({"status": "skipped", "path": str(sample), "before": info})
                    continue
                backup = backup_root / sample.parent.name / sample.name
                if not backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(sample, backup)
                result = optimize_template_preview(sample, force=args.force)
                result["backup"] = str(backup)
                results.append(result)
            except (OSError, ValueError, PreviewError) as error:
                results.append({"status": "failed", "path": str(sample), "error": str(error)})

    summary = {}
    for item in results:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    report = {"root": str(root), "summary": summary, "items": results}
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
