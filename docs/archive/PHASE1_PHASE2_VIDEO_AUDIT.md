# 一期与二期视频提交、数据库关联核查

核查日期：2026-10-01。范围：仓库初始提交 `4329fcf` 的一期源码、当前工作区二期源码、正在运行的本地 `aitravel-local-real` MySQL / Redis。没有连接测试或生产服务器，不能据此断言线上部署与仓库完全相同。本轮不修改业务代码，不提交真实云端生成任务。

结论：二期已接上原视频服务的素材注册、模型提交、轮询和成片存储流程，但用户、订单、项目、云任务和作品之间的持久关联还不完整，故障恢复也有明确缺口。之前通过的离线测试验证了部分接口与状态规则，不等于视频全链路真实验收。

## 1. 一期实际执行流程

1. 小程序生成并展示定妆照；创建支付订单时带 `character_task_id`，`payapi.create_order` 将其写入 MySQL `orders`。
2. 支付后进入分镜流程。分镜完成，`story.js` 的 `_executeAfterPayment` 直接请求 `/api/v1/video/generate`，一期原始请求只传 `wx_openid`、`storyboard_image`、`template_code`，不传订单编号或角色任务编号。
3. 视频服务通过 `video_user_task:{openid}` 查最新视频任务，Redis 锁防止普通重复生成。新任务先 INSERT MySQL `video_tasks`，再写 Redis `video_task:{task_id}`，然后投入进程内线程池，HTTP 返回 202。
4. 根据 `template_code` 调模板服务，拿 `config_json.groups[0].Prompt`、角色列表、服装图、场景图、BGM。用户照片通过 `user_task:character:{openid}` 指向的 Redis 任务读取。
5. 素材依次为：分镜图 → 每个角色的人脸图、模板服装图 → 场景图。人脸缺失时原实现会尝试模板默认角色图。
6. 每张图调用 `CreateAsset`，循环 `GetAsset` 到 Active。构造文本加 `asset://...` 的 `reference_image` 列表，调用 Ark `content_generation.tasks.create`。
7. 拿到云端任务编号后，在同一线程循环 `tasks.get`。完成后下载视频、合成 BGM、上传 TOS，更新 MySQL / Redis 的 `status/result/error/finished_at`。
8. 小程序 `create.js` 先按 openid 查最新任务，再轮询具体任务；完成时显示 `result`，前端另行触发赠送抽奖次数。

### 一期数据库的真实关联程度

```text
orders.character_task_id -> Redis task:{character_task_id}
orders.scene_task_id     -> 表中预留，但仓库未找到完整赋值回填逻辑
orders.video_task_id     -> 表中预留，但仓库未找到完整赋值回填逻辑

video_tasks.wx_openid    -> 用户 openid（应用层关联）
video_tasks.template_code -> 模板编码（应用层关联）
video_tasks.task_id      -> 本地业务任务号，不是 Ark 云任务号
```

一期 `video_tasks` 表没有 `order_id`，原视频请求也不携带订单号。全仓库检索 `scene_task_id/video_task_id` 的 Python、JavaScript、SQL，发现的是模型定义、返回字段及注释，未找到一期生成链路回写这两个订单字段的实现。不能把表注释“生成完成回填”当作已经实现。

基线证据：`git show 4329fcf:client/pages/story/story.js` 第 860 行；`server/pay/payapi.py` 第 459 行；`server/aitask/seeddance_video_app.py` 第 164、261、735、812、939、1178、1339 行；`server/init/v003_order.sql` 第 82 行。

## 2. 二期当前执行流程

```text
选择模板 -> 创建 project -> 上传角色原图
-> 本地模拟订单确认（非真实支付）
-> 人脸检测 -> 角色三视图 -> 竖屏定妆照保存到 TOS
-> portrait_ready，等待用户手动点击
-> POST /api/v2/projects/{id}/confirm-portrait
-> 编排器请求 /api/v1/video/generate
-> 注册人脸/三视图/场景素材 -> 提交 Seedance -> 轮询
-> 合成 BGM -> TOS 存储 -> video_tasks 完成
-> 编排器更新 Redis project -> 小程序作品页展示
```

二期向内部视频服务提交：

```json
{
  "wx_openid": "项目编号_本轮随机编号",
  "template_code": "所选模板编码",
  "character_task_id": "本轮角色生成任务编号"
}
```

这里的 `wx_openid` 实际装的是 `run_id`，不是游客真实 openid。这样能隔离一期按用户复用最新任务的逻辑，但不能再用这个字段作为真实用户归属。

视频模型收到：全部用户人脸 → 全部本轮三视图 → 模板场景图。单人 / 双人 / 三人分别 3 / 5 / 7 张。定妆照不传入视频模型，二期不生成分镜图。`character_id` 校验、明确的 `character_task_id` 关联、确认版本校验和普通重复点击防重已实现。

## 3. 对照结果

| 环节 | 一期基线 | 二期当前 |
| --- | --- | --- |
| 开始视频 | 分镜页点击提交 | 定妆照确认后由编排器提交 |
| 角色定位 | openid 最新任务 | 明确 character_task_id + 角色映射校验 |
| 造型参考 | 模板服装图 | 本轮生成的角色三视图 |
| 分镜 / 定妆照作为视频输入 | 传分镜图 | 两者都不传 |
| 用户归属 | video_tasks.wx_openid 为用户 | 字段被 run_id 占用，依赖 project 反查真实用户 |
| 订单 | MySQL orders，任务回填不完整 | 仅 Redis project.order_no，本地模拟支付 |
| 视频任务 | MySQL + Redis | 继续 MySQL + Redis，多了 character_task_id |
| 云端任务编号 | 线程变量 / 日志 | 仍未落库 |
| 素材库 asset_id | 线程变量 / 日志 | 仍未落库，每次执行重新注册 |
| 用户作品列表 | 按用户查任务 / 最新任务 | 扫描 Redis project，按用户筛选 |
| 完成权益 | 前端请求赠送接口 | project.lottery_granted 布尔标志，非完整权益账本 |
| 故障恢复 | 整个任务重跑 | 仍有整任务重跑及完成状态无法同步的问题 |

## 4. 已确认的问题及整改

### P1：业务关联没有持久落库

`phase2_app.py:311` 只写 Redis；`413` 的 order 只生成 `P2...` 字符串并标记 paid；`269` 的作品列表扫描 Redis，不读视频任务表恢复项目。MySQL 当前仅有 `characters`、`phase2_templates`、`video_tasks`，没有二期项目、订单、角色任务、用户资产表。

Redis 本地开启 AOF，完成定妆照 Hash 和本地 project 也已设置不过期，所以普通容器重启不等于数据立即丢失。但这仍不是数据库业务关联；Redis 数据丢失后，即使 TOS 留有成片或 MySQL 留有视频任务，也缺少完整用户/订单/项目关系。真实用户不能直接用旧的 `video/list?wx_openid=真实用户` 找到二期按 run_id 保存的视频。

整改：为项目、订单、角色快照、资产建立 MySQL 持久记录；任务显式保存 `project_id/order_id/user_id/run_id`，不要让 run_id 继续代替用户 ID。Redis 只作缓存、锁和调度协调。按数据库用户索引分页查作品。

### P1：云端任务号未落库，无法可靠续查

`seeddance_video_app.py:1132` 得到 `volcano_task_id` 后立即轮询，但 `save_task/update_task_status` 和实际表均没有保存它。`asset_ids`、云端输出地址也只保存在本轮执行变量中。线程或容器终止后，业务任务可能仍停在 running，却无法从持久记录查询原云任务。

整改：保存 `provider_task_id`、提交状态、提交尝试、素材 asset_id 与引用顺序、云端结果地址；优先续查已有任务。提交响应丢失属于结果不明，不能直接当作“未提交”再生成；使用提供方支持的幂等能力，若无法确认则进入待核对状态。

### P1：二期恢复把“已有任务”一律调用 retry

`orchestrator.py:135` 只要有 `video_task_id` 就调用 `/retry`；而 `seeddance_video_app.py:1571` 只允许 failed。编排器一次状态查询异常 / 超时就会标记项目 failed；重启恢复也会将制作中项目改 failed，未先核对下游任务。

因此，视频服务还在 running 或已经 completed 时，用户返回确认页再点生成，会收到 HTTP 400，项目无法自动接回已有结果。当前本地编排超时为 1800 秒，视频云轮询也是 1800 秒，前者还涵盖素材激活与存储，存在前者先超时的窗口。

整改：有 task_id 先 GET：completed 同步作品，pending/running 继续查询，failed 再按失败阶段重试。查询网络故障不等于生成失败。重启恢复从数据库和云任务状态接续。

### P1：视频存储失败会重新调用计费生成

`run_video_task` 将素材注册、云生成、配乐、存储放在同一个 try 中；任意一步异常都会将视频标为 failed。`retry_video_task` 重新运行整个函数，且没有读取现有云端结果或已合成文件的恢复分支。

即便 `save_local_video` 留下了已合成文件，下次重试仍先重新注册素材、调用模型，然后才存储。

整改：分开 `assets_preparing/video_generating/video_generated/composing/storing/completed` 阶段；云生成成功立即记录结果，配乐失败仅重做配乐，TOS 上传失败仅重试上传。只有明确的云端生成失败且用户确认时才能重新生视频。

### P1：二期业务规则绑定 APP_ENV=local-real，不能直接搬到测试环境

`seeddance_video_app.py:1373` 仅 local-real 允许不传分镜；其他环境仍强制要求 storyboard_image。`1012` 的素材构建也按环境判断是否加分镜；TOS 接入、HTTP 地址和角色任务持久化同样有本地专用分支。

离线复现：将 APP_ENV 设为 test，发送二期合法请求，返回 HTTP 400、未入队。不能靠在测试服务器伪装 local-real 来解决。

整改：将 `pipeline_version=2` / 独立二期视频入口与环境配置分开；开发、测试、生产都遵循二期的无分镜合同。环境只决定连接地址、账号、资源，不决定业务流程。

### P2：模板没有固化版本，延迟确认可能换了素材与提示词

`create_project` 保存模板编码和角色槽位，但没有完整模板版本及素材 / Prompt / BGM 快照。视频生成时 `fetch_template_prompt` 重新读取运行时模板；本地 runtime 还会读取模板目录内容。后续更新同编码模板可能导致视频与此前定妆照不一致。

整改：创建制作项目时固定模板版本和角色、场景、视频 Prompt、BGM、引用图号规则，重试沿用快照。定妆照角色生成和视频生成都使用同一版本。

### P2：延后确认依赖旧素材公网地址

原图及三视图映射保存了本地素材隧道的完整公网 URL。即使 Redis 不过期，临时隧道换域名后旧 URL 也可能不可访问 / 不再通过当前域名检查。定妆照已存 TOS 不代表视频所需的原图与三视图地址也已稳定。

整改：持久保存存储对象 ID / key，提交素材库前按当前环境生成有效可访问 URL；以内容及素材版本关联资产，不把临时域名作为永久资产身份。

### P2：普通防重不覆盖异常重试窗口

确认接口有 project 锁，generate 有 Redis 用户任务锁，这是已实现的保护。但 retry 接口没有同等锁或数据库条件更新；线程入队失败会留下 pending 任务；Redis 最新任务映射过期后 generate 不按数据库业务幂等键查询。源码还存在 `finished_at=None` 无法清空旧结束时间的问题。

整改：按 `project_id + stage + attempt` 建唯一约束，事务更新状态并记录待执行任务；worker 有持久队列和租约；retry 使用状态条件更新。明确“重试存储”与“新一次计费生成”的幂等键及授权。

## 5. 本地只读核验与离线复现

本地 MySQL 实际结果：

```text
tables: characters, phase2_templates, video_tasks
rows:   0,          3,                0
video_tasks columns:
task_id, wx_openid, storyboard_image, template_code, character_task_id,
status, result, error, created_at, finished_at, type
```

Redis DB2 有 7 个项目：4 个 uploading、3 个 portrait_ready；3 个完成定妆照的关联角色 Hash 在 DB3 均存在、TTL=-1，没有 video_task_id。本次时点没有本地视频数据库任务，因此不能声称真实视频全链路已经跑通。

无需云端调用的复现：抽取实际视频路由和 worker 函数，替换网络 / 数据库边界后执行。

```text
二期请求 + APP_ENV=test                    -> HTTP 400，未入队
视频状态 running 调 retry                 -> HTTP 400
视频状态 completed 调 retry               -> HTTP 400
编排恢复已有 task，retry 返回400           -> 未查询原任务，项目 failed
云视频成功、存储失败，再执行现有重试路径    -> 模型提交 2 次，素材创建 6 次（单人）
```

这些是离线模拟边界的实际代码执行结果，不是已经产生的真实重复计费。没有在核查时写业务数据库、重启服务或修改测试 / 生产资源。

## 6. 建议的二期关联与修复顺序

```text
user_id
  -> project_id + template_version
     -> order_id（支付状态与制作状态分开）
     -> character_task_id -> 持久角色原图 / 三视图映射
     -> portrait_asset_id（展示资产）
     -> 用户确认记录
     -> video_task_id（业务任务）
        -> provider_task_id（Seedance 云任务）
        -> 引用素材 asset_id 列表
        -> 云生成结果 -> 配乐结果 -> video_asset_id
     -> 作品记录 / 权益发放记录
```

1. 先补数据库持久关联、云端任务号与存储阶段检查点；保留本地模拟支付模式，但也写本地订单表，明确标记 simulated。
2. 修复“先查询再恢复”、分阶段重试、入队失败补偿及幂等，保证不会因存储或查询失败重做计费视频。
3. 固化模板和引用素材快照，把二期业务版本与环境变量分开，解决隧道更新后的素材访问。
4. 用离线故障注入验收：提交响应丢失、轮询超时、容器重启、上传失败、重复点击、Redis 缓存丢失、跨用户作品隔离。
5. 再做一条经授权的真实视频生成验收：核对同一个用户 / 项目 / 订单 / 角色任务 / 本地视频任务 / 云任务 / TOS 对象 / 作品全部能双向追踪，再部署到测试服务器。

本次只完成核查与整改设计，以上缺口尚未在本轮修复。
