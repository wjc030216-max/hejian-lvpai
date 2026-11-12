# 贺见旅拍迁移说明

本项目已从 `G:\桂智旅\aitravel\aitravel` 迁移到 `G:\贺见旅拍`。

## 已迁移

- 小程序客户端：`client/`
- 服务端：`server/`
- 基础设施与部署配置：`infra/`、`deploy-config/`
- 项目文档：`docs/`、`README.md`
- 本地启动与部署脚本
- 非敏感环境变量示例
- 本地真实服务凭证配置 `.env.local.real`（已排除在版本控制外）

## 已排除

- 旧 Git 仓库历史与 `client/.git`
- `.ruff_cache`、`dist/`、`local-backups/`
- `测试模板/`（当前已单独放入 `GPS-888-建崇的幸福时光` 测试模板）

## 当前开发配置

- 项目名称：`贺见旅拍`
- 小程序 AppID：等待填写贺见旅拍的新 AppID
- 小程序入口：`client/`
- 首页：`pages/phase2/entry/entry`
- 本地 API：`http://127.0.0.1:8092`
- 本地环境名：`hejian-local`（与旧桂智旅 Docker 环境隔离）

本地使用 `touristappid` 以免误开旧桂智旅账号。请把根目录和 `client/` 下两份 `project.config.json` 中的测试 AppID
填写为贺见旅拍新申请的真实 AppID，禁止沿用桂智旅 AppID。
后端域名、微信支付和云资源等配置仍需按贺见旅拍的实际环境填写。

## 本地运行

完整 AI 服务链（当前开发使用）：

```powershell
.\start-local-real.ps1 -Offline -NoBuild -ProjectName hejian-local-real -GatewayPort 8092 -MediaPort 8093 -MysqlPort 3310 -RedisPort 6383
```

完整环境入口：

- 小程序 API：`http://127.0.0.1:8092`
- 媒体网关：`http://127.0.0.1:8093`
- 管理员后台：`http://127.0.0.1:10030/admin/`
- MySQL：`127.0.0.1:3310`
- Redis：`127.0.0.1:6383`

`-Offline` 只表示暂不开放云端付费调用；文件、人脸、定妆照、双段视频、TOS 和编排服务仍会全部启动。
不指定 `-Offline` 时，启动脚本会先验证媒体隧道，再开放本地云调用。

## 部署隔离

`deploy_all.ps1` 默认拒绝执行，直到存在 `deploy-config/deploy-targets.json`
且其中主机、安装目录和新小程序 AppID 均不属于旧桂智旅环境。
发布包也必须显式提供：

```powershell
$env:PHASE2_RELEASE_API_BASE_URL='https://<贺见旅拍API域名>'
$env:PHASE2_RELEASE_APP_ID='<贺见旅拍AppID>'
python .\infra\staging\package_release.py
```

轻量模板预览环境：

```powershell
.\start-hejian-local.ps1
```

脚本会构建并启动独立的 MySQL、Redis、API 和网关，然后自动导入
`测试模板\GPS-888-建崇的幸福时光`。停止本地服务：

```powershell
.\start-hejian-local.ps1 -Down
```

当前已支持：

- 旧版 15 秒单片段模板
- 30 秒 A/B 双片段模板（两个 15 秒视频分别生成后拼接）
- 同一角色在不同片段使用不同服装
- 失败后只重试失败片段
- 双段视频统一分辨率、帧率和音轨后只混一次配乐
