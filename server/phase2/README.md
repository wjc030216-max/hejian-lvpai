# 二期联调服务

当前业务：上下刷模板 → 按景区、人数、关系筛选 → 按角色上传照片／一人体验 → 人脸检查 → 通联支付 → 定妆照生成并保存 → 自动生成视频 → 保存作品；付费生成最终失败后由用户选择重新生成或申请全额退款。

最终定妆照不传给视频模型；定妆照和视频均采用 9:16。数据库结构由 `common/database_schema.py` 统一升级。模板统一由管理员后台上传，仓库和部署包不内置模板。

本地 Docker 由项目根目录的 `start-local.ps1` 启动，网关入口为 `http://127.0.0.1:8080`。

生产环境不要直接使用此服务的默认密码；部署测试服务器时必须通过环境变量设置独立数据库、Redis、文件目录和 `PUBLIC_BASE_URL`。订单状态由真实编排服务推进，二期服务不再提供手动模拟推进接口。

测试环境启用真实编排时增加：

```dotenv
PHASE2_AI_ORCHESTRATION_ENABLED=true
PHASE2_DEV_PAYMENT_BYPASS=true
CHARACTERGEN_SERVICE_URL=http://127.0.0.1:10005
PERSONATTR_SERVICE_URL=http://127.0.0.1:10002
PHASE2_INTERNAL_JWT=服务间调用使用的 JWT
SEEDDANCE_SERVICE_URL=http://127.0.0.1:10006
```

视频等待使用持久化任务号，每次只查询一次。角色生成使用 500 个并发 worker，Seedream 服务使用 500 个请求线程；视频使用 40 个并发 worker 和 40 个云端在途上限，模型为 `doubao-seedance-2-0-fast-260128`。不明结果的收费请求不会盲目重发。

小程序前台状态观察使用 `/api/v2/works/watch`，只返回状态和媒体布尔值，不签发媒体链接。最终定妆照和视频通过 `download=1` 同源回传，并支持 HTTP Range；管理台和保存流程不会依赖 TOS 跨域跳转。

通联 W06S 支付和用户申请的全额退款均已接入生产网关。

生产环境通过 `APP_ENV=production` 启用正式媒体、内部服务鉴权和 `phase2-prod/` TOS 命名空间。退款成功后除了应用层隐藏媒体，还会由后台删除 TOS 最终对象；退款处理和媒体删除都使用持久化状态与退避重试。

项目创建和角色照片上传均有基础频率限制，可通过 `PHASE2_PROJECTS_PER_HOUR`、`PHASE2_UPLOADS_PER_10_MINUTES` 调整。

用户照片、生成中间图、提示词、生成视频和分享统一保留 90 天。到期后媒体接口和分享立即失效，后台清理本地资料、文件服务中间图、AI 本地合成文件及 TOS 最终对象；支付订单、退款等财务记录保留。
