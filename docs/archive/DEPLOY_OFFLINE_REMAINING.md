# 管理节点 healthy 后：主节点与 AI 节点

管理节点 115.191.23.137 已由用户日志确认五个服务 healthy。下面不重启或重建管理节点。

## 1. Windows 上传剩余镜像

```powershell
cd G:\桂智旅\aitravel\aitravel
scp .\dist\phase2-main-images.tar.gz .\dist\phase2-main-images.sha256 root@115.191.26.134:/opt/guizhilv-phase2/
scp .\dist\phase2-ai-images.tar.gz .\dist\phase2-ai-images.sha256 root@115.190.53.29:/opt/guizhilv-phase2/
```

源码包和 .env 已上传，不用再次上传。主节点证书已申请并复制到 tls/。

## 2. 两台分别确认 Docker

```bash
docker version
docker compose version
```

仅当没有安装时，在对应 Ubuntu 服务器执行：

```bash
if [ -f /etc/apt/sources.list.d/docker.list ]; then mv /etc/apt/sources.list.d/docker.list /etc/apt/sources.list.d/docker.list.disabled; fi
apt-get update
apt-get install -y docker.io docker-compose-v2
systemctl enable --now docker
```

不使用前面无法连接的 download.docker.com 软件源。安装失败时停下处理，不继续启动。

## 3. 主节点 115.191.26.134

```bash
cd /opt/guizhilv-phase2
tar -xzf phase2-main.tar.gz
chmod 600 .env.main tls/privkey.pem
sha256sum -c phase2-main-images.sha256
docker load -i phase2-main-images.tar.gz
python3 infra/staging/start_node.py main --check-only
```

校验和配置均通过后，确认可以访问管理节点 MySQL：

```bash
python3 -c "import socket; s=socket.create_connection(('172.31.24.157',13306),5); s.close(); print('Database port reachable')"
```

超时时按 DEPLOY_THREE_SERVERS.md 配置私网安全组；不能开放数据库到整个公网。

```bash
docker compose --env-file .env.main -f infra/staging/docker-compose.main.yml up -d --no-build --pull never --wait --wait-timeout 180
docker compose --env-file .env.main -f infra/staging/docker-compose.main.yml ps
curl --fail --resolve api.guizhilv.juxiaobai.com.cn:443:127.0.0.1 https://api.guizhilv.juxiaobai.com.cn/api/v2/health
```

## 4. AI 节点 115.190.53.29

```bash
cd /opt/guizhilv-phase2
tar -xzf phase2-ai.tar.gz
chmod 600 .env.ai
sha256sum -c phase2-ai-images.sha256
docker load -i phase2-ai-images.tar.gz
python3 infra/staging/start_node.py ai --check-only
python3 -c "import socket; s=socket.create_connection(('172.31.24.157',13306),5); s.close(); print('Database port reachable')"
docker compose --env-file .env.ai -f infra/staging/docker-compose.ai.yml up -d --no-build --pull never --wait --wait-timeout 180
docker compose --env-file .env.ai -f infra/staging/docker-compose.ai.yml ps
```

预期 personattr、seedream、charactergen、video 四个服务 healthy。

## 5. 浏览器验证

打开 https://api.guizhilv.juxiaobai.com.cn/api/v2/health，确认 environment 为 staging，再打开 https://api.guizhilv.juxiaobai.com.cn/admin/，登录账号在电脑 deploy-config/ADMIN_LOGIN.txt。先核对模板、样片和运行监控，再开启真实云调用。

所有服务仍使用 LOCAL_CLOUD_ENABLED=false。开启真实调用后，也用本文的 up 命令重新载入配置；不要改用默认会重新构建的启动命令。修改源码后必须重新打包对应镜像，单独替换服务器源文件不会改变镜像内运行的代码。

故障日志：在对应节点使用 `docker compose --env-file .env.main -f infra/staging/docker-compose.main.yml logs --tail 80`，AI 节点把两处 main 改成 ai。

证书续期仍须使用 `/opt/certbot/bin/certbot`；旧系统 certbot 存在依赖冲突。后续安装续期任务时也应使用这个完整路径，并在成功续期后将证书同步到 tls/、重载 gateway。
