# 桂智旅小程序二期（AI 旅拍）技术文档

> 版本：v2（2026-10-13 更新）
> 环境：测试环境（staging），前端用微信开发者工具，后端跑在三台服务器上。

## 1. 项目概述

二期在小程序内完成「选模板 → 上传角色照片 → 人脸检查 → 下单 → 生成定妆照 → 生成旅行视频 → 保存/分享」完整链路。

- 定妆照与角色三视图由火山 Seedream 生成。
- 旅行视频由火山 Seedance 生成，参考资产先注册到 Seedance 资产库。
- 最终产物统一转存火山 TOS，作品与分享链接保留 30 天。

技术栈：

- 后端：Python 3.11 + Flask + waitress + SQLAlchemy + MySQL 8.4 + Redis 7.4
- 前端：微信小程序原生（WXML / WXSS / JS）
- 云服务：火山 Ark（Seedream 生图、Seedance 视频、Seedance 资产库）、阿里云 FaceBody 人脸检测、火山 TOS
- 部署：Docker Compose，三节点（main / ai / data）

## 2. 服务器与部署拓扑

| 节点 | 公网 IP | 内网 IP | 服务（容器） | 端口映射 |
|------|---------|---------|--------------|----------|
| main | 115.191.26.134 | 172.31.24.158 | phase2-api、logcollector、gateway(nginx) | 18020 / 18031 / 443 |
| ai | 115.190.53.29 | 172.31.24.159 | personattr、seedream、charactergen、video | 18002 / 内部 / 18005 / 18006 |
| data | 115.191.23.137 | 172.31.24.157 | mysql、redis、file、tos、admin | 13306 / 16379 / 18004 / 18012 / 18030 |

部署目录统一为 `/opt/guizhilv-phase2/`，每个节点一份 `.env.<node>`（不打进发布包、不入库）。

服务职责：

- `phase2-api`（main）：模板目录、作品/订单、支付、编排调度器，对外唯一业务入口。
- `logcollector`（main）：接收三节点结构化日志，按类别落盘 + 写 MySQL。
- `gateway`（main）：HTTPS 443 反代 `/api/v2/`、`/admin/`、`/media/...`，私有服务端口不对外。
- `personattr`（ai）：人脸检测（阿里云 FaceBody）。
- `seedream`（ai）：Seedream 生图（定妆照与角色三视图共用）。
- `charactergen`（ai）：角色换装三视图 + 定妆照任务编排（durable_generation）。
- `video`（ai）：Seedance 视频 + Seedance 资产库（durable_generation）。
- `file`（data）：用户照片/生成图的本地文件存储。
- `tos`（data）：唯一持有 TOS 凭证，负责视频/定妆照转存与签名。
- `admin`（data）：运营管理台（模板、制作记录、运行监控、日志）。

## 3. 业务数据流

项目状态机（存于 `phase2_projects.payload.status`）：

```text
draft → uploading → pending_payment → portrait_submit
      → portrait_unknown / portrait_processing → portrait_saving
      → video_submit → video_unknown / video_processing → completed
```

终态：`completed`、`terminated`（生成失败，dev-bypass 下无扣款）、`refund_pending`（待退款）、`archived`（历史作品）。

完整链路：

1. 首页按热度浏览模板，`POST /api/v2/projects` 创建草稿。
2. 上传页上传角色照片（`file` 服务落盘），`personattr` 做人脸检查。
3. `POST /api/v2/projects/<pid>/order` 下单：`dev-bypass` 直接进入生成；`wechat` 走微信支付 V3 预下单/回调。
4. 编排器提交 `charactergen`：先生成角色三视图，再生成定妆照。
5. 定妆照转存 TOS 后，自动提交 `video`：把参考图注册进 Seedance 资产库，再提交视频任务。
6. 视频完成后转存 TOS，项目 `completed`，前端展示并支持保存/分享。

失败与重试：

- 云端确定性失败（模板/参考图校验、鉴权、额度、内容审核等）标记任务终态失败，编排器自动重试 1~2 次，仍失败则 `terminated` / `refund_pending`。
- 前端 work 页弹窗让用户选择「重新生成」或「退款」。
- 提交后响应丢失进入 `needs_review`，不自动重复计费提交。

## 4. 模板目录与热度

模板存储在 `phase2_templates` 的 `payload_json`（含 `managed_runtime` 快照）。

热度 = 运营设置的 `heat_base`（展示基数）+ `phase2_template_selections` 真实选择次数。

`GET /api/v2/templates` 先按热度从高到低排序，同热度再用稳定随机（seed 派生）做次要排序；因此用户进入小程序默认播放的是当前最热模板，往后滑动热度递减。

「换一批」只会在同热度的一批里换顺序（热度排序优先）。

## 5. 参考图与资产映射

### 5.1 角色绑定（定妆照）

- 模板元数据 `module_1.character_settings` 提供 `character_id`、`display_name`、`person_uid`。
- 用户照片按 `character_id` 绑定，不按数组下标；一人体验时未上传角色使用模板 `actor_url`（source=template）。
- `role_mapping` 字段：`role_index`、`character_id`、`display_name`、`source`、`three_view_url`、`costume_url`、`face_index`、`three_view_index`，以及 `user_face_url` / `template_face_url`。

### 5.2 图片 N 映射（提交 Seedream / Seedance）

按火山官方规范使用「图片1、图片2……图片n」。

定妆照参考顺序：每个角色的「人脸」依次为图片1..N，每个角色的「三视图」依次为图片N+1..2N，最后为场景图。

视频参考顺序（`common/video_references.py` 的 `build_references`）：

1. 用户自拍照（face）
2. 用户三视图（three_view）
3. 模板人物整体图（template_character，仅一人体验时出现）
4. 模板分镜图（storyboard，`10-` 文件）
5. 模板场景图（scene）

每条记录带 `index`（即图片N）、`kind`、`character_id`、`url`。注册进资产库后 URL 换成 `asset://<asset_id>` 再提交视频 API。

### 5.3 资产库 asset_id

- 通过 Seedance `CreateAsset` 注册，返回 `asset_id`。
- 视频任务体用 `asset://<asset_id>` 引用，与「图片N」一一对应。
- `reference_plan` 持久化在任务里，重试不重复注册。

## 6. 模板素材规范

每个模板文件夹/ZIP 必须包含：

- `1-基础元数据.txt`：`module_1`（video_name、actor_count、character_settings）、`module_2`（video_genre、relationship_tags、video_specification）、`recommendation_persona`。
- `3.*` 角色设定图、`9.*` 角色服装图（按 `-<character_id>` 结尾匹配）。
- `2-` 或 `2.1-` 场景图、`10-` 分镜图、`6-` 封面、`7-` 成片案例、`8-` 配乐。
- `5-分镜提示词.txt`（视频动作提示词，groups 仅一组）、`11-定妆照提示词.txt`。

导入时生成 `managed_runtime` 快照（含 storyboard_url），生成阶段使用快照，源文件与快照分离，保证模板资产不可变。

## 7. 数据库

共享库：`aitravel_phase2_test`（data 节点 MySQL，内网 13306，utf8mb4）。

### 7.1 模板目录

`phase2_templates`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK AUTO_INCREMENT | 自增主键 |
| template_code | VARCHAR(128) UNIQUE | 模板编号（P2T-/P2B-） |
| location_association | VARCHAR(128) | 景区 |
| video_genre | VARCHAR(128) | 视频类型 |
| actor_count | INT | 角色人数 |
| status | INT | 1 上架 / 0 下架 |
| sort_order | INT | 手动排序 |
| payload_json | LONGTEXT | 模板完整 JSON（含 managed_runtime、asset_revision、cover、example） |
| identity_hash | VARCHAR(64) | 景区+名称去重哈希 |
| created_at / updated_at | TIMESTAMP | 时间 |

`phase2_template_settings`

| 字段 | 类型 | 说明 |
|------|------|------|
| template_code | VARCHAR(128) PK | 模板编号 |
| heat_base | BIGINT | 展示热度基数 |
| show_heat | INT | 是否展示热度 |
| revision | INT | 乐观锁版本 |

`phase2_template_selections`（真实选择，按用户+模板去重）

| 字段 | 类型 | 说明 |
|------|------|------|
| template_code | VARCHAR(128) | 模板编号 |
| owner_hash | VARCHAR(64) | 用户 openid 哈希 |
| created | DOUBLE | 选择时间 |
| — | PK(template_code, owner_hash) | 联合主键 |

### 7.2 项目与订单（MySQLStore）

`phase2_projects`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(64) PK | project_id（p2_...） |
| owner_id | VARCHAR(128) | wx_openid |
| status | VARCHAR(32) | 状态机 |
| runnable | TINYINT | 是否可调度 |
| due | DOUBLE | 下次调度时间 |
| paid_at | DOUBLE | 支付时间 |
| order_no | VARCHAR(64) UNIQUE | 订单号 |
| payload | LONGTEXT | 项目完整 JSON |

`payload` 关键字段：project_id、wx_openid、template_code、template、template_version、runtime_code、roles[]、status、stage、attempt、participation_mode、selected_person_uid、order_no、payment_mode、payment_status、amount_fen、paid_at、portrait_url、portrait_asset_id、portrait_version、video_url、video_task_id、character_task_id、role_mapping[]、runtime_snapshot、progress、error、created_at、completed_at、next_run_at、last_tick_at。

`phase2_orders`

| 字段 | 类型 | 说明 |
|------|------|------|
| order_no | VARCHAR(64) PK | 订单号 |
| project_id | VARCHAR(64) UNIQUE | 项目 id |
| owner_id | VARCHAR(128) | wx_openid |
| amount | INT | 金额（分） |
| payment_mode | VARCHAR(24) | dev-bypass / wechat |
| paid_at | DOUBLE | 支付时间 |

`phase2_roles`

| 字段 | 类型 | 说明 |
|------|------|------|
| project_id / role_id | VARCHAR(64/128) | PK(project_id, role_id) |
| source | VARCHAR(24) | user_face / template |
| photo_asset_id | VARCHAR(64) | 照片资产 id |
| check_status | VARCHAR(24) | 人脸检查状态 |
| payload | TEXT | 角色 JSON |

`phase2_assets`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(64) PK | 资产 id |
| project_id | VARCHAR(64) | 项目 id |
| kind | VARCHAR(24) | upload / portrait / video |
| reference | TEXT | 资产引用 |

`phase2_events`（进度与未读）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(100) PK | 事件 id |
| project_id | VARCHAR(64) | 项目 id |
| kind | VARCHAR(32) | 事件类型 |
| title | TEXT | 事件文案 |
| created | DOUBLE | 时间 |
| seen | INT | 是否已读 |

`phase2_leases`（调度租约）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(64) PK | 项目 id |
| owner | VARCHAR(64) | 持有者 |
| expires | DOUBLE | 到期时间 |

`phase2_migrations`：name / applied_at。

### 7.3 durable 任务（TaskJournal）

`phase2_tasks`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(64) PK | task_id（kind+run_id 哈希） |
| kind | VARCHAR(24) | image / character / video |
| run_id | VARCHAR(128) | wx_openid |
| status | VARCHAR(32) | pending/running/recovering/failed/completed/needs_review |
| next_run | DOUBLE | 下次调度时间 |
| payload | LONGTEXT | 任务 JSON |
| — | UNIQUE(kind, run_id) | 同用户同类型唯一 |

`payload` 关键字段：task_id、type、wx_openid、template_code、status、steps{step:{status,result,started_at}}、attempt、error、result、role_mapping[]、reference_plan[]、generation_parameters、created_at、next_run、cloud_failed。

`phase2_task_events`（任务状态历史）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(32) PK | 事件 id |
| task_id | VARCHAR(64) | 任务 id |
| kind | VARCHAR(24) | 任务类型 |
| status | VARCHAR(32) | 状态 |
| message | TEXT | 错误/说明 |
| created | DOUBLE | 时间 |

### 7.4 运营与日志

`phase2_admin_audit`：id、actor、action、target、detail(TEXT)、created。

`phase2_admin_attempts`：id、source（登录来源哈希）、created（限流用）。

`phase2_service_logs`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK AUTO_INCREMENT | 自增 |
| service | VARCHAR(64) | 服务名（phase2-api/seedream/video/...） |
| category | VARCHAR(32) | 类别（login/payment/image/...） |
| level | VARCHAR(16) | info/warning/error |
| message | TEXT | 日志内容 |
| context | TEXT | 上下文（prompt/URL/asset 等） |
| created | DOUBLE | 时间 |

### 7.5 媒体对象

`phase2_media_objects`

| 字段 | 类型 | 说明 |
|------|------|------|
| id / kind | VARCHAR(64/24) | PK(id, kind) |
| bucket | VARCHAR(128) | TOS 桶 |
| object_key | VARCHAR(512) | 对象键 |
| mime | VARCHAR(64) | 类型 |
| size_bytes | BIGINT | 大小 |
| width / height | INT | 尺寸 |
| sha256 | VARCHAR(64) | 哈希 |
| created_at | DOUBLE | 时间 |

### 7.6 其他

`phase2_schema_versions`：name / applied_at（结构版本标记）。

## 8. 错误处理

`common/ark_errors.py` 把上游错误统一映射成对用户友好、不含模型名的中文文案，并区分「可重试 / 已确认失败」。

覆盖：鉴权失败、权限不足、模型不可用、额度/余额不足、限流、素材过大、内容审核不通过、参数不合法、5xx/网络故障。

图片/视频/资产库/人脸 API 的原始错误详情只进服务器日志，不返回前端、不暴露模型名与密钥。

## 9. 日志体系

所有节点把结构化日志异步上报到 main 节点的 `logcollector`（内网 18031）。

类别：`login / payment / image / video / asset / tos / file / face / orchestration / admin / system`。

落盘位置（main 节点宿主机路径）：

```text
/opt/guizhilv-phase2/logs/
  <category>/YYYY-MM-DD.log
```

同时写 `phase2_service_logs` 供后台检索，默认保留 7 天（`PHASE2_LOG_RETENTION_SECONDS` 可调）。

日志内容：

- 登录、支付、下单、提交云端、生成成功/失败等业务事件。
- 生图/视频/资产库/人脸的完整请求与响应摘要：完整 prompt、参考图 URL、asset 引用、转存后的最终 URL。
- 不含 API 密钥、不暴露模型名、不打印原始上游报文。

查看方式：

- 运营后台：`https://api.guizhilv.juxiaobai.com.cn/admin/` → 服务日志。
- 服务器实时：`tail -f /opt/guizhilv-phase2/logs/*/*.log`。
- 容器原始日志：`docker logs <容器名>`。

## 10. 支付

`server/phase2/wechat_pay.py` 实现微信支付 V3 JSAPI；测试环境默认 `PHASE2_PAYMENT_MODE=dev-bypass`（跳过扣款直接进入生成）。正式接入时在 `.env.main` 配置 `WECHAT_PAY_*` 并切换模式。

## 11. 前端

页面：

- `pages/phase2/entry`：抖音式模板视频流，竖屏上下滑，按热度排序，当前项自动播放。
- `pages/phase2/upload`：角色上传、一人/多人体验切换、授权与下单。
- `pages/phase2/work`：制作进度、定妆照/视频、保存/分享、失败重试/退款。
- `pages/phase2/works`：我的作品列表，每个资产「保存/分享」上下堆叠。
- `pages/phase2/share`：被分享人的公开只读页。

关键工具：

- `utils/phase2Api.js`：接口封装。
- `utils/phase2Ui.js`：安全区/胶囊高度、保存媒体、视图映射。
- `utils/phase2Session.js`：微信登录会话。
- `utils/request.js`：统一请求（15 秒超时、鉴权头）。
- `state/phase2State.js`：浏览种子、草稿、会话本地缓存。
- `styles/phase2-v2.wxss`：全局主题与设计 token。

自适应规则：

- 宽度用 rpx（750rpx = 屏宽）。
- 高度用 flex 让视频区吃满剩余空间；抖音式首页视频铺满整屏，控件浮于其上。
- 刘海/底栏用 `env(safe-area-inset-*)`。
- 特别矮/长的屏幕用 `@media (max/min-height)` 微调。
- 设计 token 用 CSS 变量（`--r-*` 圆角、`--f-*` 字号、`--gold`、`--line` 等）。

首页 swiper 行为（微信原生组件限制下的折中）：

- swiper 不绑定 `current` 做受控滚动，滑动时只更新数据下标，避免真机方向卡死。
- 当前项渲染一个 `<video>`，其余项只渲染封面图；切到新项时新视频挂载并自动播放（微信原生 video 限制，新视频有拉流缓冲）。
- iOS 上自动带声播放可能被系统拦截，会显示中心播放键让用户点一下出声。

## 12. 分享

- 分享小程序：首页「分享」按钮 + 右上角菜单，分享当前模板。
- 分享作品：定妆照生成即可分享（不等视频），视频完成后可分享视频。
- 分享卡片用后端实时生成的 5:4 缩略图（`/api/v2/share-image/<pid>.jpg`），避免微信显示 Logo。
- 公开分享页 `/api/v2/share/<pid>` 只返回已完成/有产物的最小字段，媒体签名有效期 30 天，与 TOS 保留期一致。
- 隐私提醒：分享页知道 pid 即可查看，属于公开分享；如需可加「允许分享」开关。

## 13. 并发与容量

针对日活约 2000、云生图并发 500、视频并发 25 的定位：

- `PHASE2_ORCHESTRATOR_WORKERS=16`
- `PHASE2_GENERATION_WORKERS=16`（charactergen）/ `8`（video）
- `PHASE2_VIDEO_MAX_INFLIGHT=25`（本地对视频付费提交的并发上限，对齐云侧 25）
- `SERVICE_THREADS=32`
- MySQL 连接池 20 + 溢出 20

## 14. 数据隔离

- 所有 `/api/v2/projects/<pid>` 接口在 auth 层校验 `wx_openid` 归属。
- 作品列表、批量状态、已读、上传、下单、重试、退款均按 owner 过滤。
- durable 任务 id 由 `kind + wx_openid` 派生，重试校验归属。
- 媒体文件需服务端签发的路径令牌访问。
- 唯一公开的是分享页（按设计公开），其余无跨用户串数据/串任务。

## 15. 部署

本地打包：

```powershell
cd G:\桂智旅\aitravel\aitravel
python .\infra\staging\package_release.py
```

生成 `dist/phase2-main.tar.gz`、`phase2-ai.tar.gz`、`phase2-data.tar.gz`、`phase2-client.zip`。

一键部署三台（上传 + 解压 + 重建重启，data → main → ai）：

```powershell
.\deploy_all.ps1
```

仅重传某台：

```powershell
.\deploy_all.ps1 -SkipPackage -Only main   # main/ai/data
```

首次配置 SSH 免密（每台输一次密码，之后不再输）：

```powershell
.\setup_ssh_keys.ps1
```

每个节点在服务器上通过 `sudo python3 infra/staging/start_node.py <node>` 启动，校验 `.env.<node>` 后 `docker compose up -d --build --wait`。

健康检查：

```powershell
curl https://api.guizhilv.juxiaobai.com.cn/api/v2/health
```

返回 200 且 `environment=staging` 即正常。

## 16. 常用运维命令

模板回填分镜图（data 节点，旧模板一次性修复）：

```bash
cd /opt/guizhilv-phase2
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml exec -T admin python /app/server/admin/backfill_storyboard.py
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml exec -T admin python /app/server/admin/backfill_storyboard.py --commit
```

看日志：

```bash
tail -f /opt/guizhilv-phase2/logs/*/*.log
docker logs -f --tail 100 guizhilv-phase2-test-ai-video-1
```

日志采集链路自测（main 节点）：

```bash
docker exec -i guizhilv-phase2-test-main-phase2-api-1 python -c "from common import service_log; service_log.install('manual'); service_log.event('system','ping'); import time; time.sleep(3)"
ls -la /opt/guizhilv-phase2/logs/system
```

## 17. 接口列表

### 17.1 主节点业务接口（phase2-api，网关 `/api/v2/`，用户 JWT 鉴权，公开接口单独标注）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | /api/v2/auth/login | 公开 | 微信 code 换登录态 |
| GET | /api/v2/config | 公开 | 环境配置（支付模式、授权文本等） |
| GET | /api/v2/health | 公开 | 健康检查 |
| GET | /api/v2/media-ready | 公开 | 媒体安装校验 |
| GET | /api/v2/scenes | 公开 | 景区列表 |
| GET | /api/v2/templates | 公开 | 模板列表（热度排序，支持 scene/actor_count/relationship 筛选与分页） |
| GET | /api/v2/templates/<code> | 公开 | 模板详情 |
| GET | /api/v2/templates/<code>/runtime | 内部 | 模板运行快照（local-phase2） |
| POST | /api/v2/projects | 用户 | 创建草稿 |
| GET | /api/v2/projects/<pid> | 用户 | 项目详情 |
| GET | /api/v2/projects/<pid>/status | 用户 | 项目状态 |
| POST | /api/v2/projects/<pid>/participation | 用户 | 拍摄模式/角色选择 |
| POST | /api/v2/projects/<pid>/assets | 用户 | 上传角色照片 |
| GET | /api/v2/assets/<aid> | 媒体 | 读取上传照片 |
| POST | /api/v2/projects/<pid>/order | 用户 | 创建订单 |
| POST | /api/v2/projects/<pid>/prepay | 用户 | 微信预下单 |
| POST | /api/v2/payments/notify | 公开 | 微信支付回调 |
| POST | /api/v2/payments/callback | 公开 | 预留（501） |
| GET/POST | /api/v2/payments/allinpay/notify | 公开 | 通联支付回调（预留） |
| POST | /api/v2/projects/<pid>/retry | 用户 | 重新生成 |
| POST | /api/v2/projects/<pid>/refund | 用户 | 退款/取消 |
| GET | /api/v2/works | 用户 | 作品列表 |
| POST | /api/v2/works/status | 用户 | 批量作品状态 |
| POST | /api/v2/projects/<pid>/read | 用户 | 标记已读 |
| GET | /api/v2/messages | 用户 | 未读消息 |
| GET | /api/v2/results/<result_id> | 媒体 | 视频读取（302 到 TOS 签名） |
| GET | /api/v2/portraits/<result_id> | 媒体 | 定妆照读取（302 到 TOS 签名） |
| GET | /api/v2/share/<pid> | 公开 | 公开分享页数据 |
| GET | /api/v2/share-image/<pid>.jpg | 公开 | 分享 5:4 缩略图 |
| GET | /assets/<path> | 公开 | 模板静态素材 |

### 17.2 AI 节点

charactergen（local-phase2 内部 JWT）：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/image/generatecharacter | 提交角色/定妆照任务 |
| GET | /api/v1/image/task/<task_id> | 查询任务状态 |
| GET | /api/v1/image/task/by-user | 按 wx_openid 查最近任务 |
| POST | /api/v1/image/task/<task_id>/retry | 重试 |
| GET | /api/v1/image/health | 健康检查 |

video（local-phase2 内部 JWT）：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/video/generate | 提交视频任务 |
| GET | /api/v1/video/task/<task_id> | 查询任务状态 |
| GET | /api/v1/video/task/by-user | 按 wx_openid 查最近任务 |
| POST | /api/v1/video/task/<task_id>/retry | 重试 |
| GET | /api/v1/health | 健康检查 |

seedream（phase2-image 内部 JWT）：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/images/generate | 生图 |
| GET | /api/v1/images/health | 健康检查 |

personattr（local-phase2 内部 JWT）：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/personattr/detect | 人脸检测 |
| GET | /api/v1/personattr/health | 健康检查 |

### 17.3 data 节点

file（phase2-file 内部 JWT）：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/file/upload | 上传文件 |
| GET | /api/v1/file/download/<wx_openid>/<filename> | 下载文件 |
| GET | /api/v1/file/health | 健康检查 |

tos（local-video-storage / local-phase2-storage 内部 JWT）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /health | 健康检查 |
| POST | /internal/results/<result_id> | 上传视频到 TOS |
| GET | /internal/results/<result_id>/url | 取视频签名 URL |
| POST | /internal/portraits/<result_id> | 上传定妆照到 TOS |
| GET | /internal/portraits/<result_id>/url | 取定妆照签名 URL |

admin（管理员 JWT + CSRF）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /health | 健康检查 |
| GET | /admin/ | 管理台页面 |
| GET | /admin/static/<path> | 管理台静态资源 |
| POST | /admin/api/login | 登录 |
| GET | /admin/api/session | 当前会话 |
| POST | /admin/api/logout | 退出 |
| GET | /admin/api/templates | 模板列表 |
| POST | /admin/api/templates/upload | 上传模板 |
| PATCH | /admin/api/templates/<code> | 调整上架/热度 |
| GET | /media-assets/<revision>/<filename> | 模板素材读取 |
| GET | /admin/api/tasks | 制作任务列表 |
| GET | /admin/api/logs | 操作/进度/任务日志 |
| GET | /admin/api/service-logs | 集中式服务日志 |
| GET | /admin/api/overview | 数据总览 |
| GET | /admin/api/monitor | 服务连通监控 |

### 17.4 日志采集（logcollector，内部 JWT）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/logs/ingest | 接收日志批次 |
| GET | /health | 健康检查 |
