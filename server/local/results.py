"""Save the real cloud result and mix template BGM entirely on local disk."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import requests
from werkzeug.utils import secure_filename


def _internal_media_url(url):
    """本地联调时隧道不稳定；把公网媒体地址换成 compose 内网网关地址。"""
    external = (os.getenv('EXTERNAL_DOMAIN') or '').rstrip('/')
    internal = (os.getenv('LOCAL_INTERNAL_MEDIA_BASE') or '').rstrip('/')
    if external and internal and str(url).startswith(external):
        return internal + str(url)[len(external):]
    return url


def _download(url, target):
    try:
        _download_once(url, target)
    except requests.RequestException:
        fallback = _internal_media_url(url)
        if fallback == url:
            raise
        _download_once(fallback, target)


def _download_once(url, target):
    with requests.get(url, stream=True, timeout=(10, 120)) as response:
        response.raise_for_status()
        total = 0
        with target.open("wb") as output:
            for chunk in response.iter_content(1024 * 1024):
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    raise RuntimeError("Generated media exceeds 512 MB")
                output.write(chunk)


def _has_audio(path):
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return bool(probe.stdout.strip())


def _mix_bgm(source, music, output):
    cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(source),
           "-stream_loop", "-1", "-i", str(music)]
    if _has_audio(source):
        cmd += ["-filter_complex", "[1:a]volume=0.25[bg];[0:a][bg]amix=inputs=2:duration=first[a]",
                "-map", "0:v:0", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-shortest",
            "-movflags", "+faststart", str(output)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)


def _normalize_segment(source, target, seconds):
    width = max(2, int(os.getenv("PHASE2_VIDEO_WIDTH", "720")))
    height = max(2, int(os.getenv("PHASE2_VIDEO_HEIGHT", "1280")))
    fps = max(1, int(os.getenv("PHASE2_VIDEO_FPS", "25")))
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=white,"
        f"fps={fps},format=yuv420p"
    )
    cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(source)]
    if not _has_audio(source):
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        audio_input = "1:a:0"
    else:
        audio_input = "0:a:0"
    cmd += ["-map", "0:v:0", "-map", audio_input, "-t", str(seconds),
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", "-movflags", "+faststart",
            str(target)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)


def _concat_segments(segments, target):
    if len(segments) < 2:
        raise ValueError("至少需要两个视频片段才能拼接")
    cmd = ["ffmpeg", "-nostdin", "-y"]
    for segment in segments:
        cmd += ["-i", str(segment)]
    pairs = "".join(f"[{index}:v][{index}:a]" for index in range(len(segments)))
    graph = f"{pairs}concat=n={len(segments)}:v=1:a=1[v][a]"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", str(target)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)


def validate_final_video(path, expected_seconds):
    """Reject truncated or incorrectly encoded results before storage upload."""
    width = max(2, int(os.getenv("PHASE2_VIDEO_WIDTH", "720")))
    height = max(2, int(os.getenv("PHASE2_VIDEO_HEIGHT", "1280")))
    fps = max(1, int(os.getenv("PHASE2_VIDEO_FPS", "25")))
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    info = json.loads(probe.stdout)
    streams = info.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video or video.get("codec_name") != "h264":
        raise RuntimeError("成片不是可交付的 H.264 视频")
    if (int(video.get("width") or 0), int(video.get("height") or 0)) != (width, height):
        raise RuntimeError("成片分辨率不符合交付规格")
    rate = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1")
    try:
        numerator, denominator = (int(value) for value in rate.split("/", 1))
        actual_fps = numerator / max(1, denominator)
    except (TypeError, ValueError):
        actual_fps = 0
    if abs(actual_fps - fps) > 0.5:
        raise RuntimeError("成片帧率不符合交付规格")
    if not audio:
        raise RuntimeError("成片缺少音轨")
    duration = float((info.get("format") or {}).get("duration") or 0)
    if abs(duration - float(expected_seconds)) > 0.75:
        raise RuntimeError("成片时长不符合模板规格")
    # Decode the first frame to reject a well-formed but unusable container.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path),
         "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-"],
        capture_output=True, check=True, timeout=30,
    )
    return {"duration": duration, "width": width, "height": height, "fps": actual_fps}


def save_local_video(video_url, bgm_url, user_id, task_id, prepare_only=False):
    root = Path(os.environ["LOCAL_RESULT_ROOT"])
    user = secure_filename(user_id)
    name = secure_filename(task_id) + ".mp4"
    if not user or not task_id:
        raise ValueError("Missing local result identity")
    folder = root / user
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    # Atomic replace below means an existing output is a fully composed file.
    if prepare_only and target.is_file():
        return str(target)
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        temp = Path(temp)
        source = temp / "source.mp4"
        normalized = temp / "normalized.mp4"
        _download(video_url, source)
        seconds = max(1, int(os.getenv("PHASE2_SINGLE_SEGMENT_SECONDS", "15")))
        _normalize_segment(source, normalized, seconds)
        if bgm_url:
            from common.local_guard import validate_media_url
            validate_media_url(bgm_url)
            music = temp / "music"
            _download(bgm_url, music)
            output = temp / "final.mp4"
            _mix_bgm(normalized, music, output)
            output.replace(target)
        else:
            normalized.replace(target)
    if prepare_only:
        return str(target)
    if os.getenv("LOCAL_RESULT_STORAGE") == "tos":
        from common.storage_client import upload_result
        return upload_result(task_id, target)
    return os.environ["EXTERNAL_DOMAIN"].rstrip("/") + "/aitravel/api/v1/file/download/" + user + "/" + name


def save_local_video_segments(video_urls, bgm_url, user_id, task_id,
                              segment_seconds=15, prepare_only=False):
    """Generate two segment videos independently, then compose one final film."""
    if not isinstance(video_urls, list) or len(video_urls) != 2:
        raise ValueError("双段模板必须提供两个视频地址")
    root = Path(os.environ["LOCAL_RESULT_ROOT"])
    user = secure_filename(user_id)
    name = secure_filename(task_id) + ".mp4"
    if not user or not task_id:
        raise ValueError("Missing local result identity")
    folder = root / user
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if prepare_only and target.is_file():
        return str(target)
    with tempfile.TemporaryDirectory(dir=folder) as temp_dir:
        temp = Path(temp_dir)
        normalized = []
        for index, url in enumerate(video_urls, start=1):
            source = temp / f"source-{index}.mp4"
            _download(url, source)
            segment = temp / f"segment-{index}.mp4"
            _normalize_segment(source, segment, segment_seconds)
            normalized.append(segment)
        joined = temp / "joined.mp4"
        _concat_segments(normalized, joined)
        if bgm_url:
            from common.local_guard import validate_media_url
            validate_media_url(bgm_url)
            music = temp / "music"
            _download(bgm_url, music)
            output = temp / "final.mp4"
            _mix_bgm(joined, music, output)
            output.replace(target)
        else:
            joined.replace(target)
    if prepare_only:
        return str(target)
    if os.getenv("LOCAL_RESULT_STORAGE") == "tos":
        from common.storage_client import upload_result
        return upload_result(task_id, target)
    return os.environ["EXTERNAL_DOMAIN"].rstrip("/") + "/aitravel/api/v1/file/download/" + user + "/" + name
