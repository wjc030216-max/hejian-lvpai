const { assertLocalUrl } = require('../config/environment')
function request(options) {
  try { assertLocalUrl(options.url) } catch (error) {
    if (options.fail) options.fail(error)
    return
  }
  // Never reuse the published mini program's token in this local build.
  const session = require('./phase2Session')
  return session.ensureSession().then(token => wx.request({ ...options,
    timeout: options.timeout || 15000,
    header: { ...(options.header || {}), ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    success: res => { if (res.statusCode === 401) session.clear(); if (options.success) options.success(res) }
  })).catch(error => { if (options.fail) options.fail(error) })
}
module.exports = { request }
