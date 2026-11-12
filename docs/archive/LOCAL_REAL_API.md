# Windows 本地真实 API 开发

本目录现在是**本地专用构建**，不应上传为微信体验版或正式版。微信开发者工具中修改、运行本地代码不会替换已发布版本；本次没有部署服务器、修改微信后台或上传发布小程序。

## 当前状态与边界

本机运行 MySQL、Redis、上传服务、人脸服务、图片服务、角色与定妆照服务、视频服务、独立 TOS 存储服务、二期 API 和网关。真实模型推理仍由阿里云、火山引擎执行；“本地开发”不等于模型离线运行。上传照片会提交给这些云服务。

- 小程序业务请求固定 `http://127.0.0.1:8080/api/v2/`；试图运行体验版、正式版会报错，不再切换到测试或生产域名。
- 独立微信本地状态键 `phase2.localReal.v1`，不读取一期 token/openid；网络失败不再伪装成上传、创建会话成功。
- 独立 Docker 项目 `aitravel-local-real`，独立 MySQL、Redis、上传照片及生成文件数据卷。数据库/缓存固定连接该项目内部容器；宿主机端口仅绑定 `127.0.0.1`。
- `.env.local.real` 只接受列出的云凭证和本地素材配置。不会加载旧 `.env`，不会继承旧 shell 中的服务地址，密钥不会打包进镜像或小程序。
- 内部使用本地随机密钥签发有效期 5 分钟的 JWT。不同项目、不同重试使用不同的下游任务身份，避免复用别的项目图片。
- 成片先下载到本机并合成配乐，再由独立 TOS 服务上传到存储账号的私有开发桶，不执行旧上传脚本。Seedance 视频账号与 TOS 存储账号可以完全不同；视频服务拿不到 TOS 密钥。原图和中间素材仍在本机，通过素材隧道提供给云模型。
- **正式微信支付不接入**；订单仅本地模拟确认，AI 调用是可计费真实调用。抽奖仍属原型交互。
- 使用生产账号下相同配额的云密钥，仍可能争用生产配额或余额。要隔离这部分，请使用本地专用云账号/项目及独立配额、独立 Seedance 资产组。当前按用户要求复用一期素材组，因此素材库这一项不属于完全隔离；只新建一个 AccessKey 并不等于配额独立。

## 第一次启动

在 PowerShell 里进入真实项目目录：

```powershell
cd G:\桂智旅\aitravel\aitravel
.\start-local-real.ps1 -Init
.\start-local-real.ps1 -Offline
```

`-Init` 生成本地随机鉴权密钥和素材访问令牌，不覆盖已有云凭证。`-Offline` 启动服务，但禁止计费云调用，不会生成假成片。它可以验证浏览模板、建立会话和上传照片；点击确认制作会明确提示未启用云调用。

两种本地模式共用 8080、3307、6380；脚本只停止此前的 `aitravel-local` 容器，保留其数据。其他 Docker 项目不受影响。

## 让云端读取本机素材

另开 PowerShell，运行并保持该窗口开启：

```powershell
cd G:\桂智旅\aitravel\aitravel
.\start-local-tunnel.ps1
```

脚本使用 Docker 中的官方 `cloudflare/cloudflared` 镜像，无需安装 Windows 程序。从输出中复制 `https://xxxx.trycloudflare.com` 到 `.env.local.real` 的 `LOCAL_PUBLIC_BASE_URL`。也可使用你自己的 HTTPS 隧道，但目标必须是 **127.0.0.1:8081**，不能是 8080。

8081 只放行带随机路径令牌的 GET/HEAD 素材请求，不允许生成、下单、上传、删除、数据库或缓存访问。持有完整素材链接的人可读取素材，勿公开分享这些链接。开发结束关闭隧道即可。

临时隧道重启会换域名；更新配置、重启本地服务，并新建制作会话、重新上传照片，旧会话中的素材地址不会自动迁移。

## 配置真实调用

编辑 `.env.local.real`，填写：

| 字段 | 用途 |
| --- | --- |
| `LOCAL_PUBLIC_BASE_URL` | 上一步的本机 HTTPS 素材隧道地址，不带路径 |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | 本地开发专用阿里云 FaceBody 凭证 |
| `ALIBABA_CLOUD_SECURITY_TOKEN` | 仅使用 STS 临时凭证时填写，普通 AccessKey 留空 |
| `SEEDREAM_API_KEY` | 图片生成专用方舟 API Key |
| `SEEDANCE_API_KEY` | 视频账号 A 的方舟 API Key，需开通 Seedance 2.0 Mini |
| `SEEDANCE_ASSET_ACCESS_KEY_ID` / `SEEDANCE_ASSET_ACCESS_KEY_SECRET` | 视频账号 A 下素材库 IAM 用户的 AK/SK，不是方舟 API Key |
| `SEEDANCE_ASSET_GROUP_ID` / `SEEDANCE_ASSET_PROJECT_NAME` | 视频账号有权访问的素材组及项目；当前按用户要求复用一期组 |
| `TOS_ACCESS_KEY_ID` / `TOS_ACCESS_KEY_SECRET` | 独立存储账号 B 下 TOS IAM 用户的 AK/SK |
| `TOS_SECURITY_TOKEN` | 仅存储账号使用 STS 临时凭证时填写，普通 AK/SK 留空 |
| `TOS_BUCKET` | 存储账号 B 下实际存在的桶名，原样填写，无需添加前缀 |
| `TOS_REGION` / `TOS_ENDPOINT` | 存储桶实际区域及公网 HTTPS Endpoint，独立于视频 API 区域 |
| `TOS_KEY_PREFIX` | 固定在 `local-real/` 下，与生产对象分开 |
| `LOCAL_CLOUD_RESOURCES` | 旧版兼容字段，已停用，无需填写，不影响启动 |

详细的账号与密钥对应关系见 [CLOUD_CREDENTIALS.md](CLOUD_CREDENTIALS.md)。本地不再接受通用 `ARK_API_KEY`、`VOLC_ACCESS_KEY` 等旧配置，也不会用视频密钥补齐 TOS 密钥。

`AITRAVEL_JWT_SECRET` 和 `LOCAL_MEDIA_TOKEN` 已自动生成，通常无需修改。不要把密钥贴进聊天或前端代码。

```powershell
.\start-local-real.ps1 -CheckOnly
.\start-local-real.ps1
```

启动器先以禁止云调用的状态启动，验证 HTTPS 隧道确实回到当前本机服务，再启用云调用。缺少密钥、配置错误或隧道验证失败时停止，不回退到测试或生产。

## 微信开发者工具里怎么测试

本地工程已设置为游客 AppID `touristappid`，不复用已注册小程序的 AppID。打开根工程或其 `client` 工程，使用开发者工具内的模拟器；根工程已设置 `miniprogramRoot=client/`，后端和密钥不属于小程序代码目录。若工具仍显示旧 AppID，请重新导入这个本地工程。不改微信公众平台的生产 request 域名，也不要上传发布这个本地包。

本地 HTTP 调试需要开发者工具允许跳过合法域名/HTTPS 校验。当前地址为 127.0.0.1，适用于电脑内的开发者工具模拟器；手机真机的 127.0.0.1 是手机自身，这套默认配置不支持直接真机连接。

验收顺序：漓江 → 热门/全部模板 → 就拍这个 → 按角色上传照片 → 确认本地测试订单 → 人脸检查 → 角色三视图与定妆照 → TOS 保存定妆照 → 前端展示定妆照 → 用户点击“下一步生成视频” → Seedance 视频 → 本机混入模板配乐 → 独立账号 TOS 私有桶 → 作品页播放。先用单人模板，再测双人、三人；银子岩没有测试模板时保持空列表。

人脸失败不能进入付款制作。新订单跳过开发付款后自动制作定妆照、保存，再提交视频；无需二次确认。二期编排服务重启会查询原任务，下游提交状态不明确时保留核实状态，避免盲目重复生成。

图片模型固定为 `doubao-seedream-5-0-pro-260628`、`1K`；视频沿用 `doubao-seedance-2-0-mini-260615`、竖屏 9:16、15 秒等既有参数。

## 排查与停止

```powershell
# 本机健康状态（cloud_calls_enabled 区分是否允许真实调用）
Invoke-RestMethod http://127.0.0.1:8080/api/v2/health
# 不输出环境变量或密钥，只查看容器状态
 docker ps --filter label=com.docker.compose.project=aitravel-local-real
# 查看指定服务日志（素材链接也不要公开分享）
 docker logs --tail 60 aitravel-local-real-phase2-api-1
# 保留数据卷停止本地服务
.\start-local-real.ps1 -Down
```

启动脚本是兼容 Windows PowerShell 5.1 的 ASCII 源码，避免此前中文编码导致的“缺少右花括号”错误。不要使用 `down -v`，它会删除本地数据。

## 自动验证

镜像提供不调用云端的边界测试：

```powershell
docker exec aitravel-local-real-seeddance-video-1 python -m unittest local.test_contracts -v
docker exec aitravel-local-real-tos-storage-1 python -m unittest local.test_storage -v
# 须处于 -Offline 模式；校验运行中的网关、上传、鉴权、缓存及重启持久化
python server/local/smoke_local.py
```

测试覆盖 JWT、云调用开关、素材域名限制、三个真实模板角色素材、人脸官方响应结构、跨项目/重试任务身份和 FFmpeg 本地配乐保存。它们用替代响应隔离计费接口，**不代表真实云 API 已验收**。

## 本次验证记录（2026-09-30）

以下为 9 月 30 日历史记录，当前状态见文末 10 月 1 日记录。

- Windows PowerShell 5.1：初始化、配置检查、本地服务启动通过；缺少真实凭证时正确拒绝启用云调用。
- 10 个本地服务容器运行（另有素材隧道容器）；业务端口、数据库端口及缓存端口均只绑定本机回环地址。
- 8 项 provider 边界测试及 6 项存储/凭证隔离测试通过（含真实 TOS SDK 离线签名）；运行中容器的凭证字段隔离检查通过；真实运行的模板素材、上传、JWT、跨服务 Redis 角色映射、重启持久化、素材网关拒绝业务写请求检查通过。
- Nginx 使用 Docker DNS 动态解析，容器重建后可更新服务地址；配置校验通过。
- 前端 JavaScript 语法、仅本机业务 URL、禁止 trial/release、游客 AppID 和小程序代码目录隔离检查通过。
- 当前运行在 `-Offline` 检查模式，云调用关闭。素材隧道已配置，但本地云密钥和 TOS 桶信息尚未填齐，已配置的旧素材组权限也尚未验证，因此**尚未完成真实云端生成验收**，也未验证微信开发者工具中的实际页面操作。生产/测试服务器、微信公众平台及已发布版本未修改。

## 竖屏调整验证（2026-10-01）

- 已重建本地容器，`/api/v2/health` 返回 `environment=local-real`、`status=ok`、`cloud_calls_enabled=true`。
- 定妆照使用 Seedream 5.0 Pro 官方 1K 竖屏尺寸 `800x1424`；视频 SDK 提交 `ratio=9:16`。前端确认页与作品页采用 9:16 容器完整展示。
- 定妆照仅展示、保存为用户资产，不提交给视频模型；二期不生成分镜图，定妆照保存后视频由后台自动提交。
- 39 项离线测试通过：图片请求尺寸 2 项，流程/确认/视频输入 28 项，存储 9 项。Python 编译与两个结果页面的 JavaScript 语法检查通过。本轮没有调用真实图片或视频生成接口，微信开发者工具中的视觉效果仍需刷新后查看。
