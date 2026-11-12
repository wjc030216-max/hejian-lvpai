const target = require('./target')
const CURRENT_ENV = target.environment
const ENVIRONMENTS = {
  local: { environment: 'local', localOnly: true, phase2ApiBaseUrl: target.apiBaseUrl, phase2ApiEnabled: true, paymentChannel: 'mock' },
  staging: { environment: 'staging', localOnly: false, phase2ApiBaseUrl: target.apiBaseUrl, phase2ApiEnabled: true, paymentChannel: 'dev-bypass' },
  production: { environment: 'production', localOnly: false, phase2ApiBaseUrl: target.apiBaseUrl, phase2ApiEnabled: true, paymentChannel: 'dev-bypass' }
}
function resolveEnvironment() {
  let version = 'develop'
  try { version = wx.getAccountInfoSync().miniProgram.envVersion || 'develop' } catch (_) {}
  if (version === 'release' && CURRENT_ENV !== 'production') throw new Error('当前构建仍指向测试环境，不能发布正式版')
  if (!ENVIRONMENTS[CURRENT_ENV]) throw new Error('开发环境配置无效')
  if (CURRENT_ENV === 'local' && version !== 'develop') throw new Error('本机地址不能用于体验版，请切换测试服务器')
  if (['staging', 'production'].includes(CURRENT_ENV) && !/^https:\/\/[^/]+$/.test(target.apiBaseUrl)) throw new Error('请设置服务器 HTTPS 域名')
  return ENVIRONMENTS[CURRENT_ENV]
}
function assertLocalUrl(url) {
  const base = resolveEnvironment().phase2ApiBaseUrl
  if (!String(url).startsWith(base + '/api/v2/')) throw new Error('已阻止跨环境业务请求')
}
module.exports = { CURRENT_ENV, ENVIRONMENTS, resolveEnvironment, assertLocalUrl }
