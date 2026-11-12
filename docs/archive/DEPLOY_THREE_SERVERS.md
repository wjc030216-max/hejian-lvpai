# 二期测试服务器部署与文件分配

本说明对应 2026-10-08 清理后的二期代码。三台均为测试服务器，供开发版、体验版使用；不修改已发布小程序，不上传生产服务器。

## 1. 哪个文件放哪台机器

| 服务器 | 公网 IP（用于 SSH/SFTP） | 私网 IP（程序内部通信） | 上传文件 | 上传和解压路径 | 本机配置文件 |
|---|---|---|---|---|---|
| 主节点 | 115.191.26.134 | 172.31.24.158 | `dist/phase2-main.tar.gz` | `/opt/guizhilv-phase2/` | `/opt/guizhilv-phase2/.env.main` |
| AI 节点 | 115.190.53.29 | 172.31.24.159 | `dist/phase2-ai.tar.gz` | `/opt/guizhilv-phase2/` | `/opt/guizhilv-phase2/.env.ai` |
| 管理与数据节点 | 115.191.23.137 | 172.31.24.157 | `dist/phase2-data.tar.gz` | `/opt/guizhilv-phase2/` | `/opt/guizhilv-phase2/.env.data` |
| 你的 Windows 电脑 | — | — | `client/` 或 `dist/phase2-client.zip` | 当前项目的 `client/` | `client/config/target.js` |

**前端不用放到服务器运行。** 你在 Windows 微信开发者工具中打开 `client/`，编译、上传开发版/体验版。后端三个包分别传到对应服务器。三个包均不包含真实密钥、历史备份或一期运行代码。

不要覆盖一期的 `/home/aitravel/`。二期全部使用新的 `/opt/guizhilv-phase2/`，数据库为 `aitravel_phase2_test`，数据库用户为 `phase2_test`。

## 2. 配置文件怎样生成和分发

本次沿用测试域名 **`api.guizhilv.juxiaobai.com.cn`**，目标为主节点公网 `115.191.26.134`。沿用原项目 AppID `wxd1ce8e9fbba6186a`。DNS 由你在域名官网手动调整，详见 [DNS 修改清单](DNS_TEST_SWITCH.md)。可以沿用同一个小程序的 AppID 做开发版/体验版，必须保证域名是这套测试后端；不发布正式版。不能使用游客 AppID。

本次已在本地 `deploy-config/` 生成配套配置；不要重复生成覆盖密码。以下命令仅用于首次生成或新环境：

```powershell
cd G:\桂智旅\aitravel\aitravel
python .\infra\staging\prepare_configs.py --domain api.guizhilv.juxiaobai.com.cn --appid wxd1ce8e9fbba6186a
```

会生成三个互相匹配的文件，只分发到对应机器（管理节点仍使用 data 这个启动名称）：

| 本地生成文件 | 放到哪台 | 填写什么 |
|---|---|---|
| `deploy-config/.env.main` | 主节点 `/opt/guizhilv-phase2/.env.main` | 微信 AppSecret |
| `deploy-config/.env.ai` | AI 节点 `/opt/guizhilv-phase2/.env.ai` | 阿里云人脸密钥、Seedream 图片 API Key、Seedance 视频 API Key、该视频账号的素材库 AccessKey 和素材组 |
| `deploy-config/.env.data` | 数据节点 `/opt/guizhilv-phase2/.env.data` | TOS 自己账号的 AccessKey、桶名、地区；管理员账号及密码摘要已自动生成 |

管理员初始账号密码保存在本地 `deploy-config/ADMIN_LOGIN.txt`，只给负责后台的人，不要上传代码目录。登录地址是 `https://api.guizhilv.juxiaobai.com.cn/admin/`。

数据库密码、Redis 密码、内部服务密钥、素材地址标识由脚本自动生成并保持一致，不用手工拼接。三个文件中的 `LOCAL_CLOUD_ENABLED=false` 表示先检查环境，暂不提交真实生成；验收基础连通后再按第 6 节开启。

TOS 和 Seedance 素材库不是同一套账号：TOS 密钥只给数据节点；视频 API Key 和视频账号的素材库密钥只给 AI 节点。Seedream 使用独立 API Key。不要把旧生产 `.env` 复制到测试节点。

可参考 `infra/staging/main.env.example`、`ai.env.example`、`data.env.example` 查看每台需要的字段。生成后重复运行脚本不会覆盖现有密码。

## 3. 主节点的 HTTPS 证书

证书文件只放主节点：

```text
/opt/guizhilv-phase2/tls/fullchain.pem
/opt/guizhilv-phase2/tls/privkey.pem
```

为测试域名申请受信任的 HTTPS 证书，例如 Let's Encrypt 正式签发的证书，将完整证书链和私钥复制到以上位置；不能用自签名证书或 Let's Encrypt 测试签发环境的证书做微信真机测试。不要把一期生产域名证书混用。网关统一从这两个路径读取，无需修改 Nginx 配置。

主节点需要空闲的公网 443 端口。如果原测试 Nginx 已占用 443，先确认其站点用途，再停用旧测试站点或调整反向代理；启动脚本不会替你删除或停止现有站点。

## 4. 服务器之间使用哪些端口

三台同一私网且互通时，**不需要内网穿透**。

| 接收机器 | 私网端口 | 用途 | 允许来源 |
|---|---|---|---|
| 主节点 `.158` | 18020 | 读取二期模板快照 | AI 节点 `.159` |
| AI 节点 `.159` | 18002、18005、18006 | 人脸检测、定妆照任务、视频任务 | 主节点 `.158` |
| 数据节点 `.157` | 13306 | 二期 MySQL | 主节点 `.158`、AI 节点 `.159` |
| 数据节点 `.157` | 16379 | Redis，可选缓存 | 主节点 `.158` |
| 数据节点 `.157` | 18004 | 中间图片文件 | 主节点 `.158`、AI 节点 `.159` |
| 数据节点 `.157` | 18012 | 保存、读取 TOS 成片和定妆照 | 主节点 `.158`、AI 节点 `.159` |
| 管理节点 `.157` | 18030 | 管理页面、模板上传与素材读取 | 主节点 `.158` |
| 主节点公网 | 443 | 微信前端和云模型读取指定素材 | 公网 |

这些私网端口已避开一期常用端口。云安全组只允许表中的来源；MySQL、Redis、AI 和文件服务不能开放到任意公网地址。Docker 绑定私网 IP 不代替云安全组规则。管理节点查看状态时还需访问主节点 18020 和 AI 节点 18002、18005、18006；安全组仅允许这些私网来源。三台应允许 HTTPS 出站访问微信、阿里云、火山引擎和 TOS。

Seedream 10008 仅在 AI 节点的 Docker 网络内通信，不映射到宿主机。无需为它开安全组。

## 5. 解压与首次启动

三台安装 Docker Engine + Compose 插件、Python 3；不需要在系统 Python 手工安装所有服务依赖。

**数据节点先启动：**

```bash
sudo mkdir -p /opt/guizhilv-phase2
# 上传 phase2-data.tar.gz 和 .env.data 到该目录后
cd /opt/guizhilv-phase2
sudo tar -xzf phase2-data.tar.gz
sudo chmod 600 .env.data
sudo python3 infra/staging/start_node.py data
```

**然后主节点：**

```bash
sudo mkdir -p /opt/guizhilv-phase2/tls
# 上传 phase2-main.tar.gz、.env.main 和两份证书后
cd /opt/guizhilv-phase2
sudo tar -xzf phase2-main.tar.gz
sudo chmod 600 .env.main tls/privkey.pem
sudo python3 infra/staging/start_node.py main
```

**最后 AI 节点：**

```bash
sudo mkdir -p /opt/guizhilv-phase2
# 上传 phase2-ai.tar.gz 和 .env.ai 后
cd /opt/guizhilv-phase2
sudo tar -xzf phase2-ai.tar.gz
sudo chmod 600 .env.ai
sudo python3 infra/staging/start_node.py ai
```

脚本会检查当前机器的私网 IP、配置、数据库端口，并等待本机服务健康。只检查配置可加 `--check-only`；仅重建配置、不重新构建镜像可加 `--no-build`。

查看状态和日志（各机把 `main` 换成自己的 `ai` 或 `data`）：

```bash
cd /opt/guizhilv-phase2
sudo docker compose --env-file .env.main -f infra/staging/docker-compose.main.yml ps
sudo docker compose --env-file .env.main -f infra/staging/docker-compose.main.yml logs --tail 80
```

不要执行 `down -v`，它会删除该测试节点的数据卷。备份和回退代码之前先保存数据库、上传目录、TOS 记录目录；不要把一期旧库直接导入二期库。

## 6. 连通检查与开启真实生成

1. 浏览器打开 `https://api.guizhilv.juxiaobai.com.cn/api/v2/health`，应返回 `environment: staging`、`status: ok`。
2. 打开 `https://api.guizhilv.juxiaobai.com.cn/api/v2/templates`，确认只有提供的三个测试模板且图片/示例视频可访问；模板筛选来自基础元数据。
3. 在三台对应 `.env` 中补齐本机密钥。把 `LOCAL_CLOUD_ENABLED=false` 改为 `true`，按数据节点 → AI 节点 → 主节点顺序重新执行 `start_node.py 节点 --no-build`。
4. 小程序选模板 → 按模板角色上传照片（一人体验只选一个游客坑位）→ 确认后跳过付款 → 自动生成定妆照、存为资产 → 使用角色原图/三视图/场景生成视频 → 保存作品。定妆照是展示资产，不提交给视频模型。
5. 制作中重复点击、刷新或重启后端，应继续原任务；不要为了处理“待核实”状态重复下单。`server/local/reconcile_task.py` 是已有人工核对工具，使用前核实云任务是否已扣费提交。

付款默认 `dev-bypass`（测试订单支付金额为 0）；接入真实微信支付见下节。此版本禁止以 `APP_ENV=production` 运行，前端也拒绝正式版 `release`。

## 6.5 接入微信支付 JSAPI

商户资料（以本次提供的 `微信支付.zip` 为准）：AppID `wxd1ce8e9fbba6186a`、商户号 `1115854612`、APIv3 密钥，以及商户证书 `apiclient_cert.pem` / `apiclient_key.pem`。平台公钥 `wechatpay_pub.pem` 需在微信商户平台下载，不要混用旧一期证书。

1. 把三份文件放到主节点并限制权限：

```bash
sudo mkdir -p /opt/guizhilv-phase2/certs
# 上传 apiclient_cert.pem、apiclient_key.pem、wechatpay_pub.pem 后：
sudo chmod 600 /opt/guizhilv-phase2/certs/*
```

2. 在主节点 `/opt/guizhilv-phase2/.env.main` 增加以下字段（`CERT_SERIAL_NO` 从商户平台或证书中取得）：

```text
PHASE2_PAYMENT_MODE=wechat
WECHAT_PAY_APPID=wxd1ce8e9fbba6186a
WECHAT_PAY_MCHID=1115854612
WECHAT_PAY_APIV3_KEY=<你的APIv3密钥>
WECHAT_PAY_CERT_SERIAL_NO=<商户证书序列号>
WECHAT_PAY_NOTIFY_URL=https://api.guizhilv.juxiaobai.com.cn/api/v2/payments/notify
AITRAVEL_AIVIDEO_PRICE_FEN=1990
```

3. 重启主节点：`sudo python3 infra/staging/start_node.py main --no-build`。

4. 微信商户平台把支付回调地址设为 `https://api.guizhilv.juxiaobai.com.cn/api/v2/payments/notify`；小程序关联该商户号。

5. 真机下单验证：小程序走完照片检查后点“开始制作”，会拉起微信支付；支付成功后自动进入定妆照/视频生成。回调以微信异步通知为准，预下单成功不代表已付款。

## 7. 微信开发者工具

前端已切换为本次测试域名和原项目 AppID。服务器尚未部署、DNS 尚未切换时无法正常加载二期内容。之后重新切换时，在 Windows 项目根目录执行：

```powershell
.\infra\staging\configure-client.ps1 -Environment staging -Domain api.guizhilv.juxiaobai.com.cn -AppId wxd1ce8e9fbba6186a
```

它会同时改前端后端地址和项目 AppID。重新打开/编译项目，上传开发版或设为体验版，**不要点发布正式版**。

在微信公众平台的小程序“服务器域名”中新增测试 HTTPS 域名用于 request、uploadFile、downloadFile，保留一期生产域名；作品链接会跳转到当前 TOS 桶的 HTTPS 域名，也需配置为 downloadFile 合法域名。体验者还需加入项目体验成员。前端配置不含 AppSecret 或云密钥。

切回本地电脑：

```powershell
.\infra\staging\configure-client.ps1 -Environment local
```

本地与测试服务器的草稿缓存按不同环境和地址分开，不会把本地制作编号带到测试服务器。

## 8. 文件与数据实际位置

| 内容 | 位置 |
|---|---|
| 主节点二期 API、模板读取代码 | `/opt/guizhilv-phase2/server/phase2/` |
| 主节点三个真实测试模板 | `/opt/guizhilv-phase2/测试模板/` |
| AI 节点生图、生视频共用代码 | `/opt/guizhilv-phase2/server/common/` |
| AI 服务启动文件 | `server/personattr/personattr_app.py`、`server/ai2pic/seedream_app.py`、`server/charactergen/charactergen_app.py`、`server/aitask/seeddance_video_app.py` |
| 数据节点文件、TOS 服务 | `server/file/file_app.py`、`server/local/tos_app.py` |
| 管理后台代码 | `/opt/guizhilv-phase2/server/admin/` |
| 后台新上传模板 | Docker 卷 `guizhilv-phase2-test-data_phase2_templates` |
| 数据节点 MySQL 持久数据 | Docker 卷 `guizhilv-phase2-test-data_phase2_mysql` |
| 数据节点中间图片 | Docker 卷 `guizhilv-phase2-test-data_phase2_uploads` |
| 数据节点 TOS 记录 | Docker 卷 `guizhilv-phase2-test-data_phase2_tos` |
| 主节点游客原始照片 | Docker 卷 `guizhilv-phase2-test-main_phase2_api_uploads` |
| AI 节点视频合成暂存 | Docker 卷 `guizhilv-phase2-test-ai_phase2_results` |
| TOS 定妆照和视频 | 你配置的桶下 `phase2-test/portraits/`、`phase2-test/videos/` |

`server/local/` 中的 `results.py`、`tos_app.py`、`tos_storage.py` 是二期开发中编写的共用模块，名称沿用开发目录，不是一期旧流程。内部少数 URL 保留 `/api/v1/` 字样以兼容现有媒体记录；旧分镜、旧人物资料、抽奖、支付实现均不再注册或部署。

## 9. 清理和性能

一期运行目录已移出当前源码并存入电脑的 `G:\桂智旅\phase1-cleanup-backups\`；那里可能包含历史私密配置，不能上传服务器或发群。历史数据库和本地 Docker 数据卷未删除。抽奖数据库定义仅留下注释，不创建新抽奖表。

AI 节点为 2 核 8 GB，定妆照和视频任务先各允许 1 个并行任务；视频合成采用复制视频轨道，仅处理音频，降低 CPU 压力。请求服务改用 Waitress；任务进度持久化 MySQL，Redis 不承担任务真相。模板种子只在不存在时插入，不会每次启动覆盖服务器上的模板编辑。

日志每个容器最多保留 3 份、每份 10 MB。模板图片和示例视频不打入小程序代码包；三份测试模板只是测试数据，实际 500 多条模板需要另行导入真实资料，不虚构补数。

当前还没有登录这三台机器部署。本地通过的测试与远端部署后需要验证的微信登录、域名证书、私网安全组、真实云调用分别记录在 [交付结果](PHASE2_DEPLOYMENT_RESULT.md) 中。


## 10. 管理员后台

主节点 HTTPS 入口 `/admin/` 转发到 `.157` 上的管理程序。管理员账号与游客微信身份分开，使用独立登录密钥；修改操作校验会话。不开放执行系统命令、清空数据库或一键重发付费任务。

| 页面 | 可以做什么 | 数据保存方式 |
|---|---|---|
| 模板管理 | 搜索、竖屏样片预览、查看角色和元数据、ZIP 新增/替换、上下架 | 元数据在二期 MySQL，每次上传独立素材目录 |
| 展示热度 | 设置每个模板的展示基数和显示开关，同时看真实人数 | 基数与真实人数分表保存；默认不编造数字 |
| 制作记录 | 查定妆照、图片、视频任务状态、轮次、失败说明 | 使用持久任务表，不重发生成 |
| 运行监控 | 真实选择人数、上架数、待核实任务、服务连通、管理节点磁盘 | 从数据库和健康接口读取，不调用付费模型 |
| 操作日志 | 管理员登录、模板上传、热度与上下架修改、制作状态变化 | 留存在二期数据库，不展示密钥或用户照片地址 |

真实选择人数按用户确认开始制作计数，同一用户对同一模板只计一次。小程序显示数 = 手动基数 + 真实人数，例如 `3.2万人选择`；此展示数不计入后台真实统计。改热度不会改变模板版本，不影响已经上传照片的草稿。

上传接受现有结构的单个模板 ZIP，校验元数据、人数、角色编号、对应服装图与人物图、场景、封面、MP4 示例、视频描述和定妆照描述。上传默认下架，检查后再上架；替换保留旧素材，已创建的制作使用自己的模板快照。全部下架时游客端显示空列表，不会自动把测试样例重新推上去。

后台日志包含管理员操作和制作状态记录。容器启动错误和完整 Python 堆栈仍按第 5 节用 `docker compose logs` 查看；网页不挂载 Docker 管理接口。运行监控不代表云账号余额和配额充足。
