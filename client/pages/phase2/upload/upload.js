const api = require('../../../utils/phase2Api')
const state = require('../../../state/phase2State')
const ui = require('../../../utils/phase2Ui')
const payment = require('../../../utils/phase2Payment')
const MAX_ERROR_RETRIES = 6
Page({
  data: { layout: ui.layout(), project: null, roles: [], config: {}, aiEnabled: false, all: false, passed: 0, required: 0, photoCount: 0, remaining: 0, checking: false, rejected: false, multiFigs: [], singleFigs: [], multiDesc: '', singleDesc: '', videoSpecification: '', priceText: '', standardPriceText: '', mode: 'multi', selectedUid: '', solo: false, savingMode: false, consent: true, uploading: false, submitting: false, error: '', loading: true },
  onLoad(options) {
    this.pid = options.id || state.get().projectId
  },
  onShow() {
    this.active = true
    api.getConfig(true).then(config => this.setData({ config, aiEnabled: config.ai_enabled !== false }, () => {
      if (this.data.project) this.update(this.data.project)
    })).catch(error => this.setData({ error: ui.message(error), loading: false }))
    this.poll()
  },
  onHide() { this.active = false; clearTimeout(this.timer) }, onUnload() { this.active = false; clearTimeout(this.timer) },
  update(project) {
    const mode = project.participation_mode || 'multi', selectedUid = project.selected_person_uid || ''
    const roles = project.roles.map(r => ({ ...r, uses_template: mode === 'single' && r.person_uid !== selectedUid, reference: ((project.template.assets || {}).roles || {})[r.character_id] || {} }))
    const required = roles.filter(r => !r.uses_template)
    const count = Math.min(Math.max(roles.length, 2), 3)
    const multiFigs = Array.from({ length: count }, (_, i) => ({ key: i, live: true }))
    const singleFigs = Array.from({ length: count }, (_, i) => ({ key: i, live: i === 0 }))
    const actorCount = String(Number(project.template.actor_count) || roles.length || 1)
    const standardPrice = Number(
      (this.data.config.standard_price_tiers || this.data.config.price_tiers || {})[actorCount] || 0
    )
    const formatPrice = fen => fen ? (fen / 100).toFixed(2).replace(/\.?0+$/, '') : ''
    const quota = this.data.config.free_quota || {}
    const freeRemaining = Number(quota.remaining || 0)
    const freeActive = freeRemaining > 0 &&
      ['wechat', 'allinpay'].includes(this.data.config.payment_mode)
    const cfg = this.data.config || {}
    const paymentDisabled = cfg.payment_enabled === false ||
      ['disabled', 'unconfigured'].includes(cfg.payment_mode)
    const devBypass = cfg.payment_mode === 'dev-bypass'
    const standardPriceText = formatPrice(standardPrice)
    const payAmount = paymentDisabled ? '暂未开放'
      : devBypass ? '测试不扣款'
        : freeActive ? '免费'
          : (standardPriceText ? '¥' + standardPriceText : '暂不可支付')
    const payOriginal = freeActive && standardPriceText ? '¥' + standardPriceText : ''
    const payNote = paymentDisabled ? '支付功能已关闭'
      : devBypass ? '测试模式 · 不扣款'
        : freeActive ? '先到先得' : '安全支付 · 交易保障'
    this.setData({
      project, roles, mode, selectedUid,
      aiEnabled: this.data.config.ai_enabled !== false,
      videoSpecification: project.template.video_specification || '竖屏视频',
      solo: roles.length === 1,
      required: required.length,
      passed: required.filter(r => r.check_status === 'passed').length,
      photoCount: required.filter(r => !!r.photo_url).length,
      remaining: required.filter(r => !r.photo_url).length,
      checking: required.some(r => r.check_status === 'checking'),
      rejected: required.some(r => r.check_status === 'rejected'),
      multiFigs, singleFigs,
      multiDesc: '所有人同时出镜，呈现自然互动与真实氛围',
      singleDesc: '只需一人出镜，其余角色自动使用模板人物',
      standardPriceText, payAmount, payOriginal, payNote,
      freeActive, freeRemaining, freeTotal: Number(quota.total || 0),
      all: required.length > 0 && required.every(r => r.check_status === 'passed'),
      loading: false
    })
  },
  async poll() {
    clearTimeout(this.timer)
    if (!this.active || this.polling || this.data.uploading || this.data.savingMode) return
    if (!this.pid) return this.setData({ error: '请返回首页选择模板', loading: false })
    this.polling = true
    const revision = this.revision || 0
    try {
      const project = await api.getProject(this.pid)
      if (!this.active || this.data.uploading || this.data.savingMode || revision !== (this.revision || 0)) return
      this.errorRetries = 0
      this.update(project); this.setData({ error: '' })
      if (project.order_no && !this.navigating) { this.navigating = true; wx.redirectTo({ url: '/pages/phase2/work/work?id=' + encodeURIComponent(this.pid) }) }
    } catch (error) { this.setData({ error: ui.message(error), loading: false }) }
    finally {
      this.polling = false
      if (this.data.error) this.errorRetries = (this.errorRetries || 0) + 1
      const exhausted = this.data.error && this.errorRetries >= MAX_ERROR_RETRIES
      if (this.active && !exhausted && !this.navigating && (this.data.error || this.data.roles.some(r => !r.uses_template && r.check_status === 'checking'))) {
        const delay = this.data.error ? Math.min(60000, 10000 * (2 ** Math.min(Math.max(this.errorRetries - 1, 0), 2))) : 3000
        this.timer = setTimeout(() => this.poll(), delay)
      }
    }
  },
  manualRefresh() {
    this.errorRetries = 0
    this.poll()
  },
  consent() { this.setData({ consent: !this.data.consent }) },
  async changeParticipation(mode, uid) {
    if (this.data.savingMode || this.data.uploading || this.data.submitting) return
    this.revision = (this.revision || 0) + 1
    this.setData({ savingMode: true }); clearTimeout(this.timer)
    try { this.update(await api.setParticipation(this.pid, mode, uid)) }
    catch (error) { ui.toast(error) }
    finally { this.setData({ savingMode: false }); this.poll() }
  },
  mode(e) { if (e.currentTarget.dataset.mode === this.data.mode) return; this.changeParticipation(e.currentTarget.dataset.mode, '') },
  switchRole(e) { this.changeParticipation('single', e.currentTarget.dataset.uid) },
  choose(e) {
    if (this.data.uploading || this.data.submitting || this.data.savingMode) return
    const index = Number(e.currentTarget.dataset.index)
    const open = () => this.chooseMedia(index)
    if (!this.data.consent) { this.confirmConsent().then(ok => { if (ok) open() }); return }
    open()
  },
  chooseMedia(index) {
    const role = this.data.roles[index]
    if (!role) return
    wx.chooseMedia({ count: 1, mediaType: ['image'], sizeType: ['original'], sourceType: ['album', 'camera'], success: async result => {
      this.setData({ uploading: true, all: false }); clearTimeout(this.timer)
      try {
        if (this.data.mode === 'single' && role.uses_template) {
          await api.setParticipation(this.pid, 'single', role.person_uid)
        }
        await api.uploadRolePhoto(this.pid, role.person_uid, result.tempFiles[0].tempFilePath)
        this.update(await api.getProject(this.pid))
        if (wx.vibrateShort) wx.vibrateShort({ type: 'light' })
      }
      catch (error) { ui.toast(error) }
      finally { this.setData({ uploading: false }); this.poll() }
    }, fail: error => { if (!/cancel/.test(error.errMsg || '')) ui.toast(error) } })
  },
  async next() {
    if (this.nextBusy || this.data.submitting || this.navigating || this.data.savingMode) return
    if (!this.data.aiEnabled) return ui.toast('AI制作服务未启用，当前不能开始制作')
    if (!this.data.all || this.data.uploading) return ui.toast('请等待全部照片检查通过')
    this.nextBusy = true
    try {
      if (!this.data.consent) {
        const ok = await this.confirmConsent()
        if (!ok) return
      }
      let subscribe = false
      const templateId = this.data.config.subscribe_template_id || api.SUBSCRIBE_TEMPLATE_ID
      if (templateId) {
        try {
          const result = await new Promise((resolve, reject) => {
            wx.requestSubscribeMessage({ tmplIds: [templateId], success: resolve, fail: reject })
          })
          subscribe = result[templateId] === 'accept'
        } catch (error) {
          subscribe = false
        }
      }
      await this.doNext(subscribe)
    } finally {
      this.nextBusy = false
    }
  },
  async doNext(subscribe) {
    this.setData({ submitting: true })
    try {
      if (this.data.config.payment_enabled === false) {
        throw new Error('支付暂未开放')
      }
      const order = await api.createOrder(
        this.pid, true, subscribe, this.data.config.agreement_version || '')
      if (['wechat', 'allinpay'].includes(order.payment_mode)) {
        await payment.pay(this.pid)
        if (order.payment_mode === 'allinpay') {
          if (this.active) this.poll()
          return
        }
      }
      this.navigating = true
      wx.redirectTo({ url: '/pages/phase2/work/work?id=' + encodeURIComponent(this.pid) })
    }
    catch (error) { ui.toast(error) } finally { this.setData({ submitting: false }) }
  },
  confirmConsent() {
    return new Promise(resolve => {
      wx.showModal({
        title: '照片授权与制作说明',
        content: this.data.config.agreement_text || '使用照片即代表你已阅读并同意：照片属于本人或已取得授权，将用于人脸检查、定妆照和视频生成，并保存本次作品。',
        confirmText: '同意并继续',
        cancelText: '再看看',
        success: res => { if (res.confirm) { this.setData({ consent: true }); resolve(true) } else resolve(false) },
        fail: () => resolve(false)
      })
    })
  },
  details() { wx.showModal({ title: '照片授权与制作说明', content: this.data.config.agreement_text || '仅上传本人或已获得授权的照片，用于人脸检查、定妆照和视频制作，并保存作品。本地测试跳过付款，不扣款；真实生成仍会产生云端接口费用。', showCancel: false }) },
  back() { ui.back() }
})
