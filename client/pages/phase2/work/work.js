const api = require('../../../utils/phase2Api')
const ui = require('../../../utils/phase2Ui')
const state = require('../../../state/phase2State')
const payment = require('../../../utils/phase2Payment')
const MAX_ERROR_RETRIES = 6

function decodeBase64Url(value) {
  const source = String(value || '').replace(/-/g, '+').replace(/_/g, '/')
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
  let bits = 0, buffer = 0, output = ''
  for (let i = 0; i < source.length; i++) {
    const digit = alphabet.indexOf(source[i])
    if (digit < 0) continue
    buffer = (buffer << 6) | digit
    bits += 6
    if (bits >= 8) {
      bits -= 8
      output += String.fromCharCode((buffer >> bits) & 0xff)
      buffer &= (1 << bits) - 1
    }
  }
  return output
}

function mediaUrlNeedsRefresh(url) {
  const match = String(url || '').match(/[?&]access=([^&]+)/)
  if (!match) return false
  try {
    const claims = JSON.parse(decodeBase64Url(match[1].split('.')[1]))
    return !claims.exp || claims.exp * 1000 <= Date.now() + 60000
  } catch (error) {
    return true
  }
}

function buildSteps(p, view) {
  const paid = !['draft', 'uploading', 'pending_payment'].includes(p.status)
  const portraitDone = !!p.portrait_url
  const videoDone = !!p.video_url
  const failed = ['terminated', 'refund_pending', 'refund_failed', 'refunded'].includes(p.status)
  const portraitPhase = ['portrait_submit', 'portrait_unknown', 'portrait_processing', 'portrait_saving'].includes(p.status)
  const videoPhase = ['video_submit', 'video_unknown', 'video_processing'].includes(p.status)
  const steps = [
    { label: '支付', short: '支付', sub: paid ? (view.timeText || '已完成') : '待支付', done: paid, active: !paid, failed: false, icon: paid ? 'check' : 'dot' },
    { label: '定妆照', short: '定妆照', sub: portraitDone ? '已完成' : portraitPhase ? '制作中' : (failed ? '未完成' : '待开始'), done: portraitDone, active: portraitPhase && !failed, failed: failed && !portraitDone, icon: portraitDone ? 'check' : (portraitPhase ? 'photo' : 'dot') },
    { label: '旅行视频', short: '旅行视频', sub: videoDone ? '已完成' : videoPhase ? '制作中' : (failed ? '未完成' : '待开始'), done: videoDone, active: videoPhase && !failed, failed: failed && !videoDone, icon: videoDone ? 'check' : (videoPhase ? 'sparkle' : 'play') }
  ]
  return steps.map((step, index) => ({
    ...step,
    linked: index > 0 && steps[index - 1].done,
  }))
}
const STAGE_TEXT = {
  pending_payment: '等待支付',
  portrait_submit: '生成定妆照',
  portrait_unknown: '生成定妆照',
  portrait_processing: '生成定妆照',
  portrait_saving: '保存定妆照',
  video_submit: '生成旅行视频',
  video_unknown: '生成旅行视频',
  video_processing: '生成旅行视频',
  needs_review: '任务待核实',
  completed: '已完成',
  archived: '历史作品',
  terminated: '制作未完成',
  refund_pending: '退款处理中',
  refund_failed: '退款失败',
  refunded: '已退款 · 已归档',
  expired: '数据保留期已结束'
}
const GENERATING = ['portrait_submit', 'portrait_unknown', 'portrait_processing', 'portrait_saving', 'video_submit', 'video_unknown', 'video_processing', 'needs_review']
function decorate(view, project) {
  const uncertain = project.status === 'needs_review'
  const aiDisabled = project.ai_enabled === false && GENERATING.includes(project.status) && !uncertain
  const generating = GENERATING.includes(project.status) && !uncertain && !aiDisabled
  const portraitPhase = ['portrait_submit', 'portrait_unknown', 'portrait_processing', 'portrait_saving'].includes(project.status)
  const videoPhase = ['video_submit', 'video_unknown', 'video_processing'].includes(project.status)
  const videoProgress = project.video_progress || {}
  const segmentTotal = Number(videoProgress.total || 0)
  const segmentCompleted = Number(videoProgress.completed || 0)
  view.videoStageDetail = segmentTotal > 1
    ? (videoProgress.phase === 'composing' ? '正在合成完整成片' : '已生成 ' + segmentCompleted + '/' + segmentTotal + ' 段')
    : ''
  view.generating = generating
  view.aiBlocked = aiDisabled
  view.stageText = aiDisabled ? '制作服务未启用' : (STAGE_TEXT[project.status] || '准备中')
  if (uncertain) view.subtitle = '任务状态待核实，请稍后刷新；如长时间未更新，可联系客服处理。'
  else if (aiDisabled) view.subtitle = 'AI制作服务未启用，本次不会继续生成'
  else if (view.unpaid) view.subtitle = '完成支付后开始生成，预计需要 2–5 分钟'
  else if (view.video_url) view.subtitle = '定妆照和旅行视频已生成，可保存或分享'
  else if (project.status === 'refund_pending') view.subtitle = '退款已提交，系统正在处理全额原路退款'
  else if (project.status === 'refund_failed') view.subtitle = '退款未完成，请联系客服核对通联退款结果'
  else if (project.status === 'refunded') view.subtitle = '全额退款已完成，作品已归档，不能播放、保存或分享'
  else if (project.status === 'expired') view.subtitle = '用户过程数据、提示词、输入图片和生成媒体已按 90 天策略清理'
  else if (view.terminal) view.subtitle = '本次制作未完成'
  else view.subtitle = '我们正在为你生成定妆照和旅行视频，预计需要 ' + (segmentTotal > 1 ? '5–8' : '2–5') + ' 分钟'
  view.etaText = aiDisabled ? '无法生成' : view.video_url ? '已完成' : view.unpaid ? '待支付' : project.status === 'refunded' ? '已退款' : project.status === 'expired' ? '已清理' : project.status === 'refund_pending' ? '退款中' : uncertain ? '待核实' : view.terminal ? '需处理' : portraitPhase ? '预计 1–2 分钟' : videoPhase ? (segmentTotal > 1 ? '预计 5–8 分钟' : '预计 3–5 分钟') : '制作中'
  view.etaLine = aiDisabled ? 'AI制作服务未启用，不会继续请求生成任务' : portraitPhase ? '正在制作定妆照，预计需要 1–2 分钟，请稍候' : videoPhase ? ('正在制作旅行视频' + (view.videoStageDetail ? ' · ' + view.videoStageDetail : '') + '，请稍候') : ''
  if (project.status === 'pending_payment') {
    view.focusTitle = '等待支付'
    view.focusEta = '支付后开始制作'
    view.focusDesc = '完成支付后，系统会自动生成定妆照和旅行视频'
  } else if (project.status === 'refunded') {
    view.focusTitle = '退款已完成'
    view.focusEta = '作品已归档'
    view.focusDesc = '退款成功后可重新选择模板制作'
  } else if (project.status === 'expired') {
    view.focusTitle = '数据保留期已结束'
    view.focusEta = '作品已清理'
    view.focusDesc = '可重新选择模板，开始新的旅拍'
  } else if (project.status === 'refund_pending') {
    view.focusTitle = '退款处理中'
    view.focusEta = '正在原路退回'
    view.focusDesc = '退款完成后会更新作品状态'
  } else if (project.status === 'refund_failed') {
    view.focusTitle = '退款未完成'
    view.focusEta = '需要人工核对'
    view.focusDesc = project.error || '请联系客服核对退款流水'
  } else if (project.status === 'completed' || project.status === 'archived') {
    view.focusTitle = '作品已生成'
    view.focusEta = '可以保存和分享'
    view.focusDesc = '点击作品卡上的保存按钮，或分享给家人朋友'
  } else if (project.status === 'terminated') {
    view.focusTitle = '制作未完成'
    view.focusEta = '可选择重试或退款'
    view.focusDesc = project.error || '重新生成会保留当前照片'
  } else if (uncertain) {
    view.focusTitle = '正在核实任务'
    view.focusEta = '请稍后刷新'
    view.focusDesc = '长时间没有更新可联系客服处理'
  } else if (aiDisabled) {
    view.focusTitle = '制作服务未启用'
    view.focusEta = '本次无法生成'
    view.focusDesc = 'AI制作服务当前未启用，任务已停止，不会继续自动重试'
  } else if (portraitPhase) {
    view.focusTitle = '正在制作定妆照'
    view.focusEta = '预计需要 1–2 分钟'
    view.focusDesc = '我们正在努力生成中，请稍候…'
  } else if (videoPhase) {
    view.focusTitle = '正在制作旅行视频'
    view.focusEta = segmentTotal > 1 ? '预计需要 5–8 分钟' : '预计需要 3–5 分钟'
    view.focusDesc = view.videoStageDetail || '定妆照已完成，视频正在生成…'
  } else {
    view.focusTitle = '正在准备作品'
    view.focusEta = '即将开始制作'
    view.focusDesc = '照片检查完成后会自动继续'
  }
  view.portraitCardText = aiDisabled ? '服务未启用' : project.status === 'expired' ? '已清理' : project.status === 'refunded' ? '已归档' : view.portrait_url ? '已完成' : view.unpaid ? '等待支付' : uncertain ? '待核实' : portraitPhase ? '正在制作中…' : '等待生成'
  view.videoCardText = aiDisabled ? '服务未启用' : project.status === 'expired' ? '已清理' : project.status === 'refunded' ? '已归档' : view.video_url ? '已完成' : view.unpaid ? '等待支付' : uncertain ? '待核实' : videoPhase ? '正在制作中…' : view.portrait_url ? '将自动生成' : '等待定妆照完成'
  view.portraitOverlay = aiDisabled ? '未生成' : project.status === 'expired' ? '已清理' : project.status === 'refunded' ? '已归档' : view.portrait_url ? '' : view.unpaid ? '等待支付' : uncertain ? '待核实' : portraitPhase ? '定妆照生成中' : view.terminal ? '未生成' : '等待生成'
  view.videoOverlay = aiDisabled ? '未生成' : project.status === 'expired' ? '已清理' : project.status === 'refunded' ? '已归档' : view.video_url ? '' : view.unpaid ? '等待支付' : uncertain ? '待核实' : videoPhase ? '旅行视频生成中' : view.terminal ? '未生成' : portraitPhase ? '等待定妆照完成' : '将自动生成'
  const refundState = ['refund_pending', 'refund_failed', 'refunded'].includes(project.status)
  const expiredState = project.status === 'expired'
  view.portraitStatusText = aiDisabled ? '服务未启用' : expiredState ? '已清理' : project.status === 'refunded' ? '已归档' : refundState ? '不生成' : view.portrait_url ? '已完成' : uncertain ? '待核实' : portraitPhase ? '生成中 · 约 2 分钟' : view.unpaid ? '等待支付' : '等待生成'
  view.videoStatusText = aiDisabled ? '服务未启用' : expiredState ? '已清理' : project.status === 'refunded' ? '已归档' : refundState ? '不生成' : view.video_url ? '已完成' : uncertain ? '待核实' : videoPhase ? ('生成中 · ' + (view.videoStageDetail || '约 3–5 分钟')) : generating ? '等待中' : view.unpaid ? '等待支付' : '等待生成'
  view.portraitFoot = expiredState ? '数据保留期已结束' : project.status === 'refunded' ? '作品已归档' : refundState ? '生成失败，正在处理全额退款' : view.portrait_url ? '已完成 · 可保存和分享' : uncertain ? '任务状态待核实' : portraitPhase ? '正在为你生成高质量定妆照…' : view.unpaid ? '完成支付后开始生成' : '本次未生成'
  view.videoFoot = expiredState ? '数据保留期已结束' : project.status === 'refunded' ? '作品已归档' : refundState ? '生成失败，正在处理全额退款' : view.video_url ? '已完成 · 可保存和分享' : uncertain ? '任务状态待核实' : videoPhase ? '即将开始生成，敬请等待…' : generating ? '等待定妆照完成后开始' : view.unpaid ? '完成支付后开始生成' : '本次未生成'
  view.portraitOverlay = expiredState ? '已清理' : project.status === 'refunded' ? '已归档' : refundState ? '退款处理中' : view.portrait_url ? '' : uncertain ? '待核实' : portraitPhase ? '定妆照生成中' : view.unpaid ? '等待支付' : '等待生成'
  view.videoOverlay = expiredState ? '已清理' : project.status === 'refunded' ? '已归档' : refundState ? '退款处理中' : view.video_url ? '' : uncertain ? '待核实' : videoPhase ? '旅行视频生成中' : view.portrait_url ? '等待定妆照完成' : '等待生成'
  view.portraitPct = view.portrait_url ? 100 : portraitPhase ? 45 : 0
  view.videoPct = view.video_url ? 100 : videoPhase ? 80 : 0
  let progress = Number(view.progress || 0)
  if (aiDisabled) progress = 0
  else if (portraitPhase && !view.portrait_url) progress = Math.max(progress, 45)
  else if (videoPhase) progress = Math.max(
    progress,
    segmentTotal > 1 ? Math.min(95, 78 + Math.round(17 * segmentCompleted / segmentTotal)) : 80
  )
  if (view.video_url) progress = 100
  view.progress = progress
  view.progressLabel = view.video_url || project.status === 'completed' ? '100%' : aiDisabled ? '已停止' : generating ? ('约 ' + progress + '%') : (progress + '%')
  if (aiDisabled) view.statusKind = 'failed'
  return view
}
Page({
  data: { layout: ui.layout(), project: null, config: { payment_enabled: false }, error: '', loading: true, saving: false, paying: false, confetti: [], showConfetti: false },
  onLoad(options) {
    this.pid = options.id || state.get().projectId
    if (api.getConfig) {
      api.getConfig().then(config => { if (this.active || this.pid) this.setData({ config }) }).catch(() => {})
    }
  },
  onShow() { this.active = true; this.focusWork(); this.refresh() },
  onHide() { this.active = false; this.blurWork(); clearTimeout(this.timer) },
  onUnload() { this.active = false; this.blurWork(); clearTimeout(this.timer); clearTimeout(this.confettiTimer) },
  focusWork() { const app = getApp(); if (app && app.setWorkFocus) app.setWorkFocus(this.pid) },
  blurWork() { const app = getApp(); if (app && app.clearWorkFocus) app.clearWorkFocus(this.pid) },
  async refresh(forceMedia = false) {
    clearTimeout(this.timer)
    if (this.polling || !this.active) return
    if (!this.pid) return this.setData({ loading: false, error: '没有选中的作品，请返回作品列表' })
    this.polling = true
    try {
      const project = await api.getProjectStatus(this.pid)
      if (!this.active) return
      this.errorRetries = 0
      const view = decorate(ui.projectView(project), project)
      view.steps = buildSteps(project, view)
      const app = getApp()
      if (app && app.syncWorkState) app.syncWorkState(this.pid, { status: project.status, portrait: !!project.portrait_url, video: !!project.video_url })
      if (GENERATING.includes(project.status) && project.status !== 'needs_review' && !view.aiBlocked && !state.leavePrompted(this.pid)) {
        state.markLeavePrompted(this.pid)
        this.promptLeave()
      }
      const prev = this.data.project
      const samePath = (a, b) => (a || '').split(/[?#]/)[0] === (b || '').split(/[?#]/)[0]
      if (prev) {
        if (!forceMedia && prev.portrait_url && samePath(prev.portrait_url, view.portrait_url)
          && !mediaUrlNeedsRefresh(prev.portrait_url)) view.portrait_url = prev.portrait_url
        if (!forceMedia && prev.video_url && samePath(prev.video_url, view.video_url)
          && !mediaUrlNeedsRefresh(prev.video_url)) view.video_url = prev.video_url
      }
      this.setData({ project: view, error: '' })
      if (view.portrait_url && (!prev || !prev.portrait_url) && !this.portraitNotified) {
        this.portraitNotified = true
        wx.showToast({ title: '定妆照已完成', icon: 'success' })
      }
      if (project.status === 'completed' && !this.completedNotified) {
        this.completedNotified = true
        if (wx.vibrateShort) wx.vibrateShort({ type: 'medium' })
        this.celebrate()
        wx.showModal({ title: '制作完成', content: '你的旅行电影已经生成，可以保存或分享啦。', confirmText: '知道了', showCancel: false })
      }
      this.maybePrompt(project)
      const readVersion = [project.status, !!project.portrait_url, !!project.video_url].join(':')
      if (this.readVersion !== readVersion) { await api.markProjectRead(this.pid); this.readVersion = readVersion }
    } catch (error) { this.setData({ error: ui.message(error) }) }
    finally {
      this.polling = false; this.setData({ loading: false })
      const refundWatching = this.data.project && this.data.project.status === 'refund_pending'
      const aiBlocked = this.data.project && this.data.project.aiBlocked
      if (this.data.error) this.errorRetries = (this.errorRetries || 0) + 1
      const exhausted = this.data.error && this.errorRetries >= MAX_ERROR_RETRIES
      if (this.active && !exhausted && !aiBlocked && (!this.data.project || !this.data.project.terminal || refundWatching || this.data.error)) {
        const errorDelay = 12000 * (2 ** Math.min(Math.max(this.errorRetries - 1, 0), 2))
        const delay = this.data.error ? Math.min(60000, errorDelay) : (this.data.project && this.data.project.portrait_url && !this.data.project.video_url ? 4000 : 5000)
        this.timer = setTimeout(() => this.refresh(), delay)
      }
    }
  },
  manualRefresh() {
    this.errorRetries = 0
    this.refresh(true)
  },
  maybePrompt(project) {
    if (this.prompting || project.status !== 'terminated') return
    if (this.prompted === project.status + ':' + (project.error || '')) return
    this.prompting = true
    if (project.ai_enabled === false) {
      this.prompted = project.status + ':' + (project.error || '')
      this.prompting = false
      wx.showModal({
        title: '制作服务未启用',
        content: '当前 AI 制作服务未启用，重新生成也不会成功。请先启动制作服务。',
        showCancel: false
      })
      return
    }
    wx.showActionSheet({
      itemList: ['重新生成', ['dev-bypass', 'free'].includes(project.payment_mode) ? '取消制作' : '申请全额退款'],
      success: res => {
        this.prompted = project.status + ':' + (project.error || '')
        if (res.tapIndex === 0) this.retry()
        else if (res.tapIndex === 1) this.confirmRefund()
      },
      fail: () => {},
      complete: () => { this.prompting = false }
    })
  },
  confirmRefund() {
    const p = this.data.project || {}
    const charge = !['dev-bypass', 'free'].includes(p.payment_mode)
    wx.showModal({
      title: charge ? '申请全额退款' : '取消本次制作',
      content: charge
        ? '本单只支持全额原路退款。提交后不能重新生成，退款成功后作品归档并且不能播放、保存或分享。'
        : '本次未扣款，确定取消并放弃本次生成吗？',
      confirmText: charge ? '申请退款' : '取消制作',
      cancelText: '再看看',
      success: res => { if (res.confirm) this.refund() }
    })
  },
  promptLeave() {
    wx.showModal({
      title: '正在生成中',
      content: '定妆照与视频正在生成，你可以返回首页继续浏览其他模板，完成后会主动提醒你。',
      confirmText: '回到首页',
      cancelText: '稍等一下',
      success: res => { if (res.confirm) ui.home() }
    })
  },
  celebrate() {
    const colors = ['#E8C58A', '#58A7A0', '#F3DDA9', '#7FB98C']
    const pieces = []
    for (let i = 0; i < 18; i++) {
      pieces.push({
        id: i,
        x: Math.round(i * 100 / 18 + (Math.random() * 6 - 3)),
        delay: (Math.random() * 0.6).toFixed(2),
        dur: (1.8 + Math.random() * 0.8).toFixed(2),
        color: colors[i % colors.length],
        w: 8 + Math.round(Math.random() * 8)
      })
    }
    this.setData({ confetti: pieces, showConfetti: true })
    clearTimeout(this.confettiTimer)
    this.confettiTimer = setTimeout(() => this.setData({ showConfetti: false }), 2800)
  },
  async pay() {
    if (this.data.paying) return
    if (this.data.config && this.data.config.payment_enabled === false) {
      return ui.toast('支付暂未开放')
    }
    this.setData({ paying: true })
    try { await payment.pay(this.pid) }
    catch (error) { ui.toast(error) }
    finally { this.setData({ paying: false }); if (this.active) this.refresh() }
  },
  async retry() {
    if (this.data.saving) return
    if (this.data.project && this.data.project.ai_enabled === false) {
      return ui.toast('AI制作服务未启用，当前不能重新生成')
    }
    this.setData({ saving: true })
    try {
      await api.retryProject(this.pid)
      this.prompted = ''
      ui.toast('已重新开始生成')
      this.refresh()
    } catch (error) { ui.toast(error) }
    finally { this.setData({ saving: false }) }
  },
  async refund() {
    if (this.data.saving) return
    this.setData({ saving: true })
    try {
      await api.refundProject(this.pid)
      this.prompted = ''
      ui.toast('退款已提交')
      this.refresh()
    } catch (error) { ui.toast(error) }
    finally { this.setData({ saving: false }) }
  },
  preview() { const url = this.data.project.portrait_url; if (url) wx.previewImage({ current: url, urls: [url] }) },
  async save(e) {
    if (this.data.saving) return
    this.setData({ saving: true })
    const kind = e.currentTarget.dataset.kind
    try {
      await ui.saveMedia(kind === 'video' ? this.data.project.video_url : this.data.project.portrait_url, kind)
    } catch (error) {
      // ui.saveMedia already presents the actionable permission/download error.
    } finally {
      this.setData({ saving: false })
    }
  },
  async mediaFailed() {
    if (Date.now() - (this.mediaRefreshAt || 0) < 60000) return ui.toast('素材暂时无法读取，请稍后重试')
    this.mediaRefreshAt = Date.now()
    await this.refresh(true)
  },
  copyOrder() {
    const orderNo = (this.data.project && this.data.project.order_no) || ''
    if (!orderNo) return ui.toast('暂无订单号')
    wx.setClipboardData({ data: orderNo, success: () => wx.showToast({ title: '订单号已复制', icon: 'none' }) })
  },
  showGuide() {
    wx.showModal({
      title: '制作流程',
      content: '1. 生成定妆照，约 2 分钟\n2. 生成旅行视频，约 3–5 分钟\n3. 完成后可在「我的作品」中查看、保存和分享',
      showCancel: false,
      confirmText: '知道了'
    })
  },
  showRefundInfo() {
    wx.showModal({
      title: '退款说明',
      content: '生成失败后可选择重新生成或申请全额原路退款，不支持部分退款。提交退款后不能重新生成；退款成功后作品归档并停止播放、保存和分享。',
      showCancel: false,
      confirmText: '知道了'
    })
  },
  showHelp() {
    wx.showModal({
      title: '遇到问题？',
      content: '如果生成失败、超时或网络异常：\n1. 先点击「刷新」查看最新进度；\n2. 生成失败后可选择「重新生成」或「申请全额退款」；\n3. 退款待核实或退款失败时，请联系客服核对通联流水。',
      showCancel: false,
      confirmText: '知道了'
    })
  },
  back() { ui.back() }, goWorks() { ui.open('works') }, again() { ui.home() },
  onShareAppMessage() {
    const p = this.data.project || {}
    if (p.portrait_url) {
      const title = (p.video_url ? '我的旅行电影' : '我的定妆照') + ' · 贺见旅拍'
      return { title, path: '/pages/phase2/share/share?id=' + encodeURIComponent(this.pid), imageUrl: p.share_image_url || p.portrait_url }
    }
    return { title: '贺见旅拍 · 旅行电影', path: '/pages/phase2/entry/entry' }
  },
  onShareTimeline() {
    const p = this.data.project || {}
    if (p.portrait_url) return { title: ((p.video_url ? '我的旅行电影' : '我的定妆照') + ' · 贺见旅拍'), query: 'id=' + encodeURIComponent(this.pid), imageUrl: p.share_image_url || p.portrait_url }
    return { title: '贺见旅拍 · 旅行电影' }
  }
})
