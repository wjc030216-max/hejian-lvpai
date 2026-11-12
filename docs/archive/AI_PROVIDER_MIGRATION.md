# 二期 AI Provider 调整

本次只替换人脸与图片 Provider，视频服务 `server/aitask/seeddance_video_app.py` 保持原接口、模型和任务流程。

## 官方接口依据

- 阿里云 FaceBody Python SDK：`alibabacloud_facebody20191230==6.0.0`。
  - `DetectFace`（FaceBody API 2019-12-30）返回人脸框。
  - `PedestrianDetectAttribute`（FaceBody API 2019-12-30）返回年龄、性别、眼镜等属性。
  - [阿里云 FaceBody API 文档](https://help.aliyun.com/zh/viapi/developer-reference/face-detection)
- Seedream 5.0 Pro 走火山方舟图片生成接口 `POST /api/v3/images/generations`。
  - Mainland model ID：`doubao-seedream-5-0-pro-260628`。
  - 官方允许尺寸：`1K`、`1.5K`、`2K`；本项目统一取最低 `1K`。
  - 官方最多 10 张参考图；本项目配置为 10 张。
  - [ModelArk Seedream 5.0 Pro 官方教程](https://www.volcengine.com/docs/82379/1824121)
  - 海外 BytePlus 对应模型 ID 是 `dola-seedream-5-0-pro-260628`，如使用海外端点只需修改环境变量，不改代码。

## 保留的项目接口

人脸：`POST /api/v1/personattr/detect` 的请求和响应仍保持一期结构。后端读取 `Url` 对应照片，通过官方 `DetectFaceAdvanceRequest` / `PedestrianDetectAttributeAdvanceRequest` 的 `image_urlobject` 上传文件流。普通隧道地址不能直接当作阿里云上海 OSS 的 `ImageURL` 使用。参见[官方文件 URL 处理说明](https://help.aliyun.com/document_detail/155645.html)。无需增加阿里云 OSS 桶配置，也不复用 TOS 凭证。

图片：`POST /api/v1/images/generate` 仍返回 `code=0` 和 `data.image_url`。旧的 `model`、`size`、`quality` 入参只用于兼容，服务端统一忽略旧模型和旧尺寸，固定使用 Seedream 5.0 Pro + `1K`。

比例通过内部 `aspect_ratio` 指定，由适配器转换为官方像素尺寸后写入 Ark 的 `size`：定妆照 `9:16 → 800x1424`，角色三视图 `16:9 → 1424x800`。响应 `data.size` 表示档位，`data.output_size` 表示实际请求尺寸。尺寸表来源：[Seedream 5.0 Pro 官方说明](https://docs.byteplus.com/en/docs/ModelArk/seedream-5-0-pro)。

## 测试环境配置

把下面变量写入测试服务器的独立 `.env`，不要写进小程序和 Git：

```dotenv
ALIBABA_CLOUD_ACCESS_KEY_ID=你的测试AK
ALIBABA_CLOUD_ACCESS_KEY_SECRET=你的测试SK
ALIYUN_FACE_ENDPOINT=facebody.cn-shanghai.aliyuncs.com
ALIYUN_FACE_REGION=cn-shanghai
ARK_API_KEY=你的火山方舟测试Key
SEEDREAM_API_URL=https://ark.cn-beijing.volces.com/api/v3/images/generations
SEEDREAM_MODEL=doubao-seedream-5-0-pro-260628
SEEDREAM_MIN_SIZE=1K
SEEDREAM_MAX_REFERENCE_IMAGES=10
```

部署前安装 `server/init/requirements.txt`，然后只重启测试环境的人脸服务、图片服务和定妆照服务。生产环境仍使用它自己的 `.env` 和进程，不能复用测试密钥。

图片服务的唯一启动入口是 `server/ai2pic/seedream_app.py`（10008）。旧的 qiniu、KIE、移动云和 t2i 入口已删除，避免被误启动；调用方继续使用统一的 `/api/v1/images/generate` 合同。

## 本地验证

```powershell
python -m py_compile server/common/*.py server/personattr/personattr_app.py `
  server/ai2pic/seedream_app.py server/charactergen/charactergen_app.py
```

没有云密钥时，服务会在真正调用时返回明确的配置错误，不会把密钥打印到日志。首次真机测试应使用测试服务器上传的公网图片 URL，并先做一张图的小额调用验证。
