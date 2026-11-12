"""Alibaba Cloud FaceBody provider.

Official APIs used here are FaceBody 2019-12-30 ``DetectFace`` and
``PedestrianDetectAttribute``. Non-Shanghai-OSS images use the official
AdvanceRequest stream upload, not ImageURL directly:
https://help.aliyun.com/document_detail/155645.html
"""
from __future__ import annotations

import os
import re
from io import BytesIO

import requests


class FaceProviderError(RuntimeError):
    """Safe provider error without credential-bearing URLs or raw SDK responses."""


class FaceRejected(FaceProviderError):
    """Deterministic image/parameter error: ask the user to replace the photo."""


def _image_bytes(image_url):
    limit = 20 * 1024 * 1024
    try:
        with requests.get(
                image_url, stream=True, timeout=(5, 30),
                allow_redirects=True) as response:
            if response.status_code != 200:
                raise FaceProviderError('无法读取角色照片，请检查素材隧道是否仍在运行')
            content = bytearray()
            for chunk in response.iter_content(64 * 1024):
                content.extend(chunk)
                if len(content) > limit:
                    raise ValueError('角色照片超过 20MB，请压缩后重新上传')
            if not content:
                raise ValueError('角色照片内容为空，请重新上传')
            return bytes(content)
    except requests.RequestException as exc:
        raise FaceProviderError('读取角色照片失败，请检查素材隧道后重试') from exc


def _provider_error(exc):
    code = str(getattr(exc, 'code', '') or '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', code):
        code = 'RequestFailed'
    lowered = code.lower()
    if any(word in lowered for word in ('forbidden', 'unauthorized', 'accessdenied', 'invalidaccesskey', 'signature')):
        message = '阿里云人脸服务鉴权失败，请检查密钥及人脸检测、属性识别和文件上传权限'
        return FaceProviderError(message + '（' + code + '）')
    if any(word in lowered for word in ('image', 'invalidparameter', 'invalidargument', 'unsupported')):
        message = '阿里云无法处理这张照片，请使用清晰的 JPG 或 PNG 照片重试'
        return FaceRejected(message + '（' + code + '）')
    message = '阿里云人脸服务调用失败，请检查服务开通状态或稍后重试'
    return FaceProviderError(message + '（' + code + '）')


def detect_face(image_url: str) -> dict:
    from common.local_guard import require_cloud_enabled, validate_media_url
    require_cloud_enabled()
    validate_media_url(image_url)
    if not image_url or not str(image_url).startswith(("http://", "https://")):
        raise ValueError("阿里云人脸接口需要可公网访问的 ImageURL")
    import json as _json
    from common import service_log
    service_log.event("face", "人脸检测请求", context=_json.dumps(
        {"url_scheme": str(image_url).split(":")[0]}, ensure_ascii=False))
    try:
        from alibabacloud_facebody20191230.client import Client
        from alibabacloud_facebody20191230 import models as face_models
        from alibabacloud_tea_openapi import models as openapi_models
        from alibabacloud_tea_util import models as util_models
    except ImportError as exc:
        raise RuntimeError("未安装阿里云 FaceBody SDK，请安装 alibabacloud_facebody20191230") from exc

    access_key_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID") or os.getenv("ALIYUN_ACCESS_KEY_ID")
    access_key_secret = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET") or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
    if not access_key_id or not access_key_secret:
        raise RuntimeError("服务端未配置 ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET")
    config = openapi_models.Config(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        security_token=os.getenv("ALIBABA_CLOUD_SECURITY_TOKEN"),
        endpoint=os.getenv("ALIYUN_FACE_ENDPOINT", "facebody.cn-shanghai.aliyuncs.com"),
        region_id=os.getenv("ALIYUN_FACE_REGION", "cn-shanghai"),
    )
    client = Client(config)
    runtime = util_models.RuntimeOptions(
        read_timeout=int(os.getenv("ALIYUN_FACE_READ_TIMEOUT_MS", "15000")),
        connect_timeout=int(os.getenv("ALIYUN_FACE_CONNECT_TIMEOUT_MS", "5000")),
        autoretry=True,
        max_attempts=2,
    )
    image = _image_bytes(image_url)
    try:
        with BytesIO(image) as stream:
            detect_resp = client.detect_face_advance(
                face_models.DetectFaceAdvanceRequest(
                    image_urlobject=stream, max_face_number=10,
                    landmark=False, pose=False, quality=True,
                ), runtime,
            )
        detect_map = detect_resp.to_map()
        attr_map = {}
        if ((detect_map.get('body') or {}).get('Data') or {}).get('FaceRectangles'):
            # Each upload gets a fresh stream; a consumed stream would upload zero bytes.
            with BytesIO(image) as stream:
                attr_resp = client.pedestrian_detect_attribute_advance(
                    face_models.PedestrianDetectAttributeAdvanceRequest(image_urlobject=stream), runtime,
                )
            attr_map = attr_resp.to_map()
    except Exception as exc:
        raise _provider_error(exc) from exc
    detect_data = ((detect_map.get("body") or {}).get("Data") or {})
    attr_data = ((attr_map.get("body") or {}).get("Data") or {})
    rectangles = detect_data.get("FaceRectangles") or []
    boxes = attr_data.get("Boxes") or []
    attrs = attr_data.get("Attributes") or []
    # FaceBody may return one flat [x,y,w,h] array or a list of arrays/objects.
    if rectangles and isinstance(rectangles[0], (int, float)):
        face_count = len(rectangles) // 4
        rect = rectangles[:4] if face_count else None
    else:
        face_count = len(rectangles)
        rect = rectangles[0] if rectangles else None
    face_count = int(detect_data.get('FaceCount', face_count))
    box = boxes[0] if boxes else None
    attr = attrs[0] if attrs else {}
    # DetectFace returns [x, y, width, height] in the official model.
    if isinstance(rect, list) and len(rect) >= 4:
        location = {"x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]}
    elif isinstance(box, dict) and rectangles:
        location = {
            "x": box.get("TopLeftX"), "y": box.get("TopLeftY"),
            "width": (box.get("BottomRightX") or 0) - (box.get("TopLeftX") or 0),
            "height": (box.get("BottomRightY") or 0) - (box.get("TopLeftY") or 0),
        }
    else:
        location = {"x": None, "y": None, "width": None, "height": None}
    age_obj = attr.get("Age") or {}
    gender_obj = attr.get("Gender") or {}
    glasses_obj = attr.get("Glasses") or {}
    gender_name = str(gender_obj.get("Name") or "").lower()
    glasses_name = str(glasses_obj.get("Name") or "").lower()
    age_name = age_obj.get("Name")
    age_numbers = [int(item) for item in re.findall(r"\d+", str(age_name or ""))]
    age_value = round(sum(age_numbers) / len(age_numbers)) if age_numbers else None
    # Official SDK: Qualities.ScoreList, one aggregate score (0..100) per face.
    scores = (detect_data.get('Qualities') or {}).get('ScoreList') or []
    quality_score = scores[0] if scores else None
    quality_passed = (quality_score >= float(os.getenv('ALIYUN_FACE_MIN_QUALITY', '60'))
                      if isinstance(quality_score, (int, float)) else None)
    service_log.event("face", "人脸检测响应", context=_json.dumps(
        {"face_count": face_count, "quality_passed": quality_passed}, ensure_ascii=False))
    return {
        "face_count": face_count,
        "quality_passed": quality_passed,
        "quality_score": quality_score,
        "location": location,
        "age": age_value,
        "age_label": age_name,
        "gender": "Female" if gender_name in ("female", "woman", "女") else "Male" if gender_name in ("male", "man", "男") else "Unknown",
        "glasses": True if glasses_name in ("yes", "true", "glasses", "戴眼镜") else False if glasses_name in ("no", "false", "无眼镜") else None,
        "raw": {"detect": detect_map, "attributes": attr_map},
    }
