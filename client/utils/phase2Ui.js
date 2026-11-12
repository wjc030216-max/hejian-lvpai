const ROOT = '/pages/phase2/'
function layout() {
  const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
  const menu = wx.getMenuButtonBoundingClientRect ? wx.getMenuButtonBoundingClientRect() : null
  return { navTop: menu && menu.top || info.statusBarHeight || 24,
    navHeight: menu && menu.height || 32, navRight: menu ? info.windowWidth - menu.left + 12 : 100 }
}
function home() { wx.reLaunch({ url: ROOT + 'entry/entry' }) }
function back() { if (getCurrentPages().length > 1) wx.navigateBack(); else home() }
function open(name, id) {
  const url = ROOT + name + '/' + name + (id ? '?id=' + encodeURIComponent(id) : '')
  const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : []
  if (pages.length >= 8) wx.redirectTo({ url }); else wx.navigateTo({ url })
}
function message(error) { return error.message || error.errMsg || '暂时无法连接，请重试' }
function toast(error) { wx.showToast({ title: typeof error === 'string' ? error : message(error), icon: 'none' }) }
function time(value) { if (!value) return ''; const d = new Date(value * 1000); return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
function projectView(p) {
  const terminal = ['completed', 'terminated', 'refund_pending', 'refund_failed', 'refunded', 'archived', 'expired'].includes(p.status)
  const uncertain = (p.status || '').includes('unknown') || p.status === 'needs_review'
  const unpaid = p.status === 'pending_payment'
  const statusKind = unpaid ? 'unpaid' : p.status === 'completed' ? 'done' : (p.status === 'archived' || p.status === 'refunded' || p.status === 'expired') ? 'archived'
    : ['terminated', 'refund_pending', 'refund_failed'].includes(p.status) ? 'failed' : 'working'
  return { ...p, terminal, unpaid, statusKind, priceText: ((p.amount_fen || 0) / 100).toFixed(2), timeText: time(p.paid_at || p.created_at),
    statusText: unpaid ? '待支付' : p.status === 'refunded' ? '已退款 · 已归档' : p.status === 'refund_failed' ? '退款失败' : p.status === 'refund_pending' ? '退款处理中' : p.status === 'expired' ? '数据保留期已结束' : p.status === 'archived' ? '历史作品' : p.status === 'completed' ? '已完成' : p.status === 'terminated' ? '制作未完成' : uncertain ? '任务待核实' : '制作中',
    portraitText: p.portrait_url ? '已完成' : p.status === 'refunded' ? '已归档' : p.status === 'expired' ? '已清理' : unpaid ? '等待支付' : terminal ? '未生成' : '定妆照生成中',
    videoText: p.video_url ? '已完成' : p.status === 'refunded' ? '已归档' : p.status === 'expired' ? '已清理' : unpaid ? '等待支付' : terminal ? '未生成' : uncertain ? '任务待核实' : p.portrait_url ? '视频生成中' : '等待定妆照完成' }
}
function saveMediaNow(url, kind) {
  wx.showLoading({ title: '正在保存' })
  let ext = 'jpg'
  const clean = String(url).split('?')[0].split('#')[0]
  const match = clean.match(/\.([a-zA-Z0-9]{2,5})$/)
  if (match) ext = match[1].toLowerCase()
  if (kind === 'video') ext = 'mp4'
  const userDir = (wx.env && wx.env.USER_DATA_PATH) || ''
  const separator = String(url).indexOf('?') >= 0 ? '&' : '?'
  const options = { url: String(url) + separator + 'download=1' }
  if (userDir) options.filePath = `${userDir}/hejianlvpai_save_${Date.now()}.${ext}`
  return new Promise((resolve, reject) => {
    options.success = r => {
      if (r.statusCode !== 200) return reject(new Error('作品下载失败，请刷新后重试'))
      const method = kind === 'video' ? 'saveVideoToPhotosAlbum' : 'saveImageToPhotosAlbum'
      if (typeof wx[method] !== 'function') return reject(new Error('当前微信版本不支持保存到相册'))
      wx[method]({ filePath: r.filePath || r.tempFilePath, success: resolve, fail: reject })
    }
    options.fail = reject
    wx.downloadFile(options)
  }).then(() => {
    wx.hideLoading()
    if (wx.vibrateShort) wx.vibrateShort({ type: 'light' })
    wx.showToast({ title: '已保存到相册' })
  }).catch(error => {
    wx.hideLoading()
    const msg = (error && (error.errMsg || error.message)) || ''
    if (/scope is not declared/i.test(msg)) {
      wx.showModal({
        title: '暂时无法保存',
        content: '当前版本还缺少相册权限声明。图片可以长按直接保存；视频保存功能会在下一个版本恢复。',
        showCancel: false,
        confirmText: '知道了'
      })
    } else if (/auth|deny|denied/.test(msg)) {
      wx.showModal({ title: '需要相册权限', content: '允许保存到相册后，再次点击保存。', confirmText: '去设置', success: r => { if (r.confirm) wx.openSetting({}) } })
    } else if (kind === 'video' && /invalid|decode|format|support|not support/i.test(msg)) {
      wx.showModal({ title: '视频暂时无法保存', content: '部分机型可能无法直接保存该视频，可在预览播放后尝试手动保存。', showCancel: false, confirmText: '知道了' })
    } else {
      toast(error)
    }
  })
}

function saveMedia(url, kind) {
  if (!url) return Promise.resolve()
  if (typeof wx.requirePrivacyAuthorize !== 'function') return saveMediaNow(url, kind)
  return new Promise(resolve => {
    wx.requirePrivacyAuthorize({
      success: () => resolve(saveMediaNow(url, kind)),
      fail: () => {
        wx.showModal({
          title: '需要同意隐私保护指引',
          content: '请先阅读并同意《用户隐私保护指引》，再点击保存。',
          showCancel: false,
          confirmText: '知道了'
        })
        resolve()
      }
    })
  })
}

module.exports = { layout, home, back, open, toast, message, projectView, saveMedia, time }
