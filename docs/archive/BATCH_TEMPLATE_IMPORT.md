# 二期模板批量导入

一个模板对应一个文件夹或独立 ZIP。批量命令直接读取多个 ZIP，无需在管理网页逐个上传。RAR 仍需先解压。命令会逐个校验、逐个保存；失败模板不会中断其他模板。新导入模板自动上架，初始展示热度为 1 万；真实选择人数仍单独统计。

## 在管理网页批量上传

打开 `/admin/` 的“模板管理”，点击“批量上传模板”，在 Windows 文件选择窗口中一次选择多个独立 ZIP，点击“开始上传”。网页逐个发送请求，显示每个文件的结果；重复模板会跳过，失败项可以重试。上传期间保持页面打开。替换已有模板时，先在“导入方式”选择要替换的模板，再选一个 ZIP。文件很多或网络不稳定时可使用下文的服务器目录导入命令。

## 在 Windows 本地预检

在 PowerShell 中逐条执行：

```powershell
cd 'G:\桂智旅\aitravel\aitravel'
$env:PYTHONPATH = 'server'
py -3.10 server/admin/batch_import.py --source '测试模板\dsc\分镜图已补全模板-1009' --report "$env:TEMP\phase2-template-preview.json"
notepad "$env:TEMP\phase2-template-preview.json"
```

`--source` 可换成包含几百个模板文件夹或独立 ZIP 的上级目录，也可指向单个 ZIP。同名文件夹和 ZIP 只校验一次。预检不连接数据库，也不上传或生成云端内容。报告逐个列出模板名称、编号、状态和具体失败原因。Windows Python 3.10 需安装 `SQLAlchemy` 和 `Pillow`；缺依赖时可在管理节点执行相同的预检。

## 当前这次更新：旧镜像已上传

如果 Windows 执行 `export_data_images.py` 报 `docker image inspect mysql:8.4` 失败，随后上传的 `phase2-data-images.tar.gz` 是旧包。管理节点显示 healthy 只说明旧代码仍可运行。以下步骤使用管理节点已有镜像作为底座，离线加入新代码，不访问 Docker Hub，也无需再次上传镜像或模板。

Windows PowerShell 中重新制作并仅上传小的代码包：

```powershell
cd 'G:\桂智旅\aitravel\aitravel'
py -3.10 infra/staging/package_release.py --node data
if ($LASTEXITCODE -ne 0) { throw '代码包制作失败，停止上传' }
scp .\dist\phase2-data.tar.gz root@115.191.23.137:/opt/guizhilv-phase2/
```

SSH 登录管理节点后逐条执行：

```bash
cd /opt/guizhilv-phase2
tar -xzf phase2-data.tar.gz
docker tag guizhilv-phase2-test-data-admin:latest guizhilv-phase2-test-data-admin:batch-base
docker build --network=none --pull=false -f infra/staging/Dockerfile-admin-offline-update -t guizhilv-phase2-test-data-admin:latest .
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml up -d --no-deps --no-build --pull never --force-recreate --wait --wait-timeout 120 admin
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml exec -T admin python -c "import admin.batch_import; print('batch importer ready')"
```

最后执行下文“先预检、再正式导入”的命令。已经上传到 `/opt/guizhilv-phase2/imports/` 的 ZIP 可直接使用。

## 更新测试管理节点

管理节点为 `115.191.23.137`，路径为 `/opt/guizhilv-phase2`。这次仅需更新管理节点；主节点和 AI 节点不动。先在 Windows 启动 Docker Desktop，确认 Linux Engine 可用，然后执行：

```powershell
cd 'G:\桂智旅\aitravel\aitravel'
py -3.10 infra/staging/package_release.py --node data
if ($LASTEXITCODE -ne 0) { throw '代码包制作失败，停止上传' }
py -3.10 infra/staging/export_data_images.py --node data
if ($LASTEXITCODE -ne 0) { throw '镜像导出失败，停止上传旧镜像' }
scp .\dist\phase2-data.tar.gz .\dist\phase2-data-images.tar.gz .\dist\phase2-data-images.sha256 root@115.191.23.137:/opt/guizhilv-phase2/
```

把模板总目录传到管理节点，独立 ZIP 无需解压。下面以当前样例目录为例；大批量时把本地路径换成包含全部模板文件夹或 ZIP 的目录。已经上传过的目录不必重传。

```powershell
ssh root@115.191.23.137 'mkdir -p /opt/guizhilv-phase2/imports /opt/guizhilv-phase2/import-reports'
scp -r '.\测试模板\dsc\分镜图已补全模板-1009' root@115.191.23.137:/opt/guizhilv-phase2/imports/
```

SSH 登录管理节点后逐条执行：

```bash
cd /opt/guizhilv-phase2
sha256sum -c phase2-data-images.sha256
tar -xzf phase2-data.tar.gz
docker load -i phase2-data-images.tar.gz
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml up -d --no-build --pull never --wait --wait-timeout 120 admin
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml ps admin
```

先预检，再看报告；此时不会写数据库：

```bash
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml run --rm --no-deps admin python /app/server/admin/batch_import.py --source /imports --report /import-reports/preview.json
python3 -m json.tool /opt/guizhilv-phase2/import-reports/preview.json
```

确认有效模板数量后正式导入：

```bash
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml run --rm --no-deps admin python /app/server/admin/batch_import.py --source /imports --report /import-reports/imported.json --commit
python3 -m json.tool /opt/guizhilv-phase2/import-reports/imported.json
```

命令可重复运行。已存在的批量模板编号会跳过；与手工上传模板同景区、同名称的条目也会跳过，避免重复。需要主动替换已有批量模板时才加 `--replace`，替换后自动上架并重置展示基数为 1 万。不要在首次批量导入时使用它。报告保存在管理节点的 `/opt/guizhilv-phase2/import-reports/`，原始素材保存在 `/opt/guizhilv-phase2/imports/`；正式模板素材保存在独立 Docker 卷中。导入前用 `df -h /` 确认空间足够。

## 已上传的旧批量模板

更新管理节点代码后，旧模板不会自动改变。以下命令只处理编号以 `P2B-` 开头、仍待上架、上传日志记录为“待上架”且从未由管理员手动调整过的模板。已有非零展示基数会保留；零基数改为 1 万。先在管理节点预览，核对编号和数量，再执行一次：

```bash
cd /opt/guizhilv-phase2
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml exec -T admin python /app/server/admin/publish_legacy_batch.py
docker compose --env-file .env.data -f infra/staging/docker-compose.data.yml exec -T admin python /app/server/admin/publish_legacy_batch.py --commit
```

命令可重复执行；已处理模板不会再次命中。管理员曾手动下架或调过热度的模板会跳过，需要在管理网页单独确认。主节点读取数据库中的上架状态，缓存约 15 秒后游客端可见。
