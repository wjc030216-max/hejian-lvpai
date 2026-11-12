const api = require('../../../utils/phase2Api')
const ui = require('../../../utils/phase2Ui')
const state = require('../../../state/phase2State')
const MAX_ERROR_RETRIES = 6
function decorate(item) {
  const t = item.template || {}
  return {
    ...item,
    durationText: t.video_specification || '竖屏视频',
    portraitLabel: item.portrait_url ? '高清 · 可保存' : item.portraitText,
    videoLabel: item.video_url ? (t.video_specification ? t.video_specification + ' · 可播放' : '竖屏 · 可播放') : item.videoText
  }
}
Page({
  data: { layout: ui.layout(), items: [], error: '', loading: false, saving: false, nextOffset: null },
  onShow() { this.active = true; this.load() }, onHide() { this.active = false; clearTimeout(this.timer) }, onUnload() { this.active = false; clearTimeout(this.timer) },
  onReachBottom() { this.more() },
  more() { if (this.data.nextOffset !== null && !this.polling) this.load(true) },
  schedule() {
    clearTimeout(this.timer)
    if (this.data.error) this.errorRetries = (this.errorRetries || 0) + 1
    const exhausted = this.data.error && this.errorRetries >= MAX_ERROR_RETRIES
    if (this.active && !exhausted && (this.data.error || this.data.items.some(item => !item.terminal || item.status === 'refund_pending'))) {
      const delay = this.data.error ? Math.min(60000, 15000 * (2 ** Math.min(Math.max(this.errorRetries - 1, 0), 2))) : 15000
      this.timer = setTimeout(() => this.refreshLoaded(), delay)
    }
  },
  async refreshLoaded() {
    if (!this.active || this.polling) return
    this.polling = true
    try {
      const ids = this.data.items.filter(item => !item.terminal || item.status === 'refund_pending').map(item => item.project_id)
      const updated = new Map()
      for (let i = 0; i < ids.length && this.active; i += 30) {
        const result = await api.getWorksStatus(ids.slice(i, i + 30))
        result.items.forEach(item => updated.set(item.project_id, decorate(ui.projectView(item))))
      }
      if (this.active) { this.errorRetries = 0; this.setData({ items: this.data.items.map(item => updated.get(item.project_id) || item), error: '' }) }
    } catch (error) { if (this.active) this.setData({ error: ui.message(error) }) }
    finally { this.polling = false; this.schedule() }
  },
  async load(append = false) {
    append = append === true
    if (this.polling) return; clearTimeout(this.timer); this.polling = true; this.setData({ loading: true })
    try {
      const result = await api.listWorks(append ? this.data.nextOffset : 0)
      if (this.active) {
        const merged = new Map((append ? this.data.items : []).map(item => [item.project_id, item]))
        result.items.map(ui.projectView).map(decorate).forEach(item => merged.set(item.project_id, item))
        this.errorRetries = 0
        this.setData({ items: Array.from(merged.values()), nextOffset: result.next_offset, error: '' })
      }
    }
    catch (error) { this.setData({ error: ui.message(error) }) }
    finally { this.polling = false; this.setData({ loading: false }); this.schedule() }
  },
  open(e) { const id = e.currentTarget.dataset.id; state.save({ projectId: id }); ui.open('work', id) },
  manualRefresh() {
    this.errorRetries = 0
    this.load()
  },
  imgLoaded(e) {
    const id = e.currentTarget.dataset.id
    const kind = e.currentTarget.dataset.kind
    const index = this.data.items.findIndex(item => item.project_id === id)
    if (index < 0) return
    this.setData({ ['items[' + index + '].' + (kind === 'video' ? 'vReady' : 'pReady')]: true })
  },
  preview(e) { const url = e.currentTarget.dataset.url; if (url) wx.previewImage({ current: url, urls: [url] }) },
  async save(e) {
    if (this.data.saving) return
    this.setData({ saving: true })
    try {
      await ui.saveMedia(e.currentTarget.dataset.url, e.currentTarget.dataset.kind)
    } catch (error) {
      // ui.saveMedia already presents the actionable permission/download error.
    } finally {
      this.setData({ saving: false })
    }
  },
  back() { ui.back() }, again() { ui.home() },
  onShareAppMessage(e) {
    const id = e && e.target && e.target.dataset && e.target.dataset.id
    const item = id ? (this.data.items || []).find(x => x.project_id === id) : null
    if (item && item.portrait_url) {
      return { title: (item.video_name || '我的旅行电影') + ' · 贺见旅拍', path: '/pages/phase2/share/share?id=' + encodeURIComponent(id), imageUrl: item.share_image_url || item.portrait_url || '' }
    }
    return { title: '贺见旅拍 · 旅行电影', path: '/pages/phase2/entry/entry' }
  },
  onShareTimeline() { return { title: '贺见旅拍 · 旅行电影' } }
})
