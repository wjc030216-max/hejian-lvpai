const state = require('../../../state/phase2State')
const api = require('../../../utils/phase2Api')
const brandLogo = require('../../../utils/brandLogo')
const ui = require('../../../utils/phase2Ui')

function formatHeat(label) {
  const text = String(label || '').trim()
  const match = text.match(/^([\d.]+)(万)?人选择$/)
  if (!match) return text
  const value = Number(match[1])
  if (!Number.isFinite(value)) return text
  if (!match[2] && value < 10000) return '热度 ' + value
  const wan = match[2] ? value : value / 10000
  if (wan >= 10000) {
    const yi = wan / 10000
    return '热度 ' + (Number.isInteger(yi) ? yi : yi.toFixed(1).replace(/\.0$/, '')) + '亿'
  }
  return '热度 ' + (Number.isInteger(wan) ? wan : wan.toFixed(1).replace(/\.0$/, '')) + '万'
}

const WINDOW = 9
const MARGIN = 4

Page({
  data: {
    logoSrc: brandLogo,
    layout: ui.layout(), feedItems: [], swiperCurrent: 0,
    current: 0, selected: null,
    total: 0, loading: false, error: '', moreError: '', nextOffset: null,
    gridItems: [], gridTotal: 0, gridNextOffset: null, gridLoading: false, gridError: '',
    gridFilters: {},
    muted: true, playing: false, active: true, drawer: '',
    peopleOptions: ['不限', '1人', '2人', '3人'], peopleValue: 0,
    genderOptions: ['不限'], genderValue: 0,
    relationOptions: ['不限'], relationValue: 0,
    creating: false, unreadCount: 0, unreadAlert: false, guideVisible: false, costLabel: '', priceText: '', swipeHint: false
  },

  onLoad(options) {
    const templateCode = (options || {}).template || ''
    if (templateCode) {
      // 分享进入：按模板 code 精确定位。
      this.deepTemplate = templateCode
    } else {
      this.deepTemplate = ''
    }
    this.catalog = []
    this.windowStart = 0
    const swipeHint = !state.swipeHintSeen()
    this.setData({ guideVisible: !state.guideSeen(), swipeHint })
    this.fetch(true)
    api.getConfig().then(config => {
      const amount = Number(config.amount_fen || 0)
      const price = amount ? (amount / 100).toFixed(2).replace(/\.?0+$/, '') : ''
      const disabled = ['disabled', 'unconfigured'].includes(config.payment_mode)
      const quota = config.free_quota || {}
      const freeRemaining = Number(quota.remaining || 0)
      const freeActive = !disabled && freeRemaining > 0 &&
        ['wechat', 'allinpay'].includes(config.payment_mode)
      const costLabel = disabled ? '支付暂未开放'
        : config.payment_mode === 'dev-bypass' ? '开发测试 · 不扣款'
          : freeActive ? ('今日免费 · 剩 ' + freeRemaining + ' 个名额')
            : (price ? ('¥' + price + ' · 约几分钟') : '')
      this.setData({ costLabel, priceText: disabled ? '暂未开放' : config.payment_mode === 'dev-bypass' ? '测试不扣款' : freeActive ? '免费' : (price ? '¥' + price : '') })
    }).catch(() => {})
  },

  logoError(event) { console.error('logo load failed', event && event.detail) },
  newRequestId() { return 'req_' + Date.now() + '_' + Math.random().toString(16).slice(2) },
  onShow() { this.active = true; this.setData({ active: true }, () => this.autoplay()); this.loadUnread() },
  onHide() { this.active = false; clearTimeout(this.playTimer); this.setData({ active: false, playing: false }); this.persist() },
  onUnload() { this.active = false; this.sequence = (this.sequence || 0) + 1; clearTimeout(this.swipeHintTimer); clearTimeout(this.playTimer) },
  persist() { state.saveBrowse({ index: this.data.current }) },
  loadUnread() {
    api.getMessages().then(result => {
      const items = (result && result.items) || []
      this.setData({ unreadCount: (result && result.total) || 0, unreadAlert: items.some(item => item.kind === 'failure') })
    }).catch(() => {})
  },
  dismissGuide() { state.markGuideSeen(); this.setData({ guideVisible: false }) },
  dismissSwipeHint() {
    if (!this.data.swipeHint) return
    clearTimeout(this.swipeHintTimer)
    state.markSwipeHintSeen()
    this.setData({ swipeHint: false })
  },

  async fetch(reset) {
    if (!reset && (this.data.loading || this.data.nextOffset === null)) return Promise.resolve()
    const sequence = this.sequence = (this.sequence || 0) + 1
    const offset = reset ? 0 : this.data.nextOffset
    const deep = reset ? this.deepTemplate : ''
    this.setData({ loading: true, error: '', moreError: '', ...(reset ? { playing: false } : {}) })
    try {
      const result = await api.listTemplates({ offset, limit: 8 })
      if (sequence !== this.sequence) return
      let items = (reset ? result.items : (this.catalog || []).concat(result.items))
        .map(item => ({ ...item, heatText: formatHeat(item.heat_label) }))
      if (deep) {
        let at = items.findIndex(x => x.template_code === deep)
        if (at < 0) {
          try {
            const t = await api.getTemplate(deep)
            if (sequence !== this.sequence) return
            if (t && t.template_code) {
              items = [{ ...t, heatText: formatHeat(t.heat_label) }].concat(items)
              at = 0
            }
          } catch (error) { /* 模板不存在时保持原列表 */ }
        }
      }
      this.catalog = items
      let current = reset ? 0 : this.data.current
      if (deep) {
        const at = items.findIndex(x => x.template_code === deep)
        if (at >= 0) current = at
      }
      if (deep) this.deepTemplate = ''
      const size = Math.min(items.length, WINDOW)
      this.windowStart = Math.max(0, Math.min(current - MARGIN, items.length - size))
      const feedItems = items.slice(this.windowStart, this.windowStart + size)
      const item = items[current] || null
      this.setData({ current, swiperCurrent: current - this.windowStart, selected: item, playing: false, videoError: false, total: result.total, nextOffset: result.next_offset, feedItems }, () => {
        if (reset) this.autoplay()
      })
      this.persist()
    } catch (error) {
      if (sequence === this.sequence) { if (reset) this.catalog = []; this.setData(reset ? { error: ui.message(error), feedItems: [], selected: null } : { moreError: ui.message(error) }) }
    } finally {
      if (sequence === this.sequence) this.setData({ loading: false })
    }
  },

  fetchGrid(reset) {
    if (!reset && (this.data.gridLoading || this.data.gridNextOffset === null)) return Promise.resolve()
    const sequence = this.gridSequence = (this.gridSequence || 0) + 1
    const offset = reset ? 0 : this.data.gridNextOffset
    this.setData({ gridLoading: true, gridError: '' })
    return api.listTemplates({ ...this.data.gridFilters, offset, limit: 24 }).then(result => {
      if (sequence !== this.gridSequence) return
      const items = reset ? result.items : this.data.gridItems.concat(result.items)
      this.gridFacets = result.facets
      this.setData({ gridItems: items, gridTotal: result.total, gridNextOffset: result.next_offset })
      this.updateFilterOptions()
    }).catch(error => {
      if (sequence === this.gridSequence) this.setData({ gridError: ui.message(error) })
    }).finally(() => { if (sequence === this.gridSequence) this.setData({ gridLoading: false }) })
  },

  retry() { this.fetch(true) },
  loadMore() { this.fetchGrid(false) },

  slide(e) {
    if (e.detail.source === '') return
    const rel = Number(e.detail.current)
    const items = this.catalog || []
    const index = (this.windowStart || 0) + rel
    if (index < 0 || index >= items.length || index === this.data.current) return
    this.focus(index)
    if (index >= items.length - 2 && this.data.nextOffset !== null) this.fetch(false)
  },

  play() { if (!this.data.selected || !this.data.selected.example) return ui.toast('此模板还没有示例视频'); this.setData({ playing: !this.data.playing, videoError: false }) },
  autoplay() { this.setData({ playing: !!(this.data.active && !this.data.drawer && this.data.selected && this.data.selected.example), videoError: false }) },
  videoFailed() { this.setData({ playing: false, videoError: true }) },
  mute() { this.setData({ muted: !this.data.muted }) },

  prev() {
    if (this.data.drawer) return
    const items = this.catalog || []
    if (this.data.current > 0 && items.length) this.focus(this.data.current - 1)
  },

  next() {
    if (this.data.drawer) return
    const items = this.catalog || []
    if (this.data.current < items.length - 1) this.focus(this.data.current + 1)
    else if (this.data.nextOffset !== null && !this.data.loading) this.fetch(false)
  },

  focus(index) {
    const items = this.catalog || []
    if (!items.length) return
    if (index < 0) index = 0
    if (index >= items.length) index = items.length - 1
    const size = Math.min(items.length, WINDOW)
    let start = this.windowStart || 0
    if (start >= items.length || index < start + MARGIN || index >= start + size - MARGIN) {
      start = Math.max(0, Math.min(index - MARGIN, items.length - size))
    }
    const shifted = start !== this.windowStart
    this.windowStart = start
    const selected = items[index]
    this.dismissSwipeHint()
    const shouldPlay = !!(this.data.active && !this.data.drawer && selected.example)
    const patch = {
      current: index,
      swiperCurrent: index - start,
      selected,
      videoError: false,
      playing: false
    }
    if (shifted) patch.feedItems = items.slice(start, start + size)
    this.setData(patch)
    clearTimeout(this.playTimer)
    this.playTimer = setTimeout(() => {
      if (!this.active || this.data.drawer) return
      if (this.data.current !== index || !shouldPlay) return
      this.setData({ playing: true })
    }, 280)
    this.persist()
  },

  updateFilterOptions() {
    const facets = this.gridFacets || {}
    const people = ['不限'].concat((facets.actor_count && facets.actor_count.length)
      ? facets.actor_count.filter(v => ['1', '2', '3'].includes(String(v))).map(v => v + '人')
      : ['1人', '2人', '3人'])
    const gender = ['不限'].concat((facets.gender || []).filter(v => v && v !== '不限'))
    const relation = ['不限'].concat((facets.relationship || []).filter(v => v && v !== '不限'))
    const peopleValue = Math.max(0, people.indexOf(this.data.gridFilters.actor_count ? this.data.gridFilters.actor_count + '人' : '不限'))
    const genderValue = Math.max(0, gender.indexOf(this.data.gridFilters.gender || '不限'))
    const relationValue = Math.max(0, relation.indexOf(this.data.gridFilters.relationship || '不限'))
    this.setData({ peopleOptions: people, peopleValue, genderOptions: gender, genderValue, relationOptions: relation, relationValue })
  },

  onPeoplePick(e) {
    const index = Number(e.detail.value)
    const people = this.data.peopleOptions[index] || '不限'
    const actor_count = people === '不限' ? '' : people.replace('人', '')
    this.setData({ 'gridFilters.actor_count': actor_count, 'gridFilters.gender': '', 'gridFilters.relationship': '', peopleValue: index, genderValue: 0, relationValue: 0 })
    this.fetchGrid(true)
  },

  onGenderPick(e) {
    const index = Number(e.detail.value)
    const val = this.data.genderOptions[index] === '不限' ? '' : this.data.genderOptions[index]
    this.setData({ 'gridFilters.gender': val, genderValue: index })
    this.fetchGrid(true)
  },

  onRelationPick(e) {
    const index = Number(e.detail.value)
    const val = this.data.relationOptions[index] === '不限' ? '' : this.data.relationOptions[index]
    this.setData({ 'gridFilters.relationship': val, relationValue: index })
    this.fetchGrid(true)
  },

  openMore() { this.setData({ drawer: 'more', playing: false }); if (!this.data.gridItems.length && !this.data.gridLoading) this.fetchGrid(true) },
  clearGrid() { this.setData({ gridFilters: {}, peopleValue: 0, genderValue: 0, relationValue: 0 }); this.fetchGrid(true) },
  closeDrawer() { this.setData({ drawer: '' }, () => this.autoplay()) },
  noop() {},

  async selectCard(e) {
    const code = e.currentTarget.dataset.code
    const inFeed = (this.catalog || []).find(x => x.template_code === code)
    this.setData({ drawer: '', playing: false })
    if (inFeed) {
      this.focus(this.catalog.indexOf(inFeed))
      return
    }
    const card = this.data.gridItems.find(x => x.template_code === code)
    if (!card) return
    await this.fetch(true)
    let item = (this.catalog || []).find(x => x.template_code === code)
    if (!item) {
      try {
        item = await api.getTemplate(code)
        if (item) {
          this.catalog = [item].concat(this.catalog || [])
          this.windowStart = 0
          this.setData({ feedItems: this.catalog.slice(0, WINDOW) })
        }
      } catch (error) {
        ui.toast('模板暂时无法打开')
        return
      }
    }
    if (item) this.focus(this.catalog.indexOf(item))
  },

  async shoot() {
    const item = this.data.selected
    if (!item || this.data.creating || this.data.loading) return
    if (wx.vibrateShort) wx.vibrateShort({ type: 'light' })
    this.setData({ creating: true, playing: false }); this.persist()
    try {
      let draft = state.draft(item.template_code), project
      if (draft && draft.projectId && draft.version === item.version) {
        try { project = await api.getProject(draft.projectId) } catch (error) { if (error.statusCode !== 404) throw error }
        if (project && (project.order_no || project.status === 'expired')) {
          if (project.status === 'expired') draft = { version: item.version, requestId: this.newRequestId() }
          project = null
        }
      }
      if (!project) {
        const requestId = draft && !draft.projectId && draft.version === item.version ? draft.requestId : this.newRequestId()
        state.saveDraft(item.template_code, { version: item.version, requestId })
        project = await api.createProject(item.template_code, item.version, requestId)
      }
      state.saveDraft(item.template_code, { projectId: project.project_id, version: item.version })
      state.save({ projectId: project.project_id })
      ui.open('upload', project.project_id)
    } catch (error) { ui.toast(error) } finally { this.setData({ creating: false }) }
  },

  goWorks() { ui.open('works') },
  onShareAppMessage() {
    const item = this.data.selected
    return { title: item ? item.video_name + ' · 贺见旅拍' : '贺见旅拍 · 旅行电影',
      path: '/pages/phase2/entry/entry' + (item ? '?template=' + encodeURIComponent(item.template_code) : '') }
  },
  onShareTimeline() {
    const item = this.data.selected
    return { title: item ? item.video_name + ' · 贺见旅拍' : '贺见旅拍 · 旅行电影',
      query: item ? 'template=' + encodeURIComponent(item.template_code) : '' }
  }
})
