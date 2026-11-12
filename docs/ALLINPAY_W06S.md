# 通联 W06S 支付接入

依据 `G:\桂智旅\支付口demo` 的已验证协议接入。订单、用户归属、金额、支付状态继续保存在二期项目数据库中，不使用 demo.db。

## 主节点配置

在服务器私有 `.env.main` 中设置：

```dotenv
PHASE2_PAYMENT_MODE=allinpay
ALLINPAY_API_URL=https://interface.allinpaygx.com/api/access/payInterface/unifiedPay
ALLINPAY_REFUND_API_URL=https://interface.allinpaygx.com/api/access/payInterface/refundsuhaimei
ALLINPAY_REFUND_VERSION=v1.0
ALLINPAY_MERCHANT_NO=实际商户号
ALLINPAY_SECRET_KEY=实际商户密钥
ALLINPAY_CASHIER_APPID=wxef277996acc166c3
ALLINPAY_NOTIFY_URL=https://api.guizhilv.juxiaobai.com.cn/api/v2/payments/allinpay/notify
# 当前统一 0.10 元；留空后按 1/2/3 人使用 9.90/16.90/19.90 元
AITRAVEL_AIVIDEO_PRICE_FEN=10
```

金额单位为分，使用服务端配置的正式价格。商户号和密钥只写入服务器 `.env.main`，不进入 git；本地 `deploy-config/.env.main` 是 gitignore 的镜像，用于 scp。凭据只注入 main 的 phase2-api，不注入 AI、日志收集或 data 服务。

小程序已声明 demo 收银台 AppID。如果修改收银台 AppID，还需要同步修改 client/app.json 并重新发布客户端。微信平台需要允许本小程序跳转对应收银台。

## 流程

1. `POST /api/v2/projects/<pid>/order` 校验归属、人脸检查与授权，按服务端价格创建 `pending_payment` 订单。
2. `POST /api/v2/projects/<pid>/prepay` 使用同一商户订单号发起 W06S 预下单，返回 `cashier_appid` 和 `extra_data`。成功响应缓存 5 分钟，过期仍沿用原商户订单号。网络异常不标记已支付。
3. 小程序调用 `wx.navigateToMiniProgram`，原样传递收银台签名字段。跳转成功只代表打开收银台。
4. `GET|POST /api/v2/payments/allinpay/notify` 接收 JSON、表单或查询参数，按照 demo 的排序拼接 MD5 协议验签，再校验订单、支付通道、金额及存在时的商户号。成功后在事务内更新支付状态、实付金额和订单投影，进入 `portrait_submit`。返回纯文本 `success`。
5. 用户返回时 `onShow` 查询项目状态；待支付作品可继续支付。生成依赖服务器通知，不相信小程序返回参数。通知重放不会重新启动已支付项目。
6. 付费生成最终失败后，由用户选择重新生成或申请全额退款。退款单在调用通联前落库，同一退款单号不会自动换成新单号重发。
7. 退款成功后将项目标记为 `refunded`；作品媒体接口、分享接口和相册保存同时失效。后台会删除最终定妆照和视频的 TOS 对象，旧签名直链也随之失效。
8. 退款不会删除用户最初上传的自拍和生成过程中的中间素材；这些用户数据统一按 90 天保留策略清理。

## 部署与验证

```powershell
cd G:\桂智旅\aitravel\aitravel
python infra\staging\package_release.py
scp .\deploy-config\.env.main root@115.191.26.134:/opt/guizhilv-phase2/
.\deploy_all.ps1 -Only main -SkipPackage
```

部署后检查：

```powershell
curl https://api.guizhilv.juxiaobai.com.cn/api/v2/health
curl https://api.guizhilv.juxiaobai.com.cn/api/v2/config
```

`health` 的 `payment_mode` 应为 `allinpay`。真机走一遍「下单 → 收银台 → 支付 → 回调进入制作」，再核对管理台「退款处理」和通联流水。支付、退款和回调日志在 main 节点的 `/opt/guizhilv-phase2/logs/payment/YYYY-MM-DD.log`。

## 验证与限制

```powershell
$env:PYTHONPATH = 'server'
python -m unittest discover -s server/local -p "test_allinpay.py" -v
node --test server/local/test_payment_client.cjs
```

本地自动化测试使用模拟网关，不创建真实收费订单。

demo 没有提供独立的通联主动查单接口。退款使用生产退款接口和固定退款单号重试。生成失败后由用户选择重新生成或申请全额退款，提交退款后不能重新生成。通联返回处理中时后台继续用原退款单号核对；响应不明确时进入待核实，管理台必须按原退款单号核对，不能换单号重复退款。

当前按生产商户凭据接入，不再使用退款测试地址。正式发布仍需确认该商户实际可用的生产退款地址；如商户分配的是通用地址，可在私有 `.env.main` 覆盖 `ALLINPAY_REFUND_API_URL`。
