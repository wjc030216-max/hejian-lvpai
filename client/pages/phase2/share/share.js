const api = require('../../../utils/phase2Api')
const ui = require('../../../utils/phase2Ui')
Page({
  data: { layout: ui.layout(), item: null, error: '', loading: true, saving: false },
  onLoad(options) { this.pid = options.id || '' },
  onShow() { this.active = true; this.load() },
  onHide() { this.active = false }, onUnload() { this.active = false },
  async load() {
    if (!this.pid) return this.setData({ loading: false, error: '分享内容不存在' })
    this.setData({ loading: true, error: '' })
    try {
      const item = await api.getShare(this.pid)
      if (!this.active) return
      this.setData({ item, loading: false })
    } catch (error) {
      if (this.active) this.setData({ error: ui.message(error), loading: false })
    }
  },
  preview() { const url = this.data.item && this.data.item.portrait_url; if (url) wx.previewImage({ current: url, urls: [url] }) },
  async save(e) {
    if (this.data.saving) return
    this.setData({ saving: true })
    const kind = e.currentTarget.dataset.kind
    const item = this.data.item
    try {
      await ui.saveMedia(kind === 'video' ? item.video_url : item.portrait_url, kind)
    } catch (error) {
      // ui.saveMedia already presents the actionable permission/download error.
    } finally {
      this.setData({ saving: false })
    }
  },
  retry() { this.load() },
  goHome() { ui.home() },
  shoot() {
    const code = this.data.item && this.data.item.template_code
    const scene = this.data.item && this.data.item.location_association
    const params = [code ? 'template=' + encodeURIComponent(code) : '', scene ? 'scene=' + encodeURIComponent(scene) : ''].filter(Boolean)
    wx.reLaunch({ url: '/pages/phase2/entry/entry' + (params.length ? ('?' + params.join('&')) : '') })
  },
  onShareAppMessage() {
    const item = this.data.item || {}
    return { title: (item.video_name || '贺见旅拍 · 旅行电影') + ' · 贺见旅拍', path: '/pages/phase2/share/share?id=' + encodeURIComponent(this.pid), imageUrl: item.share_image_url || item.portrait_url || '' }
  },
  onShareTimeline() {
    const item = this.data.item || {}
    return { title: (item.video_name || '贺见旅拍 · 旅行电影') + ' · 贺见旅拍', query: 'id=' + encodeURIComponent(this.pid), imageUrl: item.share_image_url || item.portrait_url || '' }
  }
})
