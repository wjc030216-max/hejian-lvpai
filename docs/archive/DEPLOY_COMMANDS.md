# 二期三台测试服务器部署命令

三台服务器已经重装，以下安装命令适用于 Ubuntu 22.04/24.04。旧一期进程和代码不用再停止。命令中的 `root@IP` 在 Windows PowerShell 执行，其余命令在 SSH 登录后的服务器执行。主节点启动前必须完成第 8 节 DNS 和证书步骤。

实际执行顺序是：第 1 节确认系统 → 第 2 节安装 Docker → 第 3 节上传 → 第 4 节启动数据节点 → 第 8 节修改 DNS 并申请证书 → 第 5 节启动主节点 → 第 6 节启动 AI 节点 → 第 7 节检查日志 → 第 9 节开启真实云调用。

## 1. 每台服务器先确认系统

```bash
cat /etc/os-release
ip -4 addr
df -h /
```

应确认私网地址分别为 `172.31.24.158`、`.159`、`.157`。若不是 Ubuntu 22.04/24.04，先不要执行以下安装命令，提供系统版本后另配安装源。

## 2. 每台服务器安装 Docker

```bash
apt-get update
apt-get install -y ca-certificates curl gnupg python3 nano openssl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker version
docker compose version
```

任何下载、安装命令失败时，先停在该步骤，不能跳过失败的软件源配置继续执行。

## 3. Windows 上传文件

在 Windows PowerShell 的项目根目录执行：

```powershell
cd G:\桂智旅\aitravel\aitravel
```

先确认三份配置已填入微信 AppSecret、AI 密钥和 TOS 密钥；`LOCAL_CLOUD_ENABLED` 先保持 `false`。配置文件只上传到对应服务器。

```powershell
ssh root@115.191.23.137 "mkdir -p /opt/guizhilv-phase2"
scp .\dist\phase2-data.tar.gz .\deploy-config\.env.data root@115.191.23.137:/opt/guizhilv-phase2/
```

```powershell
ssh root@115.191.26.134 "mkdir -p /opt/guizhilv-phase2/tls"
scp .\dist\phase2-main.tar.gz .\deploy-config\.env.main root@115.191.26.134:/opt/guizhilv-phase2/
```

```powershell
ssh root@115.190.53.29 "mkdir -p /opt/guizhilv-phase2"
scp .\dist\phase2-ai.tar.gz .\deploy-config\.env.ai root@115.190.53.29:/opt/guizhilv-phase2/
```

若已备份有效的 `api.guizhilv.juxiaobai.com.cn` 证书，可上传；没有备份则跳过此命令，按第 8 节申请：

```powershell
scp .\tls\fullchain.pem .\tls\privkey.pem root@115.191.26.134:/opt/guizhilv-phase2/tls/
```

## 4. 管理与数据节点（115.191.23.137）

```bash
cd /opt/guizhilv-phase2
tar -xzf phase2-data.tar.gz
chmod 600 .env.data
python3 infra/staging/start_node.py data --check-only
python3 infra/staging/start_node.py data
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml ps
```

## 5. 主节点（115.191.26.134）

```bash
cd /opt/guizhilv-phase2
tar -xzf phase2-main.tar.gz
chmod 600 .env.main tls/privkey.pem tls/fullchain.pem
openssl x509 -in tls/fullchain.pem -noout -dates -ext subjectAltName
python3 infra/staging/start_node.py main --check-only
python3 infra/staging/start_node.py main
docker compose --env-file .env.main -f infra/staging/docker-compose.main.yml ps
```

## 6. AI 节点（115.190.53.29）

```bash
cd /opt/guizhilv-phase2
tar -xzf phase2-ai.tar.gz
chmod 600 .env.ai
python3 infra/staging/start_node.py ai --check-only
python3 infra/staging/start_node.py ai
docker compose --env-file .env.ai -f infra/staging/docker-compose.ai.yml ps
```

## 7. 查看日志

```bash
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml logs --tail 100
```

对应节点把 `.env.data` 和 compose 文件名换成 `.env.main`、`.env.ai`。不要执行 `docker compose down -v`。

## 8. DNS 和 HTTPS

此步骤在第 5 节启动主节点之前完成。在域名官网把 `api.guizhilv.juxiaobai.com.cn` 的 A 记录改成 `115.191.26.134`，生产域名不改。主节点安全组放行 TCP 80（证书申请及后续自动续期）和 443。如果该域名有旧 AAAA 记录，也要确认不会把验证请求导向别的机器。

DNS 生效后，在主节点申请证书：

```bash
apt-get install -y certbot
certbot certonly --standalone --preferred-challenges http --cert-name api.guizhilv.juxiaobai.com.cn -d api.guizhilv.juxiaobai.com.cn
mkdir -p /opt/guizhilv-phase2/tls
install -m 644 /etc/letsencrypt/live/api.guizhilv.juxiaobai.com.cn/fullchain.pem /opt/guizhilv-phase2/tls/fullchain.pem
install -m 600 /etc/letsencrypt/live/api.guizhilv.juxiaobai.com.cn/privkey.pem /opt/guizhilv-phase2/tls/privkey.pem
```

按提示填接收证书到期通知的邮箱、同意条款。申请失败先解决报错，不要创建空证书。部署成功后，配置续期时同步证书并重载网关（以下整块粘贴）：

```bash
cat > /etc/letsencrypt/renewal-hooks/deploy/guizhilv-phase2.sh <<'EOF'
#!/bin/sh
set -eu
[ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/api.guizhilv.juxiaobai.com.cn ] || exit 0
install -m 644 "$RENEWED_LINEAGE/fullchain.pem" /opt/guizhilv-phase2/tls/fullchain.pem
install -m 600 "$RENEWED_LINEAGE/privkey.pem" /opt/guizhilv-phase2/tls/privkey.pem
cd /opt/guizhilv-phase2
docker compose --env-file .env.main -f infra/staging/docker-compose.main.yml exec -T gateway nginx -s reload
EOF
chmod 700 /etc/letsencrypt/renewal-hooks/deploy/guizhilv-phase2.sh
systemctl enable --now certbot.timer
certbot renew --dry-run
```

Windows 验证：

```powershell
Resolve-DnsName api.guizhilv.juxiaobai.com.cn -Type A
curl.exe --fail https://api.guizhilv.juxiaobai.com.cn/api/v2/health
```

## 9. 开启真实云调用

首次只保持 `LOCAL_CLOUD_ENABLED=false`，确认健康接口、后台和模板预览正常。确认后分别编辑 `.env.data`、`.env.ai`、`.env.main`，将其改为 `true`。以下三条分别在对应服务器的 `/opt/guizhilv-phase2` 中执行，不要在同一台连续执行：

```bash
python3 infra/staging/start_node.py data --no-build
python3 infra/staging/start_node.py ai --no-build
python3 infra/staging/start_node.py main --no-build
```

真实云调用会产生费用，只用一个测试模板完成一次完整流程后再扩大测试。

私网端口和微信合法域名设置见 `DEPLOY_THREE_SERVERS.md` 第 4、7 节。主节点的 18020 还需允许管理节点访问，AI 节点的 18002、18005、18006 同样允许管理节点做健康检查。数据库和这些内部端口不向公网开放。
