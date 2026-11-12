# 单机部署（不跨主机）

本文档描述把贺见旅拍的 12 个服务全部跑在**一台服务器**上的方式。服务之间只通过
Docker compose 网络按容器名通信，宿主机只需要对外开放 `22 / 80 / 443`。

- compose：[`infra/docker-compose.single.yml`](../docker-compose.single.yml)
- 网关模板：[`infra/nginx.single.conf.template`](../nginx.single.conf.template)
- 环境变量示例：[`infra/staging/single.env.example`](single.env.example)

与三节点（`docker-compose.main/ai/data.yml`）相比，业务代码完全一致，区别只在部署形态：

| | 单机 | 三节点 |
|---|---|---|
| 服务通信 | 容器名（`phase2-api:10020` 等） | 私网 IP + 18002/18004/… |
| 安全组 | 22 / 80 / 443 | 22 / 80 / 443 + 内网段全端口 |
| 适用 | 上线初期、压测、低运维成本 | 容量上来后按节点隔离 |

## 1. 前置条件

- 服务器：4 核 16G 起，Ubuntu 22.04/24.04，**磁盘 ≥ 100G 建议**（默认 40G 很快会被媒体占满）
- 已安装 Docker Engine 24+ 和 compose 插件（`docker compose version` 能看到 v2）
- 域名 `api.<你的域名>` 已解析到本机公网 IP，且 **ICP 备案已通过**
- 微信小程序后台已把该 HTTPS 域名加入 request / uploadFile / downloadFile 合法域名

## 2. 目录布局

```text
/opt/hejian-lvpai/
├── server/                      # 代码（compose 的 build context）
├── infra/
│   ├── docker-compose.single.yml
│   └── nginx.single.conf.template
├── .env.single                  # 环境变量（含密钥，勿外传）
├── tls/
│   ├── fullchain.pem
│   └── privkey.pem
├── certbot-webroot/             # 续期验证目录
├── certs/                       # 微信支付证书（如启用微信支付）
└── imports/                     # 可选：模板批量导入源目录
```

## 3. 上传代码

在本地项目根目录执行（排除体积大且运行时不需要的目录）：

```bash
rsync -avz --delete \
  --exclude 'node_modules' --exclude 'dist' --exclude '.git' \
  --exclude '测试模板' --exclude 'local-reports' --exclude 'template-assets' \
  --exclude '.env*' --exclude '__pycache__' \
  ./ root@<服务器IP>:/opt/hejian-lvpai/
```

> `infra/staging/local-reports/` 等运行时目录不需要上传。首次部署后服务器上的
> `.env.single` 不要被覆盖，后续升级只同步 `server/` 与 `infra/`。

## 4. 生成 `.env.single`

```bash
cd /opt/hejian-lvpai
cp infra/staging/single.env.example .env.single
chmod 600 .env.single
```

生成各类密钥：

```bash
openssl rand -hex 32   # AITRAVEL_JWT_SECRET（≥32 字符）
openssl rand -hex 32   # LOCAL_MEDIA_TOKEN（必须正好 64 位十六进制）
openssl rand -hex 32   # ADMIN_JWT_SECRET
openssl rand -hex 16   # PHASE2_DB_PASSWORD / MYSQL_ROOT_PASSWORD / REDIS_PASSWORD
```

生成管理员密码哈希（在服务器上执行，需要 python3）：

```bash
cd /opt/hejian-lvpai
python3 -c "import sys; sys.path.insert(0,'server'); from common.admin_password import password_hash; print(password_hash('替换成你的密码'))"
```

把输出整行（`scrypt:...:...`）填到 `ADMIN_PASSWORD_HASH`。

必须一起改、保持一致的几项：

- `TEST_DOMAIN`、`ALLINPAY_NOTIFY_URL` → 同一个域名
- `WECHAT_APP_ID` / `WECHAT_APP_SECRET` → 小程序后台
- `PHASE2_DB_PASSWORD` **不能包含** `@ : / # ?` 等字符（会被拼进数据库 URL）；如必须使用，先做 URL 编码
- `APP_ENV=staging` 时 `PHASE2_DB_NAME` 必须是 `aitravel_phase2_test`（代码强制校验）
- 切正式环境：`APP_ENV=production`、数据库名去掉 `test`、`PHASE2_PAYMENT_MODE=allinpay`、
  `LOCAL_CLOUD_ENABLED=true`、`ALLINPAY_REFUND_API_URL` 为 HTTPS

## 5. 申请 TLS 证书

首次签发用 standalone（此时 80 端口还没有被容器占用）：

```bash
sudo apt-get update && sudo apt-get install -y certbot
sudo certbot certonly --standalone -d api.hejianlvpai.com \
     --agree-tos -m <你的邮箱> --non-interactive

sudo mkdir -p /opt/hejian-lvpai/tls
sudo cp /etc/letsencrypt/live/api.hejianlvpai.com/fullchain.pem /opt/hejian-lvpai/tls/
sudo cp /etc/letsencrypt/live/api.hejianlvpai.com/privkey.pem  /opt/hejian-lvpai/tls/
sudo chmod 644 /opt/hejian-lvpai/tls/*.pem
```

续期（网关已启动，改用 webroot，不需要停服务）：

```bash
sudo certbot renew --webroot -w /opt/hejian-lvpai/certbot-webroot --deploy-hook \
  "cp /etc/letsencrypt/live/api.hejianlvpai.com/fullchain.pem /opt/hejian-lvpai/tls/ && \
   cp /etc/letsencrypt/live/api.hejianlvpai.com/privkey.pem /opt/hejian-lvpai/tls/ && \
   docker compose --env-file /opt/hejian-lvpai/.env.single \
     -f /opt/hejian-lvpai/infra/docker-compose.single.yml exec -T gateway nginx -s reload"
```

## 6. 首次启动

```bash
cd /opt/hejian-lvpai
docker compose --env-file .env.single -f infra/docker-compose.single.yml up -d --build
```

首次构建会拉取 Python 3.11、MySQL 8.4、Redis、nginx 镜像并安装依赖，约 5~15 分钟。

## 7. 验收

```bash
# 1. 12 个容器是否全部 healthy
docker compose --env-file .env.single -f infra/docker-compose.single.yml ps

# 2. 网关健康检查（不依赖证书）
curl -fsS http://127.0.0.1/healthz

# 3. 业务健康检查（应返回 environment=staging）
curl -fsS https://api.hejianlvpai.com/api/v2/health

# 4. 媒体令牌路径（应返回 200 或 404，而不是 502）
curl -I "https://api.hejianlvpai.com/media/<LOCAL_MEDIA_TOKEN>/ready"

# 5. 管理台
curl -I https://api.hejianlvpai.com/admin/
```

小程序端验收顺序：登录 → 模板列表 → 上传照片 → 人脸检查 → 下单 →
定妆照 → 视频 → 保存/分享。

## 8. 日常运维

```bash
cd /opt/hejian-lvpai
COMPOSE="docker compose --env-file .env.single -f infra/docker-compose.single.yml"

$COMPOSE logs -f --tail=200 gateway      # 网关
$COMPOSE logs -f --tail=200 phase2-api   # 主业务
$COMPOSE restart phase2-api              # 重启单个服务
$COMPOSE down                            # 停止（保留数据卷）
$COMPOSE up -d                           # 再次启动
```

升级：重新同步代码后

```bash
$COMPOSE up -d --build
```

备份（建议每日 cron）：

```bash
docker exec hejian-lvpai-single-mysql-1 sh -c \
  'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction aitravel_phase2_test' \
  | gzip > /opt/backup/hejian-$(date +%F).sql.gz
```

媒体与模板在命名卷里，可用 `docker run --rm -v hejian-lvpai-single_hejian_uploads_file:/data ...`
打包；正式环境建议按 README 的 90 天策略把最终媒体转 TOS，控制本机磁盘。

## 9. 注意事项

- **单点**：MySQL、AI worker、API 在同一台机器，任一进程打满内存都会影响全站。
  建议给 mysql 容器加内存上限（`mem_limit: 4g`）并监控磁盘。
- **磁盘**：40G 系统盘跑完整链路偏紧，模板和中间图增长很快；上线前扩盘或尽早转 TOS。
- **换回三节点**：代码无需改动，恢复 `infra/staging/docker-compose.{main,ai,data}.yml`
  和三份 `.env.<node>` 即可，端口与地址已在三节点 compose 中定义。
- `imports/` 目录用于管理台批量导入模板，不存在时 Docker 会自动创建空目录。

## 10. 部署时容易踩的坑（已在实际服务器验证）

- **pip 源**：国内机房访问 `pypi.tuna.tsinghua.edu.cn` 会返回 403。四个
  `server/staging/Dockerfile-*` 已支持 `ARG PIP_INDEX_URL`，默认阿里云源；
  如需更换可在 compose 的 `build.args` 里覆盖。
- **Docker Hub 直连不通**：需要给 `/etc/docker/daemon.json` 配置
  `registry-mirrors`（例如 `https://docker.m.daocloud.io`），否则构建时
  `python:3.11-slim` 会解析到被污染的 IP 并超时。
- **同一镜像多个服务**：compose 里只有 `file` / `admin` / `seedream` /
  `phase2-api` 负责构建镜像，`tos` / `personattr` / `charactergen` / `video` /
  `logcollector` 直接复用对应镜像。不要让多个服务同时构建同一个 tag，
  BuildKit 会报 `image ... already exists`。
- **CMD 缺失**：`Dockerfile-data`、`Dockerfile-ai` 没有 CMD，凡是复用这两个镜像的
  服务都必须在 compose 里写 `command`，否则容器启动即退出（exit 0）。
- **TOS 前缀**：`TOS_KEY_PREFIX` 必须落在环境命名空间下，staging 用
  `phase2-test/`、production 用 `phase2-prod/`，否则 `tos` 容器启动时报
  `ValueError: TOS_KEY_PREFIX must stay under ...`。
- **证书顺序**：先把证书放进 `tls/` 再 `up`；否则 nginx 因读不到证书反复重启。
  证书还没就绪时可以用自签证书临时启动，`certbot` 走 webroot 续期后再替换。

## 11. 收费形态切换

服务器上有一键切换脚本（源码在 `infra/staging/switch-mode.sh`）：

| 命令 | 效果 | 适用 |
|---|---|---|
| `./switch-mode.sh free` | `dev-bypass`，全程不扣款，名额计数器不动 | 开发联调、内部演示 |
| `./switch-mode.sh quota 500` | 前 500 单免费（先到先得），之后按标准价走通联 | 展会放量 |
| `./switch-mode.sh paid` | 全部按标准价收费，无免费名额 | 正式运营 |

脚本只改 `.env.single` 里的 `PHASE2_PAYMENT_MODE` 和 `PHASE2_FREE_QUOTA_TOTAL`，
然后重启 `phase2-api` 并打印当前生效配置。

> 切到 `quota` / `paid` 之前必须确认：域名已备案、公网 443 可达、通联回调地址
> `https://api.hejianlvpai.com/api/v2/payments/allinpay/notify` 能从公网访问到。
> 否则用户下单后会停在「等待支付」，永远收不到回调。

重置名额（彩排消耗后想归零）：

```bash
docker exec hejian-lvpai-single-mysql-1 mysql -uroot -p"$MYSQL_ROOT_PASSWORD"   -e "UPDATE aitravel_phase2_test.phase2_free_quota SET used=0 WHERE id=1"
```

价格档位由 `AITRAVEL_AIVIDEO_PRICE_FEN` 控制：留空使用 990 / 1690 / 1990 分档价，
填数字则三档统一覆盖价（测试期常用 10，即 0.10 元）。
