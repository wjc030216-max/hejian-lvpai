# 测试域名切换清单（2026-10-09）

代码、前端及 deploy-config 中三份配置已统一使用 `https://api.guizhilv.juxiaobai.com.cn`，AppID 沿用原项目 `wxd1ce8e9fbba6186a`。尚未改 DNS、远程部署或发布小程序。

## 你在域名官网要改的记录

截图所列的记录中只修改以下一条，直接编辑现有记录，不新增同名第二条：

| 项目 | 填写内容 |
|---|---|
| 完整域名 | `api.guizhilv.juxiaobai.com.cn` |
| 主机记录（管理 juxiaobai.com.cn 时） | `api.guizhilv` |
| 类型 / 线路 | A / 默认，保持原值 |
| 旧记录值 | `115.191.23.137` |
| 新记录值 | `115.191.26.134` |
| TTL | 10 分钟，保持原值 |

另外两条测试记录 `admin.guizhilv.juxiaobai.com.cn → 115.191.26.134`、`guizhilv.juxiaobai.com.cn → 115.190.53.29` 暂时保持。二期目前只配置上述 api 域名，不使用它们作入口。正式版的 `guizhilv.com.cn`、`api.guizhilv.com.cn` 不修改。

## 截图备注与本次分工

截图备注是旧部署用途，不是二期包的分配依据。按这次已完成的部署包：

| 服务器 | 截图旧备注 | 二期用途 | 文件 |
|---|---|---|---|
| `115.191.26.134` | admin | 主节点、HTTPS 统一入口 | phase2-main.tar.gz + .env.main |
| `115.190.53.29` | 业务服务器 | AI 节点 | phase2-ai.tar.gz + .env.ai |
| `115.191.23.137` | ai任务 | 管理与数据库节点 | phase2-data.tar.gz + .env.data |

管理后台通过主节点转发到管理节点，打开 `https://api.guizhilv.juxiaobai.com.cn/admin/`。它不要求独立 admin 域名。内部通信使用私网 IP，不依赖这三条公网域名。

## 操作时机和检查

1. 上传配套代码与配置，补齐微信 AppSecret，主节点放好覆盖 `api.guizhilv.juxiaobai.com.cn` 的受信任证书。按部署说明完成三台服务启动。
2. 在官网修改上述一条 A 记录，等待 DNS 缓存刷新。若证书签发需要提前验证域名，可用 DNS 验证；本部署默认只监听 443，不提供 HTTP 80 的证书验证接口。
3. Windows 执行 `Resolve-DnsName api.guizhilv.juxiaobai.com.cn -Type A`，确认返回 `115.191.26.134`。TTL 为 10 分钟不保证所有网络同时刷新。
4. 打开 `https://api.guizhilv.juxiaobai.com.cn/api/v2/health`，应显示 staging 和 ok，然后登录 `/admin/`。
5. 微信公众平台保留生产合法域名，确认此测试域名可用于 request、uploadFile、downloadFile；再打开微信开发者工具测试。TOS 下载域名仍需按部署说明配置。

切换会影响仍使用旧测试接口域名的开发版和体验版。正式版使用原生产接口地址，此次不改其域名或服务器。

## 本地已准备的文件

- `deploy-config/.env.main`、`.env.ai`、`.env.data`：数据库密码、内部服务密钥、媒体地址已配套生成。外部云服务密钥与微信 AppSecret 留空，按各节点分别填写；真实云调用开关为 false。
- `deploy-config/ADMIN_LOGIN.txt`：初始管理员账号密码，私密保存，不要和代码一起上传。
- `dist/`：重新生成的部署包不包含以上私密文件；前端包已使用测试域名。

三台目录、端口、证书和启动命令见 [部署说明](DEPLOY_THREE_SERVERS.md)。
