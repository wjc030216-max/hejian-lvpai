# 管理节点无法连接 Docker Hub 时的部署

适用机器：115.191.23.137（私网 172.31.24.157）。Docker 与 Compose 已安装、配置已检查通过，但下载 mysql:8.4 超时。

离线包由本机已有的官方 MySQL、Redis 镜像和从已交付源码包构建的三个服务镜像组成。只打包镜像，不导出容器、数据库数据卷或 .env；不会包含本机测试数据。只避开镜像下载与构建，真实云调用仍需要服务器访问各云服务。

## 1. Windows PowerShell 上传

```powershell
cd G:\桂智旅\aitravel\aitravel
scp .\dist\phase2-data-images.tar.gz .\dist\phase2-data-images.sha256 root@115.191.23.137:/opt/guizhilv-phase2/
```

## 2. 管理节点终端校验并导入

```bash
cd /opt/guizhilv-phase2
sha256sum -c phase2-data-images.sha256
docker load -i phase2-data-images.tar.gz
```

校验应显示 OK。导入应出现 mysql:8.4、redis:7.4-alpine 和 guizhilv-phase2-test-data-file/tos/admin 三个镜像标签。首次导入需要一点时间。

## 3. 检查配置并启动

```bash
python3 infra/staging/start_node.py data --check-only
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml up -d --no-build --pull never --wait --wait-timeout 180
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml ps
```

此命令明确禁止重新下载和构建。暂时不要使用默认的 `start_node.py data`，默认命令会构建服务镜像。以后仅修改配置重启时也可以使用上面的 up 命令。

## 4. 如启动失败

```bash
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml logs --tail 80
```

不要删除数据卷重试。没有匹配的镜像时报错即停，不会尝试下载替代镜像。

## 重新生成离线包

开发电脑需有运行中的 Docker、本地 linux/amd64 的 mysql:8.4、redis:7.4-alpine 和最新源码包。执行 `python infra/staging/export_data_images.py`，它从 dist/phase2-data.tar.gz 解压构建，导出前核对五个镜像标签。该脚本不改运行中的本地容器。
