const { assertLocalUrl } = require('../config/environment')
const { request } = require('./request')

function responseError(res, fallback) {
  const error = new Error((res.data && (res.data.msg || res.data.message)) || fallback || `请求失败: ${res.statusCode}`)
  error.statusCode = res.statusCode
  return error
}

function get(path, data) {
  return new Promise((resolve, reject) => {
    const app = getApp()
    const baseUrl = (app.globalData.phase2ApiBaseUrl || app.globalData.baseUrl || '').replace(/\/+$/, '')
    request({
      url: baseUrl + path,
      method: 'GET',
      data,
      success: res => {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data && res.data.code === 200) {
          resolve(res.data.data)
        } else {
          reject(responseError(res))
        }
      },
      fail: reject
    })
  })
}

function listTemplates(filters) {
  return get('/api/v2/templates', typeof filters === 'string' ? { scene: filters } : (filters || {}))
}

function getTemplate(code) {
  return get('/api/v2/templates/' + encodeURIComponent(code))
}

function createProject(templateCode, templateVersion, requestId) {
  return new Promise((resolve, reject) => {
    const app = getApp()
    const baseUrl = (app.globalData.phase2ApiBaseUrl || app.globalData.baseUrl || '').replace(/\/+$/, '')
    request({
      url: baseUrl + '/api/v2/projects',
      method: 'POST',
      data: { template_code: templateCode, template_version: templateVersion || '', request_id: requestId || ('req_' + Date.now() + '_' + Math.random().toString(16).slice(2)) },
      header: { 'content-type': 'application/json' },
      success: res => {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data && (res.data.code >= 200 && res.data.code < 300)) {
          resolve(res.data.data)
        } else {
          reject(responseError(res, `创建制作会话失败: ${res.statusCode}`))
        }
      },
      fail: reject
    })
  })
}

async function uploadRolePhoto(projectId, personUid, filePath) {
  const token = await require('./phase2Session').ensureSession()
  return new Promise((resolve, reject) => {
    const app = getApp()
    const baseUrl = (app.globalData.phase2ApiBaseUrl || app.globalData.baseUrl || '').replace(/\/+$/, '')
    assertLocalUrl(baseUrl + "/api/v2/projects/")
    wx.uploadFile({
      url: `${baseUrl}/api/v2/projects/${encodeURIComponent(projectId)}/assets`,
      filePath,
      name: 'file',
      header: token ? { Authorization: 'Bearer ' + token } : {},
      formData: { person_uid: String(personUid), photo_consent: 'true' },
      success: res => {
        let body = {}
        if (res.statusCode === 401) require('./phase2Session').clear()
        try { body = JSON.parse(res.data || '{}') } catch (error) { reject(error); return }
        if (res.statusCode >= 200 && res.statusCode < 300 && (body.code === 200 || body.code === 201)) resolve(body.data)
        else reject(new Error(body.msg || `上传失败: ${res.statusCode}`))
      },
      fail: reject
    })
  })
}

function jsonRequest(path, method, data, timeout) {
  return new Promise((resolve, reject) => {
    const app = getApp()
    const baseUrl = (app.globalData.phase2ApiBaseUrl || app.globalData.baseUrl || '').replace(/\/+$/, '')
    request({url: baseUrl + path, method, data, timeout, header: {'content-type': 'application/json'}, success: res => {
      if (res.statusCode >= 200 && res.statusCode < 300 && res.data && (res.data.code >= 200 && res.data.code < 300)) resolve(res.data.data)
      else reject(responseError(res))
    }, fail: reject})
  })
}

const SUBSCRIBE_TEMPLATE_ID = 'GRFxK2EOl8zUAp_gwXD3Nf10x5k4qlsdmjuhUE_HHXM'
function createOrder(projectId, agreement = false, subscribe = false, agreementVersion = '') {
  return jsonRequest(`/api/v2/projects/${encodeURIComponent(projectId)}/order`, 'POST',
    { agreement, agreement_version: agreementVersion || 'travel-photo-v1', subscribe })
}
function prepay(projectId) { return jsonRequest(`/api/v2/projects/${encodeURIComponent(projectId)}/prepay`, 'POST', {}, 35000) }
function getProjectStatus(projectId) { return jsonRequest(`/api/v2/projects/${encodeURIComponent(projectId)}/status`, 'GET') }

function listWorks(offset = 0, limit = 30) { return get('/api/v2/works', { offset, limit }) }
function listWorkStates(limit = 100) { return get('/api/v2/works/watch', { limit }) }
function getWorksStatus(ids) { return jsonRequest('/api/v2/works/status', 'POST', { ids }) }
function getProject(projectId) { return get('/api/v2/projects/' + encodeURIComponent(projectId)) }
function getShare(projectId) { return get('/api/v2/share/' + encodeURIComponent(projectId)) }
function getMessages() { return get('/api/v2/messages') }

function markProjectRead(projectId) { return jsonRequest(`/api/v2/projects/${encodeURIComponent(projectId)}/read`, 'POST', {}) }
function setParticipation(projectId, mode, personUid) { return jsonRequest(`/api/v2/projects/${encodeURIComponent(projectId)}/participation`, 'POST', { mode, person_uid: personUid || '' }) }
function retryProject(projectId) { return jsonRequest(`/api/v2/projects/${encodeURIComponent(projectId)}/retry`, 'POST', {}) }
function refundProject(projectId) { return jsonRequest(`/api/v2/projects/${encodeURIComponent(projectId)}/refund`, 'POST', {}) }
let configPromise = null
let configExpires = 0
function getConfig(force = false) {
  if (!force && configPromise && Date.now() < configExpires) return configPromise
  if (force) { configPromise = null; configExpires = 0 }
  configPromise = get('/api/v2/config').then(config => {
    getApp().globalData.phase2Config = config
    configExpires = Date.now() + 60000
    return config
  }).catch(error => { configPromise = null; configExpires = 0; throw error })
  return configPromise
}
module.exports = { listTemplates, getTemplate, createProject, uploadRolePhoto, createOrder, prepay, getProjectStatus, listWorks, listWorkStates, getWorksStatus, getProject, getShare, getMessages, markProjectRead, setParticipation, retryProject, refundProject, getConfig, SUBSCRIBE_TEMPLATE_ID }

