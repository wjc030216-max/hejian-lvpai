"""Shrink a composed video for smooth playback without changing duration.

The generated clip is 720x1280 at roughly 4 Mbps. Mobile players on weak 4G
stutter at that bitrate, so the file is re-encoded to ~1.8 Mbps before upload.
Resolution, frame rate and H.264 encoding are preserved because validation
downstream checks them.
"""
import json
import os
import subprocess

TARGET_BITRATE = os.getenv('PHASE2_PLAYBACK_BITRATE', '1800k')
MAX_BITRATE = os.getenv('PHASE2_PLAYBACK_MAX_BITRATE', '2200k')
SKIP_ABOVE = int(os.getenv('PHASE2_PLAYBACK_SKIP_ABOVE', '2500000'))


def _probe(path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
        capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError('无法读取成片信息')
    return json.loads(result.stdout or '{}')


def optimize_playback(path):
    """Re-encode in place when the clip is heavier than the playback budget."""
    path = str(path)
    info = _probe(path)
    video = next((s for s in info.get('streams') or [] if s.get('codec_type') == 'video'), {})
    bitrate = int((info.get('format') or {}).get('bit_rate') or 0)
    duration = float((info.get('format') or {}).get('duration') or 0)
    if bitrate and bitrate <= SKIP_ABOVE:
        return {'status': 'skipped', 'bitrate': bitrate, 'duration': duration}

    temporary = path + '.playback.mp4'
    command = [
        'ffmpeg', '-y', '-v', 'error', '-i', path,
        '-map', '0:v:0', '-map', '0:a:0?',
        '-c:v', 'libx264', '-preset', 'veryfast', '-profile:v', 'main',
        '-b:v', TARGET_BITRATE, '-maxrate', MAX_BITRATE, '-bufsize', '3600k',
        '-pix_fmt', 'yuv420p', '-r', str(video.get('avg_frame_rate') or '25'),
        '-c:a', 'aac', '-b:a', '96k',
        '-movflags', '+faststart', temporary,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not os.path.exists(temporary):
            raise RuntimeError('播放版转码失败: ' + (result.stderr or '')[-200:])
        probe = _probe(temporary)
        new_duration = float((probe.get('format') or {}).get('duration') or 0)
        if duration and abs(new_duration - duration) > 0.5:
            raise RuntimeError('播放版时长校验失败')
        new_bitrate = int((probe.get('format') or {}).get('bit_rate') or 0)
        os.replace(temporary, path)
        return {'status': 'optimized', 'before': bitrate, 'after': new_bitrate, 'duration': new_duration}
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
