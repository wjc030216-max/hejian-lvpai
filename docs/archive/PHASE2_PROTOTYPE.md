# 二期前端原型机

原型入口：`/pages/phase2/entry/entry`

在微信开发者工具中打开 `aitravel/client` 对应的小程序项目后，可在“编译模式”里把启动页面填写为上面的路径。原型只使用本地 mock 数据，不调用一期接口、测试服务器、支付、AI、Redis 或数据库。从 `/pages/phase2/` 编译启动时，全局启动逻辑也会跳过一期静默登录；从首页启动则保持一期行为。

## 可点击流程

景区 → 景区精选模板（随机推送/热度提示/换一批）→ 查看全部模板（按类型、人数、关系筛选并横滑）→ 按模板角色槽位上传照片 → 测试环境下单 → 服务端制作进度 → 我的作品。

状态保存在微信本地缓存 `phase2PrototypeState.v3`，入口页底部可以重置；制作页面只刷新服务端状态，不再提供伪造完成或失败的演示按钮。

## 代码边界

- 页面：`client/pages/phase2/`
- mock 数据：`client/mock/phase2Data.js`
- mock 状态：`client/mock/phase2State.js`
- 视觉样式：`client/styles/phase2.wxss`

后续接入二期后端时，只需要把页面里的状态写入替换为 API 调用；一期页面和一期 `utils/request.js` 没有被二期原型复用。

