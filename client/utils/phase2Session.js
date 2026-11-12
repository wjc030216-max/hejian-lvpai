// This session is independent of all first-phase login data.
let pending = null
let session = null
function ensureSession() {
  if (session && session.expires > Date.now()) return Promise.resolve(session.token)
  if (pending) return pending
  const environment = require('../config/environment').resolveEnvironment()
  const base = getApp().globalData.phase2ApiBaseUrl || environment.phase2ApiBaseUrl
  function call(path, data) {
    require('../config/environment').assertLocalUrl(base + path)
    return new Promise((resolve, reject) => wx.request({ url: base + path, method: data ? 'POST' : 'GET', data,
      success: res => res.statusCode === 200 ? resolve(res.data.data) : reject(new Error((res.data || {}).msg || '登录失败')), fail: reject }))
  }
  pending = call('/api/v2/config').then(config => {
    if (config.auth_mode !== 'wechat') return { token: '', expires_in: 300 }
    return new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }))
      .then(result => call('/api/v2/auth/login', { code: result.code }))
  }).then(result => {
    session = { token: result.token || '', expires: Date.now() + (result.expires_in - 30) * 1000 }
    return session.token
  }).finally(() => { pending = null })
  return pending
}
function clear() { session = null }
module.exports = { ensureSession, clear }
