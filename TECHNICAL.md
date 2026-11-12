# 贺见旅拍 · 技术文档

> 版本：2026-11-12
> 适用代码：本仓库全部源码（客户端 + 服务端 + 基础设施）
> 本文不含任何真实密钥、服务器地址、商户号；示例统一使用占位符。

---

## 1. 项目概述

贺见旅拍是一个「AI 旅拍」微信小程序：游客选择景区模板、上传自己的照片，系统将其替换为模板中的服装与妆造，生成一张定妆照和一段 15 秒旅行视频，可保存、分享。

核心业务链路：

```
选模板 → 上传照片 → 人脸/照片检查 → 生成角色三视图 → 生成定妆照 → 生成视频 → 合成配乐 → 转存对象存储 → 保存/分享
```

角色：

| 角色 | 说明 |
|---|---|
| 游客 | 微信用户，下单、上传照片、查看/保存/分享作品 |
| 管理员 | 后台上传模板、上下架、调热度、设免费名额、处理退款 |
| 系统 | 定时任务、任务恢复、媒体压缩、数据保留清理 |

---

## 2. 系统架构

### 2.1 三节点拓扑

生产环境为三台云主机，通过 VPC 私网互通：

| 节点 | 职责 | 对外 |
|---|---|---|
| 主节点 | Nginx 网关、二期 API、日志收集 | 唯一公网入口（80/443） |
| AI 节点 | 人脸检查、生图、角色生成、视频生成 | 不对公网开放 |
| 数据节点 | MySQL、Redis、文件服务、TOS 网关、管理后台 | 不对公网开放 |

私网地址在部署时通过环境变量注入（`MAIN_PRIVATE_IP` / `AI_PRIVATE_IP` / `DATA_PRIVATE_IP`），文档中以 `<PRIVATE_IP>` 表示。

请求路径：

```
小程序 → https://<API_DOMAIN>
        → Nginx（网关）
        → phase2-api（主节点）
        → 内部 HTTP：personattr / seedream / charactergen / video（AI 节点）
        → 内部 HTTP：file / tos-storage / mysql / redis（数据节点）
```

媒体路径：

```
模板媒体：小程序 → 网关 /media/<token>/assets/... → 数据节点文件服务
作品媒体：小程序 → /api/v2/results/<id> → 302 到 TOS 签名地址（或流式代理）
```

### 2.2 组件职责

| 组件 | 代码目录 | 说明 |
|---|---|---|
| phase2-api | `server/phase2` | 用户端 API、订单、支付、任务编排、模板目录、分享 |
| admin | `server/admin` | 管理后台（模板、订单、退款、免费额度、监控） |
| personattr | `server/personattr` | 阿里云人脸检测（合规检查、质量分） |
| seedream | `server/ai2pic` + `server/common/seedream_service.py` | 文生图/图生图（火山方舟 Seedream） |
| charactergen | `server/charactergen` | 角色三视图/定妆照的持久任务 worker |
| video | `server/aitask` | Seedance 视频生成、素材库、合成、转存 |
| file | `server/file` | 本地上传文件读写、媒体下载 |
| tos-storage | `server/local/tos_app.py` | 唯一持有 TOS 凭证，上传/签名/删除 |
| logcollector | `server/logcollector` | 服务日志汇总入库 |
| 公共库 | `server/common` | 鉴权、任务日志、媒体引用、存储客户端、错误映射 |
| 客户端 | `client` | 微信原生小程序（WebView 渲染） |

### 2.3 关键设计原则

1. **单一入口**：只有主节点网关暴露公网，数据库/Redis/对象存储/管理台均在私网。
2. **密钥分层**：TOS 凭证只在 tos-storage；视频素材库 AK 只在 video；人脸 AK 只在 personattr；业务服务不持有云凭证。
3. **付费调用只做一次**：所有计费步骤先落库 `submitting` 再发请求，恢复时绝不盲目重发。
4. **媒体引用与部署解耦**：数据库存 `local-media:` 相对引用，读取时按当前安装域名展开并签名。

---

## 3. 仓库结构

```
.
├─ client/                 微信小程序
│  ├─ pages/phase2/        入口、上传、制作、作品、分享
│  ├─ components/          通用组件
│  ├─ styles/              主题样式（含 phase2-v2.wxss）
│  ├─ utils/               API 封装、品牌 logo、UI 工具
│  ├─ state/               本地状态（按环境+域名隔离）
│  └─ config/              环境与 API 基址
├─ server/
│  ├─ phase2/              用户端 API 与业务编排
│  ├─ admin/               管理后台
│  ├─ personattr/          人脸检测服务
│  ├─ ai2pic/              图片生成入口
│  ├─ charactergen/        角色生成 worker
│  ├─ aitask/              视频生成 worker
│  ├─ file/                文件服务
│  ├─ local/               本地联调、TOS 服务、视频合成、测试
│  ├─ logcollector/        日志收集
│  ├─ common/              公共库
│  └─ staging/             生产 Dockerfile 与发布脚本
├─ infra/                  compose、nginx、发布打包
├─ docs/                   设计与审计文档
├─ start-local*.ps1        本地一键启动
└─ README.md               快速说明
```

---

## 4. 运行环境与配置

### 4.1 环境类型

| APP_ENV | 用途 | 云端调用 |
|---|---|---|
| `local` | 纯本地，SQLite，免登录 | 关闭 |
| `local-real` | 本地后端 + 真实云 API | 需 `LOCAL_CLOUD_ENABLED=true` |
| `staging` | 预发 | 是 |
| `production` | 生产 | 是 |

### 4.2 关键环境变量

| 变量 | 说明 |
|---|---|
| `APP_ENV` | 运行环境 |
| `AITRAVEL_JWT_SECRET` | 用户/内部服务/媒体 token 签名密钥（HS256） |
| `AITRAVEL_DATABASE_URL` | MySQL 连接串 |
| `REDIS_URL` | Redis 连接串 |
| `PHASE2_AUTH_MODE` | `wechat` / `local` |
| `WECHAT_APP_ID` / `WECHAT_APP_SECRET` | 微信小程序登录 |
| `PHASE2_PAYMENT_MODE` | `allinpay` / `dev-bypass` / `disabled` |
| `ALLINPAY_*` | 通联支付商户与密钥 |
| `PUBLIC_BASE_URL` | 媒体公网基址（含 `/media/<LOCAL_MEDIA_TOKEN>`） |
| `PUBLIC_API_BASE_URL` | API 公网基址 |
| `LOCAL_MEDIA_TOKEN` | 媒体路径命名令牌（非密钥） |
| `TOS_BUCKET` / `TOS_REGION` / `TOS_ENDPOINT` / `TOS_KEY_PREFIX` | 对象存储 |
| `TOS_SIGNED_URL_TTL_SECONDS` | 签名有效期 |
| `SEEDREAM_API_KEY` / `SEEDREAM_MODEL` | 图片生成 |
| `SEEDANCE_API_KEY` / `SEEDANCE_MODEL` | 视频生成 |
| `SEEDANCE_ASSET_ACCESS_KEY_ID/SECRET/GROUP_ID/PROJECT_NAME` | 视频素材库 |
| `ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET` | 阿里云人脸 |
| `LOCAL_CLOUD_ENABLED` | 本地是否允许真实云调用 |
| `PHASE2_FREE_QUOTA_TOTAL` | 每日免费名额兜底值（数据库设置优先） |
| `PHASE2_TASK_MAX_RECOVERIES` | 任务最大恢复次数，默认 8 |
| `ADMIN_JWT_SECRET` / `ADMIN_PASSWORD_HASH` / `ADMIN_USERNAME` | 管理台鉴权 |
| `MEDIA_TOKEN_TTL_SECONDS` | 内部媒体 token 默认 TTL |

> 生产密钥只存在于服务器 `.env.*` 文件，权限 600，不进入仓库。

### 4.3 客户端环境

`client/config/target.js` 决定 API 基址与环境；发布包由 `infra/staging/package_release.py` 注入生产配置：

```
PHASE2_RELEASE_API_BASE_URL=https://<API_DOMAIN>
PHASE2_RELEASE_APP_ID=wx****************
```

---

## 5. 数据模型

### 5.1 数据表

| 表 | 说明 |
|---|---|
| `phase2_projects` | 制作记录（payload JSON，状态机核心） |
| `phase2_roles` | 角色照片（按项目冗余） |
| `phase2_orders` | 订单与支付状态 |
| `phase2_refunds` | 退款单 |
| `phase2_templates` | 模板目录（payload JSON + identity_hash） |
| `phase2_template_settings` | 热度、展示、版本号 |
| `phase2_template_selections` | 选模板埋点 |
| `phase2_tasks` | 持久任务（kind: image/character/video/migration） |
| `phase2_task_events` | 任务状态变更事件 |
| `phase2_media_jobs` | 模板封面异步压缩任务 |
| `phase2_free_daily` / `phase2_free_settings` | 每日免费名额 |
| `phase2_service_logs` | 服务日志 |
| `phase2_admin_audit` | 管理台操作审计 |
| `phase2_admin_attempts` | 登录失败限流 |

### 5.2 项目状态机

```
draft → uploading → (照片检查) → pending_payment → paid → portrait_submit
→ portrait_processing → portrait_saved → video_submit → video_processing
→ completed → archived/expired
异常分支：needs_review / terminated / refund_pending / refund_failed / refunded
```

### 5.3 `projects.payload` 关键字段

| 字段 | 说明 |
|---|---|
| `project_id` / `wx_openid` | 归属 |
| `template_code` / `runtime_code` | 模板与运行时快照编号 |
| `roles[]` | 角色、照片、检查状态、`photo_asset_id`、`photo_url` |
| `role_mapping` | 角色 → 人脸/三视图/服装 URL |
| `portrait_url` / `portrait_asset_id` | 定妆照 |
| `video_url` / `video_poster` | 视频与封面 |
| `order_no` / `payment_status` / `amount_fen` | 订单 |
| `retention_expires_at` | 数据保留到期 |
| `refund_status` / `media_cleanup` | 退款与清理 |

---

## 6. 用户端 API

基址：`https://<API_DOMAIN>/api/v2`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | 微信 code 换 token（local 模式返回本地身份） |
| GET | `/config` | 支付方式、价格、免费额度、协议版本 |
| GET | `/templates` | 模板列表（分页、筛选、热度） |
| GET | `/templates/<code>` | 模板详情 |
| GET | `/templates/<code>/runtime` | 内部：运行时模型（需内部 JWT） |
| GET | `/templates/<code>/example.mp4` | 示例视频（302 到媒体） |
| GET | `/templates/<code>/cover` | 封面 |
| POST | `/projects` | 创建制作（幂等：request_id） |
| GET | `/projects/<pid>` | 项目详情（归属校验） |
| POST | `/projects/<pid>/assets` | 上传角色照片（multipart） |
| POST | `/projects/<pid>/participation` | 单人/多人参与设置 |
| POST | `/projects/<pid>/order` | 下单/免费名额 |
| POST | `/projects/<pid>/prepay` | 预支付（通联） |
| POST | `/projects/<pid>/retry` | 失败重试 |
| POST | `/projects/<pid>/refund` | 退款申请 |
| POST | `/projects/<pid>/read` | 标记已读 |
| GET | `/results/<id>` | 视频（302 到 TOS 或流式） |
| GET | `/results/<id>/poster` | 视频封面帧 |
| GET | `/portraits/<id>` | 定妆照（302 或流式） |
| GET | `/assets/<asset_id>` | 角色照片/素材 |
| GET | `/share/<pid>` | 分享页快照（公开） |
| GET | `/share-image/<pid>.jpg` | 分享卡片图（公开） |
| POST | `/payments/allinpay/notify` | 支付回调 |

约定：

- 响应统一 `{code, data, msg}`。
- 需要登录的接口校验 `phase2-user` JWT。
- 带 `pid` 的路由统一做归属校验（`auth.authorize`）。
- 媒体接口校验 `phase2-media` token（绑定 path + 过期）。

---

## 7. 生成流水线

### 7.1 总览

```
下单
 └─ 人脸检查（personattr）
     └─ 角色三视图（charactergen → seedream）
         └─ 定妆照（charactergen → seedream）
             └─ 视频（video → Seedance）
                 └─ 合成（ffmpeg：拼接 + BGM）
                     └─ 转存 TOS → 写回项目
```

### 7.2 三视图

- 输入：用户人脸照片 + 模板服装图（9.x）
- 输出：左侧大头照 + 右侧全身三视图（16:9）
- 提示词由「固定起手式 + 模板妆造」组成：
  - 起手式固定约束换装、保脸、构图
  - 妆造来自 `12.<角色序号>.<组>-妆造提示词.txt` 的 `makeup` + `skin_photography`
  - 结尾硬性要求：左侧特写是唯一人脸参考，右侧三视图面部模糊，仅作服装/体型参考
- 分辨率：1.5K（2048×1152）
- 幂等：`request_id = task_id:role:<character_id>:<attempt>`

### 7.3 定妆照

- 输入：人脸原图 + 三视图 + 场景
- 输出：9:16，1.5K（1152×2048）
- 提示词：角色映射 + 场景 + 模板 `11-定妆照提示词.txt`
- 保存：`save_portrait()` 从 TOS 取图并转存为展示用资产

### 7.4 视频

- 参考素材：三视图、场景、分镜、用户照片
- 素材库：先 `CreateAsset` 注册，再轮询 `GetAsset` 直到 `Active`
- 提交：Seedance 内容生成任务，轮询直到 `succeeded`
- 多段模板：A/B 两组各 15 秒，分别提交后拼接
- 合成：ffmpeg 下载 → 拼接 → 混 BGM → 校验时长 → 上传 TOS

### 7.5 任务持久化与恢复

- 所有任务写入 `phase2_tasks`，`steps` 记录每一步状态（`running/submitting/done/failed`）。
- 计费步骤先写 `submitting` 再发起请求；恢复时遇到 `submitting` 不重发，转人工核实。
- 租约（lease）防止多 worker 并发执行同一任务。
- 失败按指数退避重试，默认最多 8 次后转 `needs_review`。
- 503/超时可重试；422/400（参数或审核）视为永久失败。

### 7.6 失败分类

| 类型 | 处理 |
|---|---|
| 云端明确拒绝（400/422） | 直接失败，进入重试/退款 |
| 提交结果未知（超时、连接中断） | 标记待核实，不重复计费 |
| 图片/视频审核不通过 | 失败并提示更换素材 |
| 素材下载失败 | 检查 TOS 对象与签名，必要时重注册 |

---

## 8. 模板规范

### 8.1 打包

- 单个模板一个 ZIP，根目录为模板文件夹
- 支持 15 秒单段、30 秒 A/B 双段
- 必填文件：

| 文件 | 说明 |
|---|---|
| `1-基础元数据.txt` | JSON：角色、场景、时长、名称等 |
| `5-分镜提示词.txt` | JSON：groups[].Prompt（视频提示词） |
| `11-定妆照提示词.txt` | JSON：prompt |
| `2.1-场景设定图` | 场景参考图 |
| `3.x-角色设定图` | 模板角色图 |
| `9.x-角色服装图` | 服装三视图 |
| `6-封面图` | 列表封面 |
| `7-成片案例.mp4` | 示例视频 |
| `8-模板配乐.mp3` | BGM |
| `10.1-模板分镜图` | 分镜参考 |

- 可选：`12.<角色序号>.<组>-妆造提示词.txt`

### 8.2 妆造文件

```json
{
  "version": "makeup-evidence-v1",
  "character_id": "角色ID",
  "makeup": { "底妆": "...", "眉妆": "..." },
  "skin_photography": { "受光与明暗": "...", "自然肤色与环境色": "..." },
  "video_prompt_summary": "..."
}
```

校验：

- 文件名必须匹配 `12.<序号>.<组>-妆造提示词.txt`
- 序号必须对应模板角色
- `character_id` 必须与角色一致
- 同一「角色 + 组」不能重复

### 8.3 上传去重

- 同文件名（同 template_code）→ 跳过
- 同景区 + 同名称（identity_hash）→ 跳过
- 同一 ZIP 内容（SHA-256）→ 跳过，并记录 `source_sha256`
- 替换模式不受去重限制

---

## 9. 媒体与存储

### 9.1 引用模型

数据库持久化 `local-media:<相对路径>`，读取时按当前部署展开：

```
local-media:api/v2/assets/asset_xxx → https://<API_DOMAIN>/api/v2/assets/asset_xxx?access=<token>
```

### 9.2 TOS 对象

- Key：`<TOS_KEY_PREFIX>/assets/<asset_id>.<ext>`、`<TOS_KEY_PREFIX>/results/<id>.mp4`
- 私有 ACL，全部走签名 URL
- 签名 TTL 默认 1 小时（`TOS_SIGNED_URL_TTL_SECONDS` 可调）

### 9.3 媒体交付

| 场景 | 方式 |
|---|---|
| 结果视频 | `/api/v2/results/<id>` → 302 到 TOS 签名地址（`PHASE2_MEDIA_REDIRECT=true`） |
| 定妆照 | 同上（`/portraits/<id>`） |
| 角色照片 | `/assets/<id>` → 302 |
| 模板媒体 | 网关 `/media/<token>/assets/...` 代理文件服务 |

注意：

- 302 模式下客户端若在 URL 后追加参数会破坏签名；保存功能在客户端做兼容。
- 鸿蒙等不跟随跨域 302 的客户端由服务端按 UA 回退为流式代理。

### 9.4 缓存与保留

- 模板媒体：`Cache-Control: public, max-age=31536000, immutable`
- 分享图：`no-store`
- 作品保留期到期后清理 TOS 对象并停止访问
- 退款成功后归档并清理媒体

---

## 10. 支付与退款

### 10.1 通联 W06S

- 下单：创建 `pending_payment`，调用预支付
- 回调：校验签名 → 幂等更新订单 → 触发制作
- 金额：按模板配置价格（分）
- 正式环境强制 `allinpay`，禁止 dev-bypass

### 10.2 免费名额

- 每日共享额度，先到先得
- 存储：`phase2_free_settings.total` 为准，`PHASE2_FREE_QUOTA_TOTAL` 兜底
- 扣减：与订单写在同一事务，失败一起回滚
- 设 0 即关闭活动

### 10.3 退款

- 仅失败订单可退
- 状态机：`pending → submitting → succeeded/failed/needs_review`
- 退款单号幂等，重复提交不重复退
- 成功后归档作品并清理媒体

---

## 11. 安全设计

| 层 | 机制 |
|---|---|
| 用户 | 微信 code 换 JWT（aud=phase2-user），2 小时 |
| 归属 | 所有 `pid` 路由校验 `wx_openid` |
| 媒体 | `phase2-media` JWT，绑定 path，默认 TTL 1 小时 |
| 内部服务 | 独立 subject 白名单（`local-phase2` 等） |
| 管理台 | 独立 JWT + scrypt 密码 + CSRF + 登录失败限流 |
| 上传 | 类型/大小/数量限制，文件名清洗 |
| 密钥 | 仅存服务器 env，文件权限 600，不进仓库 |
| 隐私 | 相册写入需在微信后台声明「相册（仅写入）」 |

---

## 12. 部署

### 12.1 构建

```bash
docker build -f server/staging/Dockerfile-main  -t <registry>/main:latest  .
docker build -f server/staging/Dockerfile-ai    -t <registry>/ai:latest    .
docker build -f server/staging/Dockerfile-data  -t <registry>/data:latest  .
docker build -f server/staging/Dockerfile-admin -t <registry>/admin:latest .
```

### 12.2 三节点

```bash
# 主节点
docker compose --env-file .env.main -f infra/staging/docker-compose.main.yml up -d
# AI 节点
docker compose --env-file .env.ai -f infra/staging/docker-compose.ai.yml up -d
# 数据节点
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml up -d
```

### 12.3 发布客户端

```bash
PHASE2_RELEASE_API_BASE_URL=https://<API_DOMAIN> \
PHASE2_RELEASE_APP_ID=wx**************** \
python infra/staging/package_release.py
```

产物：`dist/phase2-client.zip`，用微信开发者工具上传并提审。

### 12.4 回滚

- 代码：保留上一版镜像 tag，`docker tag` 回退后重建容器
- 模板：保留上一版 revision，管理台可回滚
- 数据库：发布前 mysqldump，异常时导入

---

## 13. 可观测性

- `phase2_service_logs`：服务、级别、消息、上下文、trace
- `phase2_task_events`：任务状态迁移
- `/api/v2/health`：db/redis/环境
- 管理台监控页：主节点、人脸、生图、视频、存储连通性
- 关键日志：云端失败原文、素材状态轮询、幂等冲突、任务异常类型

---

## 14. 本地开发

### 14.1 纯本地

```bash
APP_ENV=local PHASE2_AUTH_MODE=local python server/phase2/phase2_app.py
```

SQLite + 本地文件，云端调用关闭，适合调页面与流程。

### 14.2 本地 + 真 AI

```bash
powershell -ExecutionPolicy Bypass -File .\start-local-real.ps1
```

- 独立 MySQL/Redis/文件/TOS 网关，端口 8080/8081/10030/3307/6380
- 真实云 Key 通过 `.env.local.real` 注入
- 媒体公网入口使用临时隧道，云端拉参考图需要它
- 与生产完全隔离：不同库、不同前缀、不同端口

### 14.3 客户端联调

`client/config/target.js` 指向 `http://127.0.0.1:8080`，开发者工具勾选「不校验合法域名」。上传前务必改回生产配置。

---

## 15. 排障手册

| 现象 | 原因 | 处理 |
|---|---|---|
| `图片生成请求参数或素材不合法` | 上游 400（下载失败/参数） | 查 seedream 日志的 body；确认参考图公网可达 |
| `Generation only accepts this installation's media URLs` | 媒体 URL 非本安装 | 检查 `PUBLIC_BASE_URL` / 签名 |
| `定妆照来源不是本地生成文件` | 端口/路径形式不匹配 | 确认 `/api/v2/assets/<id>` 或 `/media/<token>/api/v2/assets/<id>` |
| `InvalidParameter.DownloadFailed` | 云端拉素材失败 | 换 TOS 直签地址，避免 302 |
| `saveVideoToPhotosAlbum:fail api scope is not declared` | 未声明相册权限 | 微信后台补「相册（仅写入）」 |
| 创建制作过于频繁 | 限流 30 次/小时 | 等待或调整 `PHASE2_PROJECTS_PER_HOUR` |
| 任务长期「生成中」 | 恢复次数/租约 | 查 `last_error_detail`，必要时转人工 |
| 视频封面空白 | poster 相对地址 | 已修为绝对地址，待发布 |
| 分享图不更新 | 微信缓存 | URL 带 `?v=` 版本参数 |

---

## 16. 附录

### 16.1 任务 kind

| kind | 说明 |
|---|---|
| `image` | 单张图片生成（seedream 持久任务） |
| `character` | 角色三视图 + 定妆照 |
| `video` | 视频生成与合成 |
| `migration` | 数据迁移 |

### 16.2 媒体 token claims

```json
{"sub":"<subject>","aud":"phase2-media","path":"/api/v2/assets/asset_x","iat":0,"exp":0}
```

### 16.3 常用命令

```bash
# 健康检查
curl https://<API_DOMAIN>/api/v2/health

# 模板列表
curl https://<API_DOMAIN>/api/v2/templates?limit=10

# 本地日志
docker logs --since 10m <container>
```

---

## 17. 已知限制

1. 模板视频未压缩，单个 5–18MB，首屏流量较大。
2. 本地联调依赖临时隧道，断线会导致云端拉参考图失败。
3. 免费额度按天共享，不按用户。
4. 管理台单账号，无角色权限。
5. 媒体 token 不绑定用户，属能力 URL 模型。
