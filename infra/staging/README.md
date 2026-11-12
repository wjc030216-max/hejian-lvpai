# 三台测试服务器部署

完整说明、逐机文件分配、路径、密钥分工及命令见 `README.md` 第 12 节和 `docs/archive/DEPLOY_COMMANDS.md`。

- `prepare_configs.py`：生成三台各自的 `.env.main`、`.env.ai`、`.env.data`。
- `start_node.py main|ai|data`：检查本机环境并启动本机服务。
- `configure-client.ps1`：Windows 小程序本地/测试地址切换。
- `package_release.py`：按允许清单制作三个后端包和一个前端包，排除密钥与一期代码。

默认部署根目录：`/opt/hejian-lvpai`。先数据节点，再主节点，最后 AI 节点。
