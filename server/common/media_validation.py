"""Validate uploaded sample media before publication; bounded local subprocesses."""
import json
import subprocess
import tempfile


def inspect_video(path):
    try:
        with tempfile.TemporaryFile() as output:
            subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],
                           stdout=output,stderr=subprocess.STDOUT,check=True,timeout=12)
            output.seek(0)
            info=json.loads(output.read(1024*1024))
        streams=info.get('streams') or []
        video=next((s for s in streams if s.get('codec_type')=='video'), None)
        if video is None:
            raise ValueError('示例视频无法读取，请确认是有效的 MP4 文件')
        width,height=int(video.get('width') or 0),int(video.get('height') or 0)
        duration=float((info.get('format') or {}).get('duration') or 0)
        # 只校验「可播放」：能读出头且能完整解码一帧视频流即通过，不再限制编码、比例或时长。
        with tempfile.TemporaryFile() as output:
            subprocess.run(['ffmpeg','-v','error','-xerror','-nostdin','-i',str(path),
                            '-map','0:v:0','-frames:v','1','-f','null','-'],
                           stdout=output,stderr=subprocess.STDOUT,check=True,timeout=12)
        return dict(width=width,height=height,duration=duration,codec=video.get('codec_name') or '')
    except FileNotFoundError:
        raise ValueError('管理服务缺少 ffmpeg/ffprobe，请更新管理节点镜像') from None
    except ValueError:
        raise
    except (subprocess.SubprocessError,KeyError,StopIteration,TypeError,json.JSONDecodeError):
        raise ValueError('示例视频无法完整读取或检查超时，请重新导出 MP4') from None
