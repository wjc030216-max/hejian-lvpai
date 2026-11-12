# TOS 与日志策略

## 用户媒体

本地磁盘只用于处理中的短时缓存：

- 用户上传的角色原图先校验、压缩，再上传 TOS；TOS 返回成功并写入项目记录后，立即删除本地 JPEG。
- Seedream 生成的中间图和定妆照直接进入 TOS，不再写入文件服务本地目录。
- Seedance 成片在本地完成统一编码和配乐后上传 TOS；任务记录保存成功后立即删除本地 MP4。
- 分享封面直接生成到 TOS；读取时使用短时签名地址，本地不保留缩略图缓存。
- 模板素材属于产品资产，不属于用户缓存，继续保存在模板素材卷。

用户媒体在 TOS 保存 90 天。项目到期清理会删除原图、中间图、定妆照、成片和 Seedance 参考素材；TOS 服务还会按对象登记时间清理孤儿对象，避免数据库记录异常时永久残留。

## 日志

日志永久保存在本地，不参与用户媒体 90 天清理：

- 主节点汇总日志：`/opt/hejian-lvpai/logs/<category>/<YYYY-MM-DD>.log`
- 主节点本机服务日志：`/opt/hejian-lvpai/node-logs/main`
- AI 节点本机服务日志：`/opt/hejian-lvpai/node-logs/ai`
- 数据节点本机服务日志：`/opt/hejian-lvpai/node-logs/data`

每条业务事件包含请求 ID、项目 ID、任务 ID、HTTP 方法、路径、状态码、耗时、错误类型和服务端来源 IP。请求 ID 会通过内部请求头传递到 AI、文件与 TOS 服务，便于跨节点追踪同一笔订单。

`phase2_service_logs` 数据库表同样不再执行定期删除。`server/admin/maintenance.py` 只清理无人引用的模板版本，不删除管理员审计、任务历史或服务日志。
