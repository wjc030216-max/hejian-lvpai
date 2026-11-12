# 贺见旅拍小程序技术文档

> 版本：v4（2026-10-23）
> 环境：本地开发与独立部署
> 本文档描述贺见旅拍后端、小程序前端、部署、数据模型、接口与运维。旧桂智旅目录不属于本项目。

## 1. 项目概述

二期在小程序内完成「选模板 → 上传角色照片 → 人脸检查 → 下单 → 生成定妆照 → 生成旅行视频 → 保存/分享」的完整链路。

- 定妆照与角色三视图由火山方舟 Seedream 生成。
- 旅行视频由火山 Seedance 生成，参考图先注册进 Seedance 资产库，再用 `asset://<asset_id>` 引用。
- 最终产物统一转存火山 TOS；用户照片、中间图、提示词、生成媒体和分享统一保留 90 天，到期后后台清理。退款成功会立即删除 TOS 最终对象，但不删除用户原始自拍。
- 后端是面向「生成任务可能失败/超时/响应丢失」设计的 durable（可重启恢复）系统，付费提交先落库再发请求，绝不盲目重复计费。

### 技术栈

- 后端：Python 3.11 + Flask + waitress + SQLAlchemy + MySQL 8.4 + Redis 7.4
- 前端：微信小程序原生（WXML / WXSS / JS）
- 云服务：火山 Ark（Seedream 生图、Seedance 视频、Seedance 资产库）、阿里云 FaceBody 人脸检测、火山 TOS
- 部署：Docker Compose，三节点（main / ai / data）

## 2. 服务器与部署拓扑

| 节点 | 公网 IP | 内网 IP | 服务（容器） | 端口 |
|------|---------|---------|--------------|------|
| main | 待配置 | 10.0.0.12（示例） | phase2-api、logcollector、gateway(nginx) | 18020 / 18031 / 443 |
| ai | 待配置 | 10.0.0.13（示例） | personattr、seedream、charactergen、video | 18002 / 内部 / 18005 / 18006 |
| data | 待配置 | 10.0.0.11（示例） | mysql、redis、file、tos、admin | 13306 / 16379 / 18004 / 18012 / 18030 |

部署目录统一为 `/opt/hejian-lvpai/`，每个节点一份 `.env.<node>`（不打进发布包、不入库）。

服务职责：

- `phase2-api`（main）：模板目录、作品/订单、支付、人脸检查与生成任务的编排调度器，对外唯一业务入口。
- `logcollector`（main）：接收三节点结构化日志，按类别落盘 + 写 MySQL。
- `gateway`（main）：HTTPS 443 反代 `/api/v2/`、`/admin/`、`/media/...`，私有服务端口不对外。
- `personattr`（ai）：人脸检测（阿里云 FaceBody）。
- `seedream`（ai）：Seedream 生图（定妆照与角色三视图共用）。
- `charactergen`（ai）：角色换装三视图 + 定妆照任务编排（durable_generation）。
- `video`（ai）：Seedance 视频 + Seedance 资产库（durable_generation）。
- `file`（data）：用户照片/生成图的本地文件存储。
- `tos`（data）：唯一持有 TOS 凭证，负责视频/定妆照转存与签名。
- `admin`（data）：运营管理台（模板、制作记录、运行监控、日志）。

## 3. 代码目录结构

```text
贺见旅拍/
├── client/                       # 微信小程序
│   ├── app.js / app.json / app.wxss
│   ├── config/                   # 环境与目标地址
│   ├── utils/                    # 请求、会话、UI、支付、API
│   ├── state/                    # 本地缓存状态
│   ├── styles/                   # 全局设计 token
│   └── pages/phase2/             # entry/upload/work/works/share 五个页面
├── server/
│   ├── common/                   # 所有服务共享的基础设施
│   ├── phase2/                   # 主业务 API + 编排器 + 支付 + 存储
│   ├── charactergen/             # AI 节点：角色/定妆照服务
│   ├── ai2pic/                   # AI 节点：seedream 生图服务
│   ├── aitask/                   # AI 节点：视频服务
│   ├── personattr/               # AI 节点：人脸检测服务
│   ├── file/                     # data 节点：文件服务
│   ├── local/                    # data/AI：TOS 存储服务、视频本地合成
│   ├── logcollector/             # main：日志采集
│   ├── admin/                    # data：运营管理台（含静态前端）
│   └── staging/                  # 三套 Dockerfile
├── infra/staging/                # docker-compose、打包/启动/校验脚本、nginx
├── deploy_all.ps1 / setup_ssh_keys.ps1
└── 各类文档 md
```

## 4. 后端模块详解

### 4.1 common/（共享基础设施）

- `runtime.py`：统一加载项目 `.env`（`PHASE2_ENV_FILE` 显式指定），`require_env` 启动即失败快速报错。
- `service_app.py`：`protect()` 内部服务 JWT 鉴权（校验 `sub` 白名单）；`serve()` waitress 启动（线程数 `SERVICE_THREADS`，请求体 512MB，`CHANNEL_TIMEOUT` 默认 120s）。
- `internal_auth.py`：手工构造 300 秒短时 HS256 JWT，供服务间互调。
- `staging.py`：测试环境 fail-closed 校验（微信登录 + MySQL + 二期测试库 + HTTPS 域名 + 密钥长度）。
- `local_guard.py`：本地/测试环境云端开关（`LOCAL_CLOUD_ENABLED`）与媒体 URL 域校验。
- `service_log.py`：统一异步日志出口，按类别批量上报 main 节点 logcollector，队列满丢弃、不阻塞业务。
- `ark_errors.py`：火山 Ark 错误统一映射为「中文文案 + 是否可重试」；可重试抛 `RuntimeError`，确定性失败抛 `CloudFailed`。
- `database_schema.py`：幂等 schema 迁移（`phase2_schema_versions` + MySQL 命名锁），建模板/任务/媒体对象表并做 identity 去重。
- `management_schema.py`：运营表（模板设置/选择、审计、登录限流、任务事件）。
- `task_journal.py`：durable 任务唯一权威状态源（`TaskJournal`）。`step()` 先落 `submitting` 再发请求；`retry()` 仅对 `cloud_failed` 允许清计费步骤重发；MySQL 用短事务租约和 `GET_LOCK`、SQLite 用进程锁，云请求期间不占用数据库锁连接。
- `durable_generation.py`：`Generation` worker，驱动 charactergen / video 两类任务；异常四分类（TaskPending / CloudFailed / ValueError / 其他→needs_review/recovering 指数退避）。
- `durable_images.py`：seedream 服务的幂等图片边界（`request_id` 派生 run，先拿 URL 再下载落盘，响应丢失可恢复）。
- `video_references.py`：视频参考图「图片N」规范映射（用户自拍→三视图→模板人物→分镜→场景，上限 9 张）与提示词渲染。
- `seedream_provider.py`：火山 Ark Seedream 5.0 Pro 适配（官方尺寸表、显式像素尺寸防横图带偏、下载后归一化为 `data[0].b64_json`）。
- `video_provider.py`：Seedance 2.0 Fast + 资产库（手写火山 OpenAPI HMAC-SHA256 签名，`CreateAsset/GetAsset/DeleteAsset`，视频任务提交/轮询）。
- `aliyun_face_provider.py`：阿里云 FaceBody `DetectFace` + `PedestrianDetectAttribute`（流式上传，返回人数/质量分/年龄/性别/眼镜）；确定性图片错误抛 `FaceRejected`，与临时服务故障区分。
- `portrait_provider.py`：charactergen 业务逻辑（`_build_role_bindings` 按 character_id 绑定、一人体验校验、`PROMPT_STEP1` 换装三视图提示词）。
- `storage_client.py`：内部存储客户端（不含 TOS 凭证；视频/定妆照上传 TOS、取签名 URL；本地读 file 服务再转存）。
- `template_client.py`：从 phase2-api 拉取 `P2S-` 模板运行快照并校验。
- `media_refs.py`：媒体引用统一用 `local-media:` 前缀持久化，`transform()` 转当前安装 URL + 加访问令牌。
- `media_validation.py`：示例视频校验（当前仅校验「可播放」：ffprobe 读头 + ffmpeg 解码一帧）。
- `asset_registry.py`：`phase2_media_objects` 登记视频/定妆照对象。
- `admin_password.py`：管理员密码 scrypt 哈希。
- `http_client.py`：线程本地 `requests.Session` + `HTTPAdapter` 连接池（高频内部调用复用连接）。

### 4.2 phase2/（主业务）

- `phase2_app.py`：对外唯一业务入口。模板目录、项目 CRUD、照片上传、订单/支付、作品/消息/分享、分享缩略图。
- `store.py` / `mysql_store.py`：项目/事件/租约的 SQLite 与 MySQL 存储；MySQL 版通过 SQL 重写复用逻辑，并维护订单/角色/资产投影表。
- `catalog.py`：模板目录按管理员设置的热度降序排列，热度相同按模板编号稳定排序；支持 scene/actor_count/gender/relationship 筛选。
- `auth.py`：微信登录（jscode2session）、用户 JWT、媒体 JWT、项目归属校验。
- `orchestrator.py`：`Phase2Orchestrator` 调度器（照片检查池 + 生成调度池，3 秒轮询；提交先落库、`_reconcile` 对账、`_poll_once` 轮询、完成/失败处理、订阅消息通知）；人脸临时故障最多重试 6 次，避免单图无限 `checking`。
- `runtime_templates.py`：从模板文件夹构建 `managed_runtime` 运行快照。
- `participation.py`：一人/多人体验的角色选择与生成 actors 构造。
- `wechat_pay.py`：微信支付 V3 JSAPI（RSA 签名、AES-GCM 解回调、`client_pay_params`）。
- `allinpay.py`：通联 W06S 收银台适配。
- `wechat_subscribe.py`：一次性订阅消息（获取/缓存 access_token、`message/subscribe/send`）。

### 4.3 AI 节点

- `charactergen_app.py`：`install(portrait_provider,'character')`，生成角色三视图 + 定妆照。
- `seeddance_video_app.py`：`install(video_provider,'video')`，视频任务。
- `seedream_app.py`：seedream 生图服务；`PHASE2_DURABLE_TASKS=true` 时用 `durable_images.install` 幂等化。
- `personattr_app.py`：人脸检测，错误脱敏。

### 4.4 data 节点

- `file_app.py`：上传/下载，`secure_filename` + `realpath` 双重防路径穿越，30MB 上限，按 wx_openid 分目录。
- `local/tos_app.py`：唯一持有 TOS 凭证的容器，只暴露上传/取签名 URL，无删除/列举/桶管理。
- `local/tos_storage.py`：TOS 客户端封装，私密 ACL，签名 TTL 300~86400 秒，key 前缀限定。
- `local/results.py`：下载云端视频 + ffmpeg 混 BGM，原子落盘。
- `logcollector_app.py`：接收批量日志，按类别/北京日期落盘 + 写 `phase2_service_logs`。
- `admin/`：运营管理台（Flask + 静态前端），按模板中心、交易与退款、运营监控、日志中心四个模块组织；模板中心支持 15 秒单段和 30 秒 A/B 双段模板，显示分段、角色服装和定妆主造型，并支持按时长筛选；同时包含统一交易筛选、批量热度、用户过程数据、任务处置、服务状态、三类日志、审计、登录限流和 CSRF。

## 5. 数据库设计

共享库：`aitravel_phase2_test`（data 节点 MySQL 8.4，内网 13306，utf8mb4）。

### 5.1 模板目录

`phase2_templates`：`id`、`template_code`(UNIQUE)、`location_association`、`video_genre`、`actor_count`、`status`、`sort_order`、`payload_json`(LONGTEXT，含 managed_runtime/asset_revision/cover/example)、`identity_hash`、`created_at/updated_at`。

`phase2_template_settings`：`template_code`(PK)、`heat_base`、`show_heat`、`revision`（乐观锁）。

`phase2_template_selections`：`template_code`、`owner_hash`、`created`，联合主键（按用户+模板去重）。

### 5.2 项目与订单（MySQLStore）

`phase2_projects`：`id`(PK)、`owner_id`、`status`、`runnable`、`due`、`paid_at`、`order_no`(UNIQUE)、`payload`(LONGTEXT)。

`payload` 关键字段：project_id、wx_openid、template_code、template、template_version、runtime_code、roles[]、status、stage、attempt、participation_mode、selected_person_uid、order_no、payment_mode、payment_status、amount_fen、paid_at、portrait_url、portrait_asset_id、video_url、video_task_id、character_task_id、role_mapping[]、runtime_snapshot、subscribe_openid、subscribe_template、subscribe_sent、progress、error、created_at、completed_at、next_run_at、last_tick_at。

`phase2_orders`：`order_no`(PK)、`project_id`(UNIQUE)、`owner_id`、`amount`、`payment_mode`、`paid_at`。

`phase2_roles`：`project_id/role_id` 联合主键、`source`、`photo_asset_id`、`check_status`、`payload`。

`phase2_assets`：`id`、`project_id`、`kind`(upload/portrait/video)、`reference`。

`phase2_events`：`id`、`project_id`、`kind`、`title`、`created`、`seen`。

`phase2_leases`：`id`(PK)、`owner`、`expires`（调度租约）。

`phase2_refunds`：`refund_no`(PK)、`project_id`(UNIQUE)、订单号、全额退款金额、
退款状态、通联退款号/流水号/交易状态、请求响应快照、重试时间及处理人。

`phase2_migrations`：`name`、`applied_at`。

### 5.3 durable 任务（TaskJournal）

`phase2_tasks`：`id`(PK)、`kind`(image/character/video)、`run_id`(wx_openid)、`status`、`next_run`、`lease_owner`、`lease_until`、`payload`(LONGTEXT)、`UNIQUE(kind,run_id)`。租约只负责避免多进程重复执行，任务状态仍以 `payload` 为权威。

`payload` 关键字段：task_id、type、wx_openid、template_code、status、steps{step:{status,result,started_at}}、attempt、error、result、role_mapping[]、reference_plan[]、generation_parameters、created_at、next_run、cloud_failed。

`phase2_task_events`：任务状态历史。

### 5.4 运营与日志

- `phase2_admin_audit`：管理员操作审计。
- `phase2_admin_attempts`：登录限流。
- `phase2_service_logs`：集中式服务日志（保留 90 天，可调）。
- `phase2_media_objects`：TOS 媒体对象登记（id/kind/bucket/object_key/mime/size/宽高/sha256）。
- `phase2_schema_versions`：结构版本标记。

## 6. 核心流程与状态机

项目状态机（存于 `phase2_projects.payload.status`）：

```text
draft → uploading → pending_payment → portrait_submit
      → portrait_unknown / portrait_processing → portrait_saving
      → video_submit → video_unknown / video_processing → completed
```

终态：`completed`、`terminated`（生成失败，用户可选择重做或退款）、`refund_pending`（退款处理中）、
`refund_failed`（退款失败待处理）、`refunded`（退款成功并归档）、`archived`（历史作品）。

链路：

1. 首页浏览模板，`POST /api/v2/projects` 创建草稿（request_id 幂等）。
2. 上传页上传照片，`file` 服务落盘，编排器照片池调 `personattr` 人脸检查。
3. `POST /api/v2/projects/<pid>/order` 下单：dev-bypass 直接进生成；wechat/allinpay 走支付预下单/回调。同时可选记录订阅消息凭证。
4. 编排器提交 `charactergen`：先生成角色三视图，再生成定妆照。
5. 定妆照转存 TOS 后，提交 `video`：参考图注册进 Seedance 资产库 → 提交视频。
6. 视频完成后转存 TOS，项目 `completed`，发一次性订阅消息，前端展示并可保存/分享。

失败与重试：

- 云端确定性失败标记任务终态，编排器自动重试 1~2 次；付费订单最终失败后由用户选择重新生成
  或申请全额退款。退款成功后进入 `refunded`，作品停止播放、保存和分享。
- 提交后响应丢失进入 `needs_review`，不自动重复计费提交。
- 人脸检查的「临时故障」（网络/502/503）保持 `checking` 并 10 秒后重试，最多 6 次（约 60 秒）；超过后标记为 `rejected` 并提示更换照片，避免单图无限转圈。
- 阿里云返回的确定性图片错误（如 `InvalidImage`、`InvalidParameter`、格式不支持）直接标记为 `rejected`，不再按临时故障重试；`face` 日志会记录真实错误码。

## 7. 前端架构与交互

页面（`pages/phase2/`）：

- `entry`：原生竖向 `swiper` 模板视频流。为控制 400+ 模板下的节点数，只渲染当前项前后共 13 条（当前项在第 4 位），滑到窗口边缘才重设窗口；右侧提供 ↑/↓ 切换按钮。首次进入显示半透明大手引导，只有真正滑动或点按钮切换后才消失。标题、简介和「分享/拍同款/更多模板」位于每个 swiper 页内部，整屏可滑动；首页按钮为「拍同款」，不显示价格。顶部景区/我的作品；更多模板抽屉筛选为「人数 / 场景」，选 1 人时出现性别、选 2+ 人时出现关系。
- `upload`：已选模板摘要、多人同框/一人体验对比卡、角色上传（JPG/PNG、人脸检查三态）、「还需要上传 X 张照片」、授权卡（默认勾选）、价格与支付卡。底部按钮按状态切换：请先上传 X 张 / 正在检查人脸 / 请更换未通过的照片 / 立即支付·开始制作；支付在上传并通过人脸检查之后。
- `work`：三步时间轴（支付 → 定妆照 → 旅行视频）、定妆照/旅行视频两张媒体卡、阶段进度卡、可离开提示、「遇到问题」帮助、订单号复制/联系客服；生成失败后由用户选择重新生成或申请全额退款。
- `works`：状态卡列表。完成态两张素材各自保存，整张作品只有一个分享按钮；制作中显示旋转指示和「查看进度」；失败卡显示原因和「查看处理」。
- `share`：公开只读分享页，使用独立字面量 WXSS（不依赖 CSS 变量/grid/inset），3:4 素材卡、每张各自保存、整页一个「分享给朋友」；「我也要拍同款」按模板 code 精确回首页。

关键工具：

- `utils/request.js`：统一请求（15 秒超时、鉴权头、跨环境拦截）。
- `utils/phase2Session.js`：微信登录会话（并发去重、提前 30 秒过期）。
- `utils/phase2Api.js`：接口封装、模板详情 `getTemplate(code)`、订阅模板 ID。
- `utils/phase2Payment.js`：微信支付 / 通联 W06S 收银台跳转。
- `utils/phase2Ui.js`：安全区布局、状态文案映射、保存到相册（下载时带 `download=1` 走 API 域名流式回传，视频落盘为 `.mp4`、图片按原后缀）。
- `state/phase2State.js`：浏览种子、草稿、项目 id、引导/滑动提示等本地缓存（按环境+域名分 namespace）。
- `styles/phase2-v2.wxss`：全局主题（青绿山水·胶片 token：墨青底 + 暖金 + 青碧）。

App 级能力（`app.js`）：

- 全局作品观察者：前台每 15 秒轮询作品列表，对未完成任务做状态跃迁提醒（完成/失败弹窗、定妆照 toast），带通知队列、状态去重、work 页聚焦握手、页面栈兜底。

## 8. 接口清单

### 8.1 phase2-api（网关 `/api/v2/`，用户 JWT，公开接口单独标注）

模板与配置：`POST /auth/login`（公开）、`GET /config`（公开）、`GET /health`（公开）、`GET /media-ready`（公开）、`GET /scenes`（公开）、`GET /templates`（公开，按管理员热度排序，支持 scene/actor_count/relationship/gender/分页）、`GET /templates/<code>`（公开）、`GET /templates/<code>/runtime`（内部）。

项目与订单：`POST /projects`、`GET /projects/<pid>`、`GET /projects/<pid>/status`、`POST /projects/<pid>/participation`、`POST /projects/<pid>/assets`、`GET /assets/<aid>`（媒体）、`POST /projects/<pid>/order`、`POST /projects/<pid>/prepay`、`POST /payments/notify`（公开）、`POST /payments/callback`（预留）、`GET/POST /payments/allinpay/notify`（公开）、`POST /projects/<pid>/retry`、`POST /projects/<pid>/refund`。

作品与分享：`GET /works`、`GET /works/watch`（轻量状态轮询）、`POST /works/status`、`POST /projects/<pid>/read`、`GET /messages`、`GET /results/<result_id>`（媒体；默认 302 到 TOS，带 `download=1` 时由 API 域名流式回传并转发 Range）、`GET /portraits/<result_id>`（同上）、`GET /share/<pid>`（公开，含 template_code）、`GET /share-image/<pid>.jpg`（公开）、`GET /assets/<path>`（公开）。

### 8.2 AI 节点

charactergen：`POST /api/v1/image/generatecharacter`、`GET /api/v1/image/task/<task_id>`、`GET /api/v1/image/task/by-user`、`POST /api/v1/image/task/<task_id>/retry`、`GET /api/v1/image/health`。

video：`POST /api/v1/video/generate`、`GET /api/v1/video/task/<task_id>`、`GET /api/v1/video/task/by-user`、`POST /api/v1/video/task/<task_id>/retry`、`GET /api/v1/health`。

seedream：`POST /api/v1/images/generate`、`GET /api/v1/images/health`。

personattr：`POST /api/v1/personattr/detect`、`GET /api/v1/personattr/health`。

### 8.3 data 节点

file：`POST /api/v1/file/upload`、`GET /api/v1/file/download/<wx_openid>/<filename>`、`GET /api/v1/file/health`。

tos：`GET /health`、`POST /internal/results/<result_id>`、`GET /internal/results/<result_id>/url`、`POST /internal/portraits/<result_id>`、`GET /internal/portraits/<result_id>/url`。

admin：`GET /admin/`、`GET /admin/static/<path>`、`POST /admin/api/login`、`GET /admin/api/session`、`POST /admin/api/logout`、`GET /admin/api/templates`、`POST /admin/api/templates/upload`、`POST /admin/api/templates/batch-delete`、`PATCH /admin/api/templates/<code>`、`GET /media-assets/<revision>/<filename>`、`GET /admin/api/projects`、`GET /admin/api/projects/<pid>`、`GET /admin/api/projects/<pid>/monitor`、`GET /admin/api/user-monitor`、`GET /admin/api/user-monitor/<owner_id>`、`GET /admin/api/tasks`、`GET /admin/api/tasks/<task_id>`、`GET /admin/api/refunds`、`PATCH /admin/api/refunds/<refund_no>`、`GET /admin/api/logs`、`GET /admin/api/service-logs`、`GET /admin/api/overview`、`GET /admin/api/monitor`。

video 内部清理接口：`DELETE /api/v1/video/result/<task_id>`、`DELETE /api/v1/video/assets/<asset_id>`。

logcollector：`POST /api/v1/logs/ingest`、`GET /health`。

## 9. 关键特性清单（本轮迭代新增）

- 前端视觉重构：青绿山水·胶片 token、三步制作时间轴、三态状态胶囊、骨架屏、统一按压态、完成庆祝彩带、静音自动播放。
- 首页交互：400+ 模板使用 13 条滑动窗口，避免 swiper 全量渲染；右侧 ↑/↓ 按钮；首次半透明大手引导（滑动后消失）；标题和操作区放进 swiper 页，整屏可滑。
- 上传流程：已选模板摘要、模式对比、角色照片「还差几张」、状态化底部按钮；授权默认勾选；支付在上传和face检查通过之后。
- 作品与分享：作品页状态卡、整卡一个分享按钮、单个素材独立保存；分享页独立字面量样式，老机型不依赖 CSS 变量/grid；分享「拍同款」按模板 code 精确定位。
- 模板筛选：人数 / 景区 / 关系或性别；用户端只按管理员设置的热度从高到低展示，不再随机排序。
- 免费名额：`PHASE2_FREE_QUOTA_TOTAL=500` 时全局先到先得，下单时原子扣减；名额用完后同一接口自动按标准价走支付。名额只对 `wechat`/`allinpay` 生效，`dev-bypass` 不会消耗。免费订单记为 `payment_mode=free`，不产生退款，失败只能重新生成。
- 通联支付：下单 → `/prepay` 预下单 → 小程序打开 W06S 收银台 → `/payments/allinpay/notify` 验签入账；正式 `unifiedPay` 地址；支付成功后进入制作。
- 人脸检查：阿里云确定性图片错误直接 `rejected`；临时故障最多重试 6 次后停止并提示更换照片；`face` 日志记录真实错误码。
- 保存到相册：`download=1` 由 API 域名流式回传媒体字节，避免 `wx.downloadFile` 跟随 302 到 TOS 域名失败；视频落盘 `.mp4`。
- 媒体回放：管理台对最终定妆照和视频使用同源 `download=1` 流，避免 CSP 拦截 TOS 302；媒体流支持 `Range`，视频预览和拖动不会强制下载完整文件。
- 隐私保护：中央日志在上报和写入日志中心前统一移除媒体路径安装令牌、签名查询参数和服务 Bearer 令牌。
- 管理台：15 秒和 30 秒模板上传成功即自动上架；30 秒模板会展示 A/B 分段、各段角色和服装配置，并标记定妆主造型组。管理员可按 15/30 秒、人数和景区筛选，也可单条或多选批量设置展示热度。交易与退款模块统一展示订单、支付、交付和退款，支持状态、支付方式、时间、需处理项和关键词筛选。
- 运营监控：按用户查看最终作品与完整生成过程，按任务排查云任务和异常，并查看服务连通与容量；所有用户过程数据仅保留 90 天。
- 日志中心：管理员日志、程序日志和服务日志分别查询，均支持关键词、时间范围和分页。
- 订阅消息：下单时请求一次性订阅授权，视频完成自动推送「AI 写真制作完成通知」。
- 弱网加固：waitress channel_timeout 降到 120；高频内部调用走线程本地连接池。
- 工程：修复打包脚本引用已移动文档导致的 `FileNotFoundError`；前端按需注入、WXML/WXSS 压缩。

## 10. 并发与容量

目标容量：日活约 5000、瞬时并发约 100；云生图并发 500、视频并发 40。

- `PHASE2_ORCHESTRATOR_WORKERS=128`（生成调度 + 照片检查两个线程池各 128）。
- Seedream 请求线程和 charactergen worker 均按云侧 500 并发配置。
- `PHASE2_VIDEO_MAX_INFLIGHT=40`：视频付费提交并发上限，与云侧 40 对齐（跨进程锁 + `video_inflight` 计数）。
- 视频模型使用 `doubao-seedance-2-0-fast-260128`。
- 主 API 使用 64 个请求线程，Seedream 使用 500 个请求线程；项目数据库池 32 + 溢出 16，AI 任务池按服务独立配置，MySQL 上限 512。
- 管理端最终媒体使用 Range 同源流；小程序全局作品观察者使用 `/works/watch`，只读取状态、布尔媒体标记和视频名，不加载完整项目 JSON，也不签发媒体 URL。
- 5000 日活和 100 瞬时并发是当前设计目标，不等于已完成生产容量承诺。正式大促前仍需用真实 MySQL、Redis、上行带宽和云侧配额做峰值压测，并监控连接池等待、队列最老任务与云调用错误率。
- 2026-10-23 测试环境只读烟测：100 并发、200 次健康检查与模板目录请求全部 HTTP 200，p50 687.6 ms、p95 1764.5 ms、最大 1921.7 ms，约 103.7 req/s。该结果不包含付费 AI 长任务。

## 11. 安全设计

- 所有 `/api/v2/projects/<pid>` 在 auth 层校验 `wx_openid` 归属；作品列表、上传、下单、重试、退款均按 owner 过滤。
- durable 任务 id 由 `kind+wx_openid` 派生，重试校验归属。
- 媒体文件需服务端签发的路径令牌；唯一公开的是分享页。
- TOS 凭证只在 tos 容器；错误信息不暴露模型名/密钥；nginx 只放行私有内网端口；部署包白名单排除密钥。
- 模板 ZIP 导入做路径穿越/符号链接/大小/元数据/封面/示例视频等确定性校验。
- 管理员独立 JWT + CSRF + 登录限流 + 审计 + scrypt 密码哈希。

## 12. 部署与运维

本地打包：

```powershell
cd G:\贺见旅拍
$env:PHASE2_RELEASE_API_BASE_URL='https://<贺见旅拍正式API域名>'
$env:PHASE2_RELEASE_APP_ID='<贺见旅拍小程序AppID>'
python .\infra\staging\package_release.py
```

生成 `dist/phase2-main.tar.gz`、`phase2-ai.tar.gz`、`phase2-data.tar.gz`、`phase2-client.zip`。

一键部署三台（data → main → ai）：

```powershell
.\deploy_all.ps1
```

首次部署前，从 `infra/staging/deploy-targets.example.json` 复制并填写
`deploy-config/deploy-targets.json`。没有该文件或仍填写旧桂智旅主机时，部署脚本会拒绝执行。

仅重传某台：

```powershell
.\deploy_all.ps1 -SkipPackage -Only main   # main/ai/data
```

> 部署包不会覆盖服务器上的 `.env.<node>`。修改 env 后需要单独推送，例如 main：
>
> ```powershell
> scp .\deploy-config\.env.main <user>@<main-host>:/opt/hejian-lvpai/
> ```
>
> 推完 env 再执行 `start_node.py <node>` 或 `deploy_all.ps1 -Only <node>`。`start_node.py` 会校验 `LOCAL_CLOUD_ENABLED`、通联商户号/密钥、价格和 HTTPS 回调地址。

健康检查：

```powershell
curl https://<贺见旅拍正式API域名>/api/v2/health
```

返回 200 且 `environment=staging` 即正常。

常用运维：

```bash
# 模板回填分镜图（data 节点）
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml exec -T admin python /app/server/admin/backfill_storyboard.py

# 清理未引用素材/过期日志
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml exec -T admin python /app/server/admin/maintenance.py --apply

# 中央结构化日志（main 节点，按北京日期切文件）
tail -f /opt/hejian-lvpai/logs/face/$(date +%F).log
tail -f /opt/hejian-lvpai/logs/orchestration/$(date +%F).log
# 分类：login/payment/image/video/asset/tos/file/face/orchestration/admin/system

# 容器日志
docker logs -f --tail 100 hejian-lvpai-test-main-phase2-api-1
docker logs -f --tail 100 hejian-lvpai-test-ai-personattr-1
```

## 13. 环境变量

main（`.env.main`）：`AITRAVEL_DATABASE_URL`、`AITRAVEL_JWT_SECRET`、`WECHAT_APP_ID/SECRET`、`PHASE2_DB_*`、`PUBLIC_API_BASE_URL`、`PUBLIC_BASE_URL`、`TEST_DOMAIN`、`REDIS_URL`、`LOCAL_MEDIA_TOKEN`、`PHASE2_PAYMENT_MODE`、`ALLINPAY_*`、`WECHAT_PAY_*`、`AITRAVEL_AIVIDEO_PRICE_FEN`、`PHASE2_FREE_QUOTA_TOTAL`、`PHASE2_AI_ORCHESTRATION_ENABLED`、`PHASE2_ORCHESTRATOR_WORKERS`、`PHASE2_PROJECT_DB_POOL_SIZE/MAX_OVERFLOW`、`PHASE2_TASK_MAX_SECONDS`、`PHASE2_PROJECT_MAX_SECONDS`、`PHASE2_RETENTION_*`、`SERVICE_THREADS`、`LOCAL_CLOUD_ENABLED`、各服务内网地址、`WECHAT_SUBSCRIBE_TEMPLATE_ID`、`WECHAT_SUBSCRIBE_MINIPROGRAM_STATE`、`CHANNEL_TIMEOUT`、`HTTP_POOL_CONNECTIONS/MAXSIZE`。

ai（`.env.ai`）：`AITRAVEL_DATABASE_URL`、`AITRAVEL_JWT_SECRET`、`LOCAL_CLOUD_ENABLED`、阿里云 `ALIBABA_CLOUD_*`/`ALIYUN_FACE_*`、`SEEDREAM_API_KEY/MODEL`、`SEEDANCE_API_KEY`、`SEEDANCE_MODEL=doubao-seedance-2-0-fast-260128`、`SEEDANCE_ASSET_*`、`SEEDREAM_SERVICE_THREADS`、`CHARACTER_GENERATION_WORKERS`、`VIDEO_GENERATION_WORKERS`、`PHASE2_VIDEO_MAX_INFLIGHT`、各服务 `*_TASK_DB_POOL_SIZE/MAX_OVERFLOW`、`PHASE2_TASK_LEASE_SECONDS`、`PHASE2_TASK_MAX_SECONDS`、`TOS_SERVICE_URL`、`EXTERNAL_DOMAIN`、`PUBLIC_*`、`PHASE2_TEMPLATE_API_URL`、`IMAGE_SERVICE_URL` 等。

data（`.env.data`）：`AITRAVEL_DATABASE_URL`、`AITRAVEL_JWT_SECRET`、`ADMIN_USERNAME/PASSWORD_HASH/ADMIN_JWT_SECRET`、`ADMIN_MEDIA_TOKEN_SECONDS`、`MAIN_PRIVATE_IP`、`AI_PRIVATE_IP`、`MYSQL_ROOT_PASSWORD`、`REDIS_PASSWORD`、`TOS_*`、`PHASE2_DB_*`。管理台的健康检查地址由 Compose 注入，不在生产环境回退到测试 IP。

### 13.1 通联 W06S（生产接口）

```dotenv
PHASE2_PAYMENT_MODE=allinpay
ALLINPAY_API_URL=https://interface.allinpaygx.com/api/access/payInterface/unifiedPay
ALLINPAY_REFUND_API_URL=https://interface.allinpaygx.com/api/access/payInterface/refundsuhaimei
ALLINPAY_REFUND_VERSION=v1.0
ALLINPAY_MERCHANT_NO=<生产商户号，只放服务器 .env.main>
ALLINPAY_SECRET_KEY=<生产密钥，只放服务器 .env.main>
ALLINPAY_CASHIER_APPID=wxef277996acc166c3
ALLINPAY_NOTIFY_URL=https://<贺见旅拍API域名>/api/v2/payments/allinpay/notify
# 当前临时统一价 0.10 元；留空后启用 9.90 / 16.90 / 19.90 元分档价
AITRAVEL_AIVIDEO_PRICE_FEN=10
```

价格由服务端按模板人数重新计算：

- 1 人：990 分，即 9.90 元。
- 2 人：1690 分，即 16.90 元。
- 3 人：1990 分，即 19.90 元。

配置 `AITRAVEL_AIVIDEO_PRICE_FEN=10` 时使用统一覆盖价，三个档位当前均为 0.10 元。
需要恢复分档标准价时，将该变量留空并重启 main；客户端不参与最终金额计算。

凭据只放服务器 `.env.main`，不入库；本地 `deploy-config/.env.main` 是 gitignore 的镜像，用于 scp。`LOCAL_CLOUD_ENABLED` 在 main/ai/data 三个节点都必须为 `true`：main 关闭会直接拦截下单（「尚未启用真实云端调用」），ai 关闭会让人脸检测返回 503，data 关闭会让定妆照/视频转存 TOS 失败。

## 14. 环境配置：测试版（staging）vs 正式版（production）

> 代码已同时支持 staging 和 production。切换生产环境需要更换正式域名、数据库、TOS 前缀、订阅配置和发布目标，并按迁移方案处理已有媒体对象。

### 14.1 后端环境变量对照

| 配置项 | 测试版 staging | 正式版 production |
|--------|----------------|-------------------|
| `APP_ENV` | `staging` | `production` |
| `PHASE2_AUTH_MODE` | `wechat` | `wechat` |
| `PHASE2_STATE_BACKEND` | `mysql` | `mysql` |
| `PHASE2_DB_NAME` | `aitravel_phase2_test` | `aitravel_phase2`（正式库） |
| `PHASE2_DB_USER` / `PHASE2_DB_PASSWORD` | `phase2_test` / 测试密码 | 正式库专用账号 |
| `PHASE2_PAYMENT_MODE` | `allinpay` | `allinpay`（W06S 收银台） |
| `AITRAVEL_AIVIDEO_PRICE_FEN` | 10（临时统一价，0.10 元） | 留空后启用 990/1690/1990 分分档价 |
| `WECHAT_PAY_APPID/MCHID/APIV3_KEY/CERT_SERIAL_NO` | 测试商户 | 正式商户 |
| `WECHAT_PAY_NOTIFY_URL` | `https://<贺见旅拍API域名>/api/v2/payments/notify` | 正式域名回调 |
| `ALLINPAY_MERCHANT_NO/SECRET_KEY/NOTIFY_URL/REFUND_API_URL` | 正式商户配置（密钥只放服务器 `.env.main`） | 生产商户及生产退款地址 |
| `PUBLIC_API_BASE_URL` | 贺见旅拍测试 API 域名 | 贺见旅拍正式 API 域名 |
| `PUBLIC_BASE_URL` | `.../media/<token>` | 正式 `/media/<token>` |
| `TEST_DOMAIN`（nginx） | 贺见旅拍测试域名 | 贺见旅拍正式域名 |
| `LOCAL_CLOUD_ENABLED` | `true` | `true` |
| `WECHAT_SUBSCRIBE_TEMPLATE_ID` | 测试模板 ID | 正式模板 ID |
| `WECHAT_SUBSCRIBE_MINIPROGRAM_STATE` | `trial` | `formal` |
| `TOS_KEY_PREFIX` | `phase2-test/` | `phase2-prod/`（或正式前缀） |
| `REDIS_URL` | 测试 Redis（私网 16379） | 正式 Redis |
| `PHASE2_ORCHESTRATOR_WORKERS` / `SERVICE_THREADS` / 连接池 | 128 / 主 API 64、Seedream 500 / 按节点配置 | 生图 500、视频 40；AI TaskJournal 按服务独立配置 |
| 日志与用户过程数据保留 | 90 天 | 90 天 |

### 14.2 前端配置对照

前端环境配置在 `client/config/target.js` 和 `client/config/environment.js`。本地开发使用 `local`；发布包由 `PHASE2_RELEASE_API_BASE_URL` 和 `PHASE2_RELEASE_APP_ID` 生成 production 配置。

| 项 | 测试版 staging | 正式版 production |
|----|----------------|-------------------|
| `target.environment` | `staging` | `production`（代码已支持） |
| `target.apiBaseUrl` | `https://api.hejianlvpai.juxiaobai.com.cn`（测试服务器） | 贺见旅拍正式域名 |
| `environment.js` 的 `ENVIRONMENTS` | 已有 `local`/`staging` | 已支持 `production` |
| `version === 'release'` 校验 | 非 production 构建禁止发布 | 仅 `target.environment=production` 可发布 |
| `paymentChannel` | `allinpay` | `allinpay` |

### 14.3 正式上线配置清单

1. 在三份环境文件中统一配置 `APP_ENV=production`、`MAIN_PRIVATE_IP`、`AI_PRIVATE_IP`、`DATA_PRIVATE_IP`。
2. `start_node.py` 会按 production 校验正式数据库、专用用户、私网连接和 `phase2-prod/` TOS 前缀。
3. 更换正式 API 域名、证书、`PUBLIC_API_BASE_URL`、`PUBLIC_BASE_URL` 和通联回调地址。
4. 更新微信 request/uploadFile/downloadFile 合法域名并发布 production 小程序。
5. 配置正式订阅模板 ID；服务端在 production 默认使用 `formal` 状态。
6. 如更换 TOS 前缀，必须同时复制 TOS 对象并更新媒体记录，不能只改环境变量。
7. 完成正式库和文件卷的备份、恢复核对及受控支付退款验收。

> 安全提醒：任何真实密钥/商户私钥/正式证书都不能提交进 git。`.gitignore` 已忽略 `.env.*`、`dist/`、`tls/`、`server/phase2/cert/`、`deploy-config/`，请保持这些路径不被强制 `-f` 添加。

## 15. 已知限制与后续规划

- 订阅消息为「一次性」：每次下单需重新授权，且仅发「视频完成」一条；如要「定妆照完成」也通知，需另加模板或改长期订阅。
- 分享「拍同款」已按模板 code 通过 `GET /templates/<code>` 精确定位，不再受景区/分页限制；模板不存在时回退到普通列表。
- 生成失败后由用户选择重新生成或全额原路退款，不支持部分退款；已完成作品不提供退款。提交退款后禁止重新生成；退款同步返回处理中时，后台继续使用原退款单号核对，响应不明确时进入待核实。
- 分享链接在作品完成后的 90 天内有效，到期后返回 410；用户照片和过程数据由后台保留任务清理，财务订单和退款记录继续保留。
- 保存视频走 main 节点流式回传（`download=1`），会占用 main 出口带宽；大视频后续可评估 CDN 或 TOS 域名白名单方案。
- 全局作品观察者为前台轮询（15 秒），小程序完全关闭时无法通知，只能靠订阅消息补足。
- AI durable 任务连接池已按 worker 规模单独配置；云端 SDK 调用（Ark）仍各自创建连接，后续可评估 SDK 自带连接复用。
