const api = require('./phase2Api')

async function pay(projectId) {
  const checkout = await api.prepay(projectId)
  if (checkout.payment_status === 'paid') return
  if (checkout.pay_mode === 'w06s') {
    return new Promise((resolve, reject) => wx.navigateToMiniProgram({
      appId: checkout.cashier_appid,
      extraData: checkout.extra_data,
      envVersion: 'release',
      success: resolve,
      fail: error => reject(new Error(/cancel/.test(error.errMsg || '') ? '已取消支付，可稍后继续支付' : '收银台暂时无法打开，请重试'))
    }))
  }
  return new Promise((resolve, reject) => wx.requestPayment({
    ...checkout.pay_params, success: resolve,
    fail: error => reject(new Error(/cancel/.test(error.errMsg || '') ? '已取消支付，可稍后继续支付' : '支付未完成，请重试'))
  }))
}

module.exports = { pay }
