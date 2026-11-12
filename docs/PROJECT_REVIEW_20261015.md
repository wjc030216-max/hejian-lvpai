桂智旅二期项目审查报告（2026-10-15）

审查基线：内层仓库 G:/桂智旅/aitravel/aitravel，分支 github-main，提交 26dfdac6f136715ec767ca1fb71e11ddc2042372。审查开始时该仓库工作树干净。外层 G:/桂智旅/aitravel 还有独立 Git 仓库，其旧文件删除状态不等于二期项目文件丢失。建议后续明确唯一仓库入口。

本次完成核心业务、五个小程序页面、管理台、支付适配、持久任务、媒体存储、部署与测试的代码审查，并做离线测试和缺陷复现。未访问线上服务器、真实支付账户或云生成接口；未验证云控制台生命周期、真实并发容量与微信真机表现。除本报告及验证材料外，没有修改业务代码。以下区分：已复现、代码可确认、待环境验证、产品取舍。

**一、总体判断**

项目已经具备较完整的测试环境业务链路，尤其是付费生成的请求先落库、任务恢复、角色映射、运行快照和 TOS 私有存储，基础方向合理。但目前不能据此认定已经具备正式收费运营的完整闭环：发布路径已经失效；微信支付成功通知存在数据契约错误；退款只记录申请；四真人模式与参考图上限冲突；媒体生命周期、失败通知与运营处置还有缺口。

优先解决交易和交付问题，其次补足隐私、成本与恢复能力，再优化容量和体验。现阶段继续增加微服务、重写全部前端、把轮询全面替换成 WebSocket，都不是最优先事项。

**二、系统如何运作**

| 层次 | 当前实现 | 评价 |
|---|---|---|
| 小程序 | 原生 JS/WXML/WXSS，entry、upload、work、works、share 五页 | 链路短，开发成本低；页面状态与异步请求需要补回归 |
| 业务入口 | Flask phase2-api，微信身份、模板、项目、订单、分享 | 业务集中且易追踪；单文件包含过多不同职责 |
| 编排 | Phase2Orchestrator，照片检查池与制作调度池 | 把照片检查与生成分开是合理的；付款等待与通知需要独立调度 |
| 图片生成 | charactergen → seedream → file | 三视图和定妆照按步骤持久化，已完成步骤可复用 |
| 视频生成 | 参考图注册 → Seedance → 本地合成 → TOS | 付费提交与可重试后处理有分离；参考图数量缺少前置校验 |
| 数据 | MySQL 权威状态，SQLite 本地模式，Redis 辅助 | 不依赖 Redis 保存唯一任务状态是优点；SQLite 测试不能替代 MySQL 并发验证 |
| 管理 | 模板导入、热度、任务与日志查看、监控 | 已有独立登录与 CSRF；订单、退款、待核实处置闭环不足 |
| 部署 | main / ai / data 三节点，Docker Compose + Nginx | 职责隔离清楚，但不是高可用部署，数据库与入口仍各有单点 |

正常业务是：选模板 → 建项目草稿 → 上传并授权照片 → 人脸检查 → 创建订单 → 服务端确认付款 → 逐角色生成三视图 → 生成并保存定妆照 → 注册视频参考素材 → 生成视频 → 合成背景音乐并存储 → 作品完成 → 发订阅消息。

数据分三层：项目记录面向用户体验；character/video/image 任务面向后台执行；steps 检查点面向具体外部操作。角色按 character_id 绑定、项目携带 runtime_snapshot，是减少错脸和模板更新影响在途订单的重要设计，应继续保留。

开发绕过支付仍可能调用收费 AI。开发订单未向游客扣款，不代表平台没有生成成本。

**三、优先修复项**

优先级定义：P1 = 对发布、收款、交付或用户权益有直接影响，应在收费上线前解决；P2 = 功能可靠性、运维与规模风险；P3 = 日常体验和工程维护。不将仅凭静态推断的风险当作线上事故。

**A01｜P1｜当前发布脚本会因文档路径失效而中断——已复现**

最新提交把历史文档移到 docs/archive，但 package_release.py 的 common 列表仍要求根目录的 DEPLOY_THREE_SERVERS.md、DEPLOY_COMMANDS.md、PHASE2_DEPLOYMENT_RESULT.md、DNS_TEST_SWITCH.md、BATCH_TEMPLATE_IMPORT.md，collect() 遇到不存在的路径直接报错。五个旧路径均不存在；直接调用相同收集函数已得到 FileNotFoundError。deploy_all.ps1 默认先打包，因此会在部署前失败。

建议从发布清单中去掉非运行必需的历史文档，或改成实际路径；在 CI 中验证所有清单路径、从干净 checkout 打包并检查产物。测试模板目录被 Git 忽略，Dockerfile-main 又 COPY 该目录，也需定义可重复获取的版本化素材来源。

证据：[infra/staging/package_release.py](G:/桂智旅/aitravel/aitravel/infra/staging/package_release.py:22)、[infra/staging/package_release.py](G:/桂智旅/aitravel/aitravel/infra/staging/package_release.py:35)、[server/staging/Dockerfile-main](G:/桂智旅/aitravel/aitravel/server/staging/Dockerfile-main:8)。

**A02｜P1｜微信成功支付回调被确认接收，但订单不进入制作——已复现**

WeChatPay.parse_notify 验签并解密后只返回交易资源，丢弃外层 event_type；业务入口随后检查返回对象中的 event_type 是否为 TRANSACTION.SUCCESS。正常通知的事件类型在外层，交易资源使用 trade_state。结果是：返回 SUCCESS/200，但订单仍 pending_payment/unpaid。

复现使用本地临时 RSA 密钥和 AES-GCM 密文构造真实结构的通知，无真实付款。得到 HTTP 200、“已收到”，订单仍未支付。现有 test_parse_notify_roundtrip 把 event_type 同时放入了交易明文，掩盖了这个问题。

建议明确“通知外层 + 交易资源”的解析契约；核验 trade_state、appid、mchid、币种、金额和订单；成功入账与状态变更放入同一事务。增加使用官方结构的端到端回调测试、重复回调测试和拒绝不匹配商户测试。

证据：[server/phase2/wechat_pay.py](G:/桂智旅/aitravel/aitravel/server/phase2/wechat_pay.py:185)、[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:683)、[server/local/test_wechat_pay.py](G:/桂智旅/aitravel/aitravel/server/local/test_wechat_pay.py:100)。

**A03｜P1｜退款不是完成的资金闭环——代码确认及接口复现**

真实付款订单的退款接口只写 refund_pending / pending，没有调用微信或通联退款接口，也未发现退款查询、退款通知或管理员确认到账流程。前端却提示“退款将原路退回”。接口返回 200，表示申请记录成功，不代表渠道受理或资金退回。

另外，已申请退款的同一 refund_pending 状态仍允许 retry，retry 会清空 refund_status 并恢复生成。目前尚无真实自动退款，所以不能认定已经发生“退款和制作同时执行”；但接入退款时必须先解决互斥，否则状态模型不安全。

建议独立 refund_requests 和幂等退款单号，明确 requested/submitting/processing/succeeded/failed；接入渠道查询与对账；退款处理中禁止重新生成。若决定暂用人工退款，必须有工单、操作审计、到账登记和明确处理时限。

证据：[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:713)、[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:735)、[client/pages/phase2/work/work.js](G:/桂智旅/aitravel/aitravel/client/pages/phase2/work/work.js:85)。

**A04｜P1｜四真人模板会在已完成生图后被视频阶段拒绝——已复现数量冲突，代码确认前置校验缺失**

导入和首页支持 1–4 人；视频参考策略为每名真人 2 张（自拍+三视图），再加分镜 1 张和场景 1 张。四真人需要 10 张，而 build_references 的上限是 9。下单校验只验证角色与素材，并不验证这个预算。四真人参考计划已复现 ValueError。

一人扮演四角色模板需要的张数不同，不应一刀切删除四角色模板。通用计算是“模板总角色数 + 真人角色数 + 2”，当前需不超过 9。

建议在模板发布、选择参与模式和下单前执行同一个参考图预算校验；短期关闭四真人模式，长期再评估减少参考图、拼图或更换已确认支持的模型能力。不要先收款并生成完定妆照才拒绝。

证据：[server/admin/importer.py](G:/桂智旅/aitravel/aitravel/server/admin/importer.py:86)、[server/phase2/participation.py](G:/桂智旅/aitravel/aitravel/server/phase2/participation.py:18)、[server/common/video_references.py](G:/桂智旅/aitravel/aitravel/server/common/video_references.py:14)。

**A05｜P1（隐私策略）｜“保留/分享 30 天”目前不是应用层有效期限——已复现**

分享接口只要项目存在并已有产物就公开返回，没有 share_enabled、固定 share_expires_at 或撤回状态；每次请求重新签发 30 天媒体令牌。90 天前完成的项目仍能获得一个新的 30 天令牌。缩略图也是公开路径。

file 服务下载路由免鉴权；网关以安装级路径转发。使用已知完整文件链接即可匿名读取，已在临时文件上验证 HTTP 200。这不等于能任意枚举所有用户文件，但它属于“持有链接即可访问”，且无每文件到期与撤销机制。安装级路径也出现在公开模板媒体链接中，不能视作每位用户的访问授权。

仓库维护脚本清理的是模板素材和部分日志，没有看到自拍、生成中间图、云素材库和作品的一致生命周期删除链路。是否另设 TOS 控制台生命周期，未核实，不能断言云对象永久保留。

建议默认私有、主动开启分享、独立随机 share_id、固定到期时间、可撤回；统一原图/中间图/成片/缩略图/云资产/备份的保留政策；应用判断到期与存储生命周期配合，避免只删除对象却仍展示可用作品。

证据：[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:219)、[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:259)、[server/file/file_app.py](G:/桂智旅/aitravel/aitravel/server/file/file_app.py:11)、[infra/staging/nginx.staging.conf.template](G:/桂智旅/aitravel/aitravel/infra/staging/nginx.staging.conf.template:51)、[server/admin/maintenance.py](G:/桂智旅/aitravel/aitravel/server/admin/maintenance.py:1)。

**A06｜P1/P2｜微信支付状态与账务字段仍有第二层问题——已隔离复现**

在绕过 A02 的解析缺陷、直接进入成功分支后，amount_paid 仍保持初始值 0；MySQL 订单投影取该字段，后续收入统计与对账将失真。通联分支正确写入了实付金额。

当回调在预下单请求返回之前到达，微信 prepay 的后续 store.update 会把 paid 覆盖为 paying。已模拟该时序并复现。微信回调的“是否已支付”判断也在事务外，重复并发回调存在回退制作状态的风险。不要把所有这些风险归结为上游通知延迟。

建议两个支付渠道共用同一个事务化 mark_paid 业务方法；限定合法源状态、保持 paid 单向性、写完整实付与交易号，增加支付主动查单和超时关单。当前查单补偿缺失意味着不能把支付可靠性完全寄托于一次回调。

证据：[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:608)、[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:658)、[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:699)、[server/phase2/mysql_store.py](G:/桂智旅/aitravel/aitravel/server/phase2/mysql_store.py:106)。

**A07｜P2｜媒体链接过期后刷新仍沿用旧令牌——前端已复现**

work.refresh 为避免图片/视频重复加载，只要去掉 query 后路径相同，就把新 URL 替换成旧 URL；媒体签名恰好在 query 中。因此 mediaFailed 调用 refresh 也可能保留过期 access。隔离页面测试中后端返回 NEW，页面最终仍是 EXPIRED。作品接口的成片令牌当前为 24 小时，短期测试不容易遇到，长时间后台恢复时会暴露。

建议把资源身份和访问 URL 分开；普通轮询可减少无意义重渲染，过期、重新展示或读取失败时必须接受新令牌。保存前按需刷新链接。

证据：[client/pages/phase2/work/work.js](G:/桂智旅/aitravel/aitravel/client/pages/phase2/work/work.js:41)、[client/pages/phase2/work/work.js](G:/桂智旅/aitravel/aitravel/client/pages/phase2/work/work.js:143)、[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:436)。

**A08｜P2｜模板卡片选择与实际选中不一致——前端已复现**

点击“更多模板”里尚未出现在 feed 的模板，只重新拉该景区第一页 8 个。如果目标不在第一页，selected 留在新第一页的默认模板，用户随后点“拍摄同款”会创建另一款项目。分享深链也有类似限制，且历史浏览景区的优先级高于传入 scene，进一步影响定位。

后端已经有按 template_code 查询详情接口，可用它让目标模板进入当前列表或直接打开其详情；也可增加按 code 定位分页，但不必先新增接口才能修复。

证据：[client/pages/phase2/entry/entry.js](G:/桂智旅/aitravel/aitravel/client/pages/phase2/entry/entry.js:18)、[client/pages/phase2/entry/entry.js](G:/桂智旅/aitravel/aitravel/client/pages/phase2/entry/entry.js:158)、[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:353)。

**A09｜P2/P3｜筛选请求竞态与空状态按钮失效——前端已复现**

feed 的 fetch 有 sequence 防旧响应，但 fetchGrid 没有。先选 1 人再选 2 人，若前一个请求后到，界面保留“2 人”筛选却显示 1 人结果。空列表 WXML 绑定 clear，JS 没有此方法。

建议所有目录查询使用请求序号或过滤条件版本；失效请求不修改数据；补齐清除景区动作与 WXML 事件绑定检查。首页 feed 一直追加全部已翻页模板，长时间浏览还应采用滑动窗口与封面懒加载。

证据：[client/pages/phase2/entry/entry.js](G:/桂智旅/aitravel/aitravel/client/pages/phase2/entry/entry.js:82)、[client/pages/phase2/entry/entry.wxml](G:/桂智旅/aitravel/aitravel/client/pages/phase2/entry/entry.wxml:11)。

**A10｜P2｜完成通知失败后不会自动补发——已复现**

视频完成时先将项目标为 completed，再调用订阅消息发送；completed 项目不再进入 due 队列。发送失败或两步之间进程退出，作品已完成但通知没有第二次调度。隔离复现：发送返回 false，后续 tick 不再发送，due 项目数为 0。

建议通过事务 outbox 保存通知待办，由独立通知 worker 有界重试；识别用户拒绝、次数耗尽等不可重试错误。前端订阅模板 ID 硬编码、后端可配置但发送又使用当前全局模板，应统一从配置获取并保存授权对应版本。容器时区未显式设置，通知格式使用 time.localtime，也应明确按 Asia/Shanghai 格式化。

证据：[server/phase2/orchestrator.py](G:/桂智旅/aitravel/aitravel/server/phase2/orchestrator.py:216)、[server/phase2/orchestrator.py](G:/桂智旅/aitravel/aitravel/server/phase2/orchestrator.py:247)、[server/phase2/store.py](G:/桂智旅/aitravel/aitravel/server/phase2/store.py:80)、[server/phase2/wechat_subscribe.py](G:/桂智旅/aitravel/aitravel/server/phase2/wechat_subscribe.py:19)、[client/utils/phase2Api.js](G:/桂智旅/aitravel/aitravel/client/utils/phase2Api.js:98)。

**A11｜P2｜日志“仅保留本地”与实际代码不一致——代码确认**

service_log 的 logger 禁止向父 logger 传播，仅安装中央队列 handler；队列发送失败时已经取出的批次不重新入队，也没有写本地文件。提示“events kept local only”不代表这些业务事件真的落盘，不能把它当支付或退款审计底账。

logcollector 对数据库做 7 天删除，但按日期创建的本地文件没有跨日期清理；RotatingFileHandler 只限制某一天单组文件，长期磁盘用量仍可增加。请求日志还包含提示词、参考图完整 URL；异常文本可能包含带 token 的 URL，应统一脱敏。

建议本地结构化日志 + 可靠采集，或磁盘缓冲重试；关键金融审计独立事务保存；统一文件和数据库保留期，监控丢弃计数；删除 query 中访问令牌与不必要的人脸素材链接。

证据：[server/common/service_log.py](G:/桂智旅/aitravel/aitravel/server/common/service_log.py:32)、[server/common/service_log.py](G:/桂智旅/aitravel/aitravel/server/common/service_log.py:64)、[server/logcollector/logcollector_app.py](G:/桂智旅/aitravel/aitravel/server/logcollector/logcollector_app.py:54)、[server/common/seedream_provider.py](G:/桂智旅/aitravel/aitravel/server/common/seedream_provider.py:126)。

**A12｜P2｜管理员限流在反向代理后可能变成所有管理员共用额度——代码推导，需部署验证**

管理登录按 request.remote_addr 计数，10 次失败后限制 15 分钟；Nginx 管理代理没有设置可信客户端 IP 链，Flask 也未配置可信代理解析。实际接收地址通常是网关，因此一个来源失败可能影响所有管理员。登录限流的计数与插入还缺少原子限制，并发可突破。

建议在固定可信代理边界内处理真实来源，结合账号与来源维度限制，并在网关增加登录限流；不要直接无条件信任任意 X-Forwarded-For。

证据：[server/admin/admin_app.py](G:/桂智旅/aitravel/aitravel/server/admin/admin_app.py:89)、[infra/staging/nginx.staging.conf.template](G:/桂智旅/aitravel/aitravel/infra/staging/nginx.staging.conf.template:18)。

**A13｜P2｜默认任务并发与数据库连接预算不匹配——静态容量风险，未用 MySQL 压测确认**

TaskJournal 使用默认 SQLAlchemy 池；MySQL QueuePool 通常为 pool_size=5、max_overflow=10，最多 15 条。一个 worker 在 GET_LOCK 期间持有连接，get/put 又另取连接。charactergen 默认 16 个 worker，可能出现工作线程占着锁连接等待另一个连接，池耗尽后超时；seedream 同样存在长请求锁占用。全局 MySQL max_connections=100 还需覆盖所有服务连接池。

建议显式配置锁连接和业务连接预算、避免执行长云请求时耗尽业务池，或改为具备 fencing 的持久租约；针对“高并发 + 云端变慢 + 断连”做真实 MySQL 压测。不能只把 worker 数调大就视为扩容。

证据：[server/common/task_journal.py](G:/桂智旅/aitravel/aitravel/server/common/task_journal.py:27)、[server/common/task_journal.py](G:/桂智旅/aitravel/aitravel/server/common/task_journal.py:37)、[server/common/database_schema.py](G:/桂智旅/aitravel/aitravel/server/common/database_schema.py:11)、[infra/staging/docker-compose.ai.yml](G:/桂智旅/aitravel/aitravel/infra/staging/docker-compose.ai.yml:103)、[infra/staging/docker-compose.data.yml](G:/桂智旅/aitravel/aitravel/infra/staging/docker-compose.data.yml:37)。

**A14｜P2｜前置人脸检查和手动重试缺少业务成本上限——代码确认能力缺口**

网关仅对用户登录配置请求速率；未见按用户限制上传、人脸检测、在途项目或总重试次数。自动重试虽然每阶段最多 3 轮，但失败后用户可创建新的 run_id，再开始一套重试预算。dev-bypass 测试环境又明确可能启用真实云调用。

建议按用户/订单/时间窗口设上传与检测额度、并发项目数、全生命周期生成预算；对确定性模板错误不要自动重试；人工重试记录原因与成本。待核实任务长期占用视频并发名额时，要有处置时限与告警。

证据：[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:475)、[server/phase2/phase2_app.py](G:/桂智旅/aitravel/aitravel/server/phase2/phase2_app.py:713)、[server/common/task_journal.py](G:/桂智旅/aitravel/aitravel/server/common/task_journal.py:98)、[infra/staging/nginx.staging.conf.template](G:/桂智旅/aitravel/aitravel/infra/staging/nginx.staging.conf.template:4)。

**附加历史风险｜P1（若仍有效）｜外层旧仓库的 HEAD 包含支付私钥**

外层仓库 G:/桂智旅/aitravel 的已提交版本包含 server/pay/cert/apiclient_key.pem；只读检查确认存在 PRIVATE KEY 标记、长度 1704 字节。本次没有输出密钥内容。该文件属于外层旧仓库，不应误记为当前内层二期仓库新提交的泄漏；也未核实密钥是否有效、是否曾对外公开。

建议核查对应商户密钥的有效性与历史仓库访问范围；若仍被使用，应轮换并更新服务端配置。工作树删除、追加 .gitignore 都不能消除已提交副本。是否清理历史应另行制定方案，避免直接重写共享历史；本次未执行轮换或历史改写。

**四、产品与架构争议点，需要明确选择**

| 争议 | 当前事实 | 建议选择 |
|---|---|---|
| 人工热度能否显示为“人选择” | 新模板和替换版本自动设置 10000 基数；文案是“1.0万人选择”；真实选择在创建订单时记，不等于付款或成功作品 | 人工权重只用于排序；人数文案使用明确口径的真实计数；若保留运营指标，改称热度而非人数 |
| 作品是否默认可分享 | 只要知道 project_id 且已有产物即可访问公开分享，无独立开启操作 | 默认私有，用户主动分享后颁发可撤回的分享凭证 |
| 30 天如何计算 | 目前每次访问延长令牌有效期，缺少固定作品到期 | 明确从完成时间开始，区分原图、中间图、成片和分享链接期限 |
| 失败后是退款还是重做 | 两种按钮共用 refund_pending 状态；退款尚无资金执行 | 订单选择其一，退款处理中禁止重做；给出明确处理时限 |
| 保守防重复计费 vs 自动恢复速度 | 不确定提交进入 needs_review，不盲重发 | 保留保守机制，增加可审计的人工核实工作台和明确“已提交/确定未提交”处置 |
| 所有角色/参与模式是否同价 | 按全局价格收费；真人越多生图与检测成本越高 | 先测成本分布；可按人数分档，或统一定价但限制重试与最低毛利 |
| 三节点是否过度设计 | 隔离了凭证和 AI 重负载，但共享数据库与全局 JWT 仍有耦合 | 暂时保留，不再细拆；优先可观测性、连接预算、备份恢复与最小权限 |
| JSON payload 与投影表并存 | 写主状态同时更新订单/角色/资产投影 | 指定唯一权威源和重建方法；钱、状态、关键时间关系化，模型上下文与快照保留 JSON |
| SQLite 与 MySQL 两套实现 | 通过 SQL 字符串重写复用，事务和锁行为差异明显 | 本地轻量模式可保留；发布门禁必须运行 MySQL 合约测试，逐步替换字符串 SQL 改写 |
| 模板同名如何去重 | 手动导入偏场景+名称，浏览器批量按 ZIP 文件名；两者可重复且测试已失败 | 制定模板业务 ID、内容哈希、版本号；文件名仅作来源信息 |
| 替换模板是否自动上架/重置热度 | save_upload 会设 status=1、heat_base=10000 | 素材替换保留运营状态；发布与权重调整作为独立明确操作 |
| 人脸属性检测是否必要 | 先 DetectFace，再调用人体属性接口；编排器主要只用人数、质量和位置 | 评估去掉不使用的年龄/性别/眼镜识别，降低成本、时延与额外失败点 |
| 样片是否承诺等同效果 | 提示词强调高画质，但真实输出固定 15 秒、配置尺寸与模型效果决定结果 | 展示实际交付规格与合理预期；建立人脸一致性、构图和音轨质量抽检 |
| 什么时候升级实时推送 | 已有前台多处轮询和微信一次性通知 | 先统一轮询、做退避和增量状态接口；达到明确流量瓶颈后再引入推送 |

人工热度证据：[server/admin/store.py](G:/桂智旅/aitravel/aitravel/server/admin/store.py:62)、[server/common/management_schema.py](G:/桂智旅/aitravel/aitravel/server/common/management_schema.py:26)。这属于对外统计口径和信任问题，需要产品负责人明确决定，不应仅当作展示细节。

**五、性能、工程与运维的优化路线**

1. 目录查询：当前每次按全目录计算热度、分面和排序，缓存主要覆盖模板载荷。先度量模板数与 p95 延迟，再缓存分面/排序结果或数据库分页。seed 只固定同热度排序，热度变化时 offset 分页仍可能重复/遗漏；使用排序快照或游标，并前端去重。
2. 调度查询：pending_payment 仍被视作 runnable，调度读到后直接返回。将支付等待移出生成队列，并安排独立支付超时/查单任务，减少大量未支付订单带来的无效数据库读写。
3. 全局轮询：App 每 15 秒拉作品首页，作品页和列表页又分别刷新；全局只观察前 30 条。合并状态订阅，以活跃 project_id 批量查询，页面可见时再刷新，网络失败指数退避。
4. 拆分业务入口：把 auth、catalog、projects、payment、share 各自拆为 Blueprint/服务，统一请求校验和状态转换；无需在第一轮引入重型架构。
5. 数据迁移：避免启动时多处临时建表、字符串替换 SQLite SQL 与单个大版本迁移不断叠加；引入明确版本迁移与升级回滚演练。
6. 发布：构建不可变镜像并记录 commit、依赖锁、素材版本与 manifest；部署前做健康与业务冒烟，准备上一版回滚。当前远端原目录解包和现场构建不构成原子发布。
7. 备份：已有带校验和的冷备工具是优点，但依赖停机和人工跨节点协调。进一步定义 RPO/RTO、数据库备份与恢复演练、对象存储版本/生命周期、异地副本。备份不等于高可用。
8. 依赖：部署 Python 3.11，本机 Python 3.9.12；各节点 requirements 的约束不完全一致（例如 Pillow 范围/固定版本不同）。用统一可重建环境与锁定依赖验证发布，避免环境差异被误判为业务缺陷。
9. 权限：TOS 凭证隔离值得保留；各服务共享用户签名/内部调用密钥、共享数据库账号，节点失陷后权限边界较宽。分离用户 JWT 与服务间身份，按服务缩小数据库权限。
10. 生产配置：当前主动禁止 production 和 release 是测试保护，不能视作 bug。但正式上线不能只删掉禁止逻辑；LOCAL_REAL、内部鉴权生成、存储地址和前缀、Cookie secure、云调用开关、证书与微信模板等都有环境分支，要做整套生产配置矩阵。
11. 监控：应补“付费未启动”“待核实最老任务”“退款积压”“通知失败”“云成本/成功作品”“数据库池等待”等业务指标。health 返回 200 不等于云凭证、商户、余额和整条交付链正常。
12. 工程入口：保留一个明确 repo root，给出一条离线测试命令、一条 MySQL 测试命令和一条打包验证命令；目前未见 .github/workflows 门禁。

容量需以峰值到达率和每阶段平均/长尾耗时估算。视频并发上限 25 时，理想吞吐约为 25 × 3600 ÷ 单视频占用秒数，还未扣除限流、错误和其他瓶颈。仅凭“日均 2000 用户”和线程配置无法证明几分钟必定交付。

**六、验证结果与边界**

| 验证 | 结果 |
|---|---|
| Git 跟踪文件清单 | 198 个；核心为 79 个 Python 文件、20 个 JS/CJS 文件 |
| Python 语法解析 | 79/79 通过 |
| JavaScript 语法检查 | 20/20 通过 |
| 后端 unittest discovery | 122 个：96 通过，5 断言失败，6 错误，15 跳过 |
| 支付前端 Node 测试 | 5 个：4 通过，1 失败（测试环境缺 getApp mock） |
| frontend_flow 脚本 | 在缺 wx.showModal mock 处中断，后续检查未完成 |
| 额外缺陷复现 | 支付通知、实付字段、支付竞态、退款状态、90 天分享续签、匿名文件读取、四真人参考预算、通知不补发、发布路径、前端媒体/模板/筛选/事件绑定 |
| 线上、真机、真实 MySQL 并发、云费用 | 未验证 |

后端失败分类：

- 2 项模板管理：手动上传后同名批量导入未跳过；替换后未引用旧素材被删除，与旧测试预期不一致。前者是规则不统一；后者需先确定保留策略，不能直接判成数据丢失。
- 2 项目录断言：新增 gender 分面后旧测试仍要求只含三个维度，属于测试契约落后。
- 1 项人脸断言和 1 项存储错误：生产代码改用 http_client.session()，测试仍 patch requests.post，mock 失效。本次禁止网络连接，因此没有实际访问这些地址。
- 5 项错误：本机缺 alibabacloud_facebody20191230 SDK。
- 15 项跳过：未配置可丢弃的 MySQL 测试库。

测试证据限制：运行环境为 Python 3.9.12 / Node 22.14.0，不是 Docker 的 Python 3.11。因此这里的统计描述本机离线审查结果，不是生产镜像验收结论；96 项通过也不能证明不存在没有覆盖的支付或隐私问题。

测试应优先新增/修复真实风险的合约用例：合法微信通知真实 envelope、回调/预下单交错、重复回调、退款与重做互斥、四真人预检、固定分享有效期、通知 outbox、媒体 token 更新、快速切换筛选、干净 checkout 打包、MySQL 行锁与命名锁竞争。不要用大量只断言源码字符串的测试替代这些行为验证。

离线复现与测试计数另见 [验证材料](G:/桂智旅/aitravel/aitravel/docs/PROJECT_REVIEW_20261015_EVIDENCE.json)。

**七、建议实施顺序与验收标准**

| 阶段 | 工作 | 验收标准 |
|---|---|---|
| 第一批：发布与资金 | 修复打包、微信回调、支付原子入账、退款流程、四真人前置检查 | 从干净环境构建；合法回调落账且只启动一次；明确退款受理/到账；不支持的组合在支付前拦截 |
| 第二批：用户权益与交付 | 明确分享/留存、重试预算、通知补偿，修复选错模板与过期链接 | 能撤回分享且到期拒绝；重试有总预算；失败通知可补偿；点击、支付和生成的模板一致 |
| 第三批：容量与运营 | MySQL 并发压测、队列/连接预算、业务告警、管理员工单、可靠日志 | 峰值及上游慢响应下无池耗尽；超时任务和退款有人处理；关键事件可追溯 |
| 第四批：维护与体验 | 统一模型/状态契约、CI、目录查询优化、轮询整合、发布回滚 | 测试与代码同步；恢复演练可执行；有可量化延迟、成功率与成本指标 |

最值得保留的现有设计是：云提交前持久化、结果不确定不盲目重发、任务/步骤检查点、不可变运行快照、按角色 ID 映射、TOS 私有 ACL、管理台独立密钥与 CSRF、模板 ZIP 路径校验、上传图片解析和订单归属验证。改进应围绕这些基础补齐闭环。
