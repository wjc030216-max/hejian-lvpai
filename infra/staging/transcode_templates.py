#!/usr/bin/env python3
"""Re-encode template sample videos and covers in place, keeping full duration.

Videos keep their length and resolution but drop to a preview bitrate; covers
are resized to a phone-width WebP. Files already under budget are skipped.
The first run copies every original into the sibling `_originals` folder, so a
manual rollback is always possible.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

VIDEO_PATTERN = re.compile(r'(成片案例|example).*\.mp4$', re.I)
COVER_PATTERN = re.compile(r'(封面图|cover).*\.(webp|jpe?g|png)$', re.I)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def duration(path):
    result = run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                  '-of', 'default=nw=1:nk=1', path])
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def backup_of(path):
    """Copy the untouched original into <root>/_originals/<revision>/ once."""
    revision_folder = os.path.dirname(path)
    root = os.path.dirname(revision_folder)
    target = os.path.join(root, '_originals', os.path.basename(revision_folder), os.path.basename(path))
    if not os.path.exists(target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(path, target)
    return target


def transcode_video(path, bitrate, budget):
    size = os.path.getsize(path)
    if size <= budget:
        return 'skip', size, size
    before = duration(path)
    tmp = path + '.tmp.mp4'
    result = run(['ffmpeg', '-y', '-v', 'error', '-i', path,
                  '-c:v', 'libx264', '-preset', 'slow',
                  '-b:v', bitrate, '-maxrate', bitrate, '-bufsize', '1500k',
                  '-pix_fmt', 'yuv420p', '-an', '-movflags', '+faststart', tmp])
    if result.returncode != 0 or not os.path.exists(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        return 'failed: ' + (result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'ffmpeg'), size, size
    after = duration(tmp)
    if before and abs(after - before) > 0.5:
        os.remove(tmp)
        return 'failed: duration %.2f -> %.2f' % (before, after), size, size
    backup_of(path)
    new_size = os.path.getsize(tmp)
    os.replace(tmp, path)
    return 'ok', size, new_size


def transcode_cover(path, width, budget):
    size = os.path.getsize(path)
    if size <= budget:
        return 'skip', size, size
    tmp = path + '.tmp.webp'
    result = run(['ffmpeg', '-y', '-v', 'error', '-i', path,
                  '-vf', 'scale=%d:-2' % width, '-c:v', 'libwebp', '-quality', '72', tmp])
    if result.returncode != 0 or not os.path.exists(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        return 'failed: ' + (result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'ffmpeg'), size, size
    backup_of(path)
    new_size = os.path.getsize(tmp)
    os.replace(tmp, path)
    return 'ok', size, new_size


def mb(value):
    return '%.2fMB' % (value / 1048576.0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='/var/lib/hejian-lvpai/templates')
    parser.add_argument('--bitrate', default='700k')
    parser.add_argument('--video-budget', type=int, default=1600000)
    parser.add_argument('--cover-width', type=int, default=540)
    parser.add_argument('--cover-budget', type=int, default=150000)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not os.path.isdir(args.root):
        print('目录不存在:', args.root)
        return 2
    total_before = total_after = 0
    rows = []
    for revision in sorted(os.listdir(args.root)):
        folder = os.path.join(args.root, revision)
        if revision.startswith(('_', '.')) or not os.path.isdir(folder):
            continue
        videos = [f for f in os.listdir(folder) if VIDEO_PATTERN.search(f)]
        covers = [f for f in os.listdir(folder) if COVER_PATTERN.search(f)]
        for name in videos:
            path = os.path.join(folder, name)
            total_before += os.path.getsize(path)
            state, before, after = ('plan', os.path.getsize(path), 0) if args.dry_run \
                else transcode_video(path, args.bitrate, args.video_budget)
            total_after += after or before
            rows.append((revision[:8], 'video', state, before, after))
        for name in covers:
            path = os.path.join(folder, name)
            total_before += os.path.getsize(path)
            state, before, after = ('plan', os.path.getsize(path), 0) if args.dry_run \
                else transcode_cover(path, args.cover_width, args.cover_budget)
            total_after += after or before
            rows.append((revision[:8], 'cover', state, before, after))

    for revision, kind, state, before, after in rows:
        print('%-8s %-5s %-28s %8s -> %8s' % (revision, kind, state, mb(before), mb(after)))
    print('-' * 62)
    print('总计 %s -> %s' % (mb(total_before), mb(total_after)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
