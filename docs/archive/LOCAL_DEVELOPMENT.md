# 本地开发与测试服务器部署

> 当前工作区已切换为本地专用真实 API 开发。请优先执行 [LOCAL_REAL_API.md](LOCAL_REAL_API.md)。下面的 start-local.ps1 是旧的无 AI 联调模式，不会生成真实成片。测试服务器发布方案需另行配置前端环境，本地包禁止直接上传发布。

## 本地启动

需要安装 Docker Desktop，并在二期项目目录 `G:\\桂智旅\\aitravel\\aitravel` 执行：

```powershell
.\start-local.ps1
```

工作区外层目录也提供了转发脚本，直接在 `G:\\桂智旅\\aitravel` 执行同名命令即可。启动脚本会同时打印 `127.0.0.1` 和当前电脑的局域网地址。

启动后检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/v2/health
Invoke-RestMethod 'http://127.0.0.1:8080/api/v2/templates?scene=漓江'
```

停止本地容器：

```powershell
.\start-local.ps1 -Down
```

如果之前已经创建过旧的本地 MySQL volume，需要重新执行初始化脚本时，只删除本地 Compose 数据卷：

```powershell
.\start-local.ps1 -Down -ResetData
.\start-local.ps1
```

本地数据只使用 `aitravel_dev`、Redis DB 2 和 Compose volume，不连接生产数据库、生产 Redis 或生产支付。

## 微信开发者工具

本地开发环境已在 `client/config/environment.js` 中启用：

```text
一期接口：本地构建不调用
二期接口：http://127.0.0.1:8080
支付：mock
```

开发者工具模拟器使用 `http://127.0.0.1:8080`，允许本地 HTTP 调试。当前前端锁定本机业务地址、独立本地缓存，并禁止体验版和正式版运行；不能只改一个地址用于真机或发布。真实 API 的素材隧道只映射 8081，不承载手机业务请求。

## 当前已接入

- 二期 `/api/v2/health`
- 二期 `/api/v2/scenes`
- 二期 `/api/v2/templates`
- 二期 `/api/v2/templates/{template_code}`
- 二期 `/api/v2/projects`
- 二期 `/api/v2/projects/{project_id}`
- 二期角色槽位由模板会话固化，不再提供独立的 roles 覆盖接口
- 二期 `/api/v2/projects/{project_id}/assets`
- 二期 `/api/v2/projects/{project_id}/order`（按环境支付配置下单）
- 二期 `/api/v2/projects/{project_id}/status`
- 热门模板页和全部模板页优先请求二期 API，接口不可用时回退当前 mock 数据
- 模板封面和示例视频通过二期网关 `/assets/` 提供本地资源地址
- 作品页会根据 `project_id` 刷新服务端制作状态；状态由真实任务编排推进
- 模板服务启动时会把 `server/phase2/templates.json` 种入本地 MySQL 的 `phase2_templates` 表；数据库不可用时才回退 JSON
- 独立本地 MySQL、Redis、网关和二期 API Compose 服务

## 后续迁移顺序

下一阶段接入真实文件服务、角色生成、图片任务和视频任务，并让订单回调统一推进制作状态。
