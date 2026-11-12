const { resolveEnvironment } = require('./config/environment')
const api = require('./utils/phase2Api')
const ui = require('./utils/phase2Ui')
const ENV_CONFIG = resolveEnvironment()

App({
  globalData: {
    ...ENV_CONFIG,
    localBuild: 'hejian-full-local-v3',
    userId: null,
    openid: null,
    token: null
  },

  onLaunch() {
    this.checkUpdate()
    this.globalData.userId = 'local-user'
    this.globalData.openid = 'local-user'
    this.globalData.token = null
    this.watchTimer = null
    this.watchBusy = false
    this.watchStates = {}
    this.workFocusPid = null
    this.notifying = false
    this.noticeQueue = []
  },

  onShow() { this.checkUpdate(); this.startWorkWatcher() },
  onHide() { this.stopWorkWatcher() },

  checkUpdate() {
    if (this.updateRegistered || this.globalData.localOnly || !wx.getUpdateManager) return
    this.updateRegistered = true
    const manager = wx.getUpdateManager()
    manager.onUpdateReady(() => wx.showModal({
      title: '更新提示',
      content: '新版本已经准备好，是否立即更新？',
      confirmText: '立即更新',
      cancelText: '稍后',
      success: result => { if (result.confirm) manager.applyUpdate() }
    }))
    manager.onUpdateFailed(() => wx.showModal({
      title: '更新失败',
      content: '新版本下载失败，请删除小程序重新打开',
      showCancel: false
    }))
  },

  // ---- work 页聚焦握手：work 页自己轮询并弹窗时，全局观察者跳过该作品 ----
  setWorkFocus(pid) { this.workFocusPid = pid },
  clearWorkFocus(pid) { if (this.workFocusPid === pid) this.workFocusPid = null },
  syncWorkState(pid, view) { if (pid && view) this.watchStates[pid] = view },

  // ---- 全局作品观察者：前台每 15 秒轮询作品列表，对每个未完成任务做状态跃迁提醒 ----
  startWorkWatcher() {
    this.stopWorkWatcher()
    const poll = () => {
      if (this.watchBusy) return
      this.watchBusy = true
      api.listWorkStates(100).then(result => {
        const items = (result && result.items) || []
        items.forEach(item => this.noticeTransition(item.project_id, item))
      }).catch(() => {}).then(() => { this.watchBusy = false })
    }
    poll()
    this.watchTimer = setInterval(poll, 15000)
  },

  stopWorkWatcher() {
    if (this.watchTimer) { clearInterval(this.watchTimer); this.watchTimer = null }
  },

  noticeTransition(pid, item) {
    if (pid === this.workFocusPid) return
    const view = item
    const now = { status: view.status, portrait: !!view.portrait_url, video: !!view.video_url }
    const prev = this.watchStates[pid]
    this.watchStates[pid] = now
    if (!prev) return
    if (prev.status === now.status && prev.portrait === now.portrait && prev.video === now.video) return
    if (now.status === 'completed' && now.video) {
      this.enqueueNotice({ kind: 'completed', pid, title: view.video_name })
    } else if (now.status === 'refunded') {
      this.enqueueNotice({ kind: 'refunded', pid })
    } else if (['terminated', 'refund_pending', 'refund_failed'].includes(now.status)) {
      this.enqueueNotice({ kind: 'failed', pid, error: view.error })
    } else if (now.portrait && !now.video && !prev.portrait) {
      this.enqueueNotice({ kind: 'portrait' })
    }
  },

  // ---- 通知队列：同一时刻只弹一个，完成/失败/轻提示串行处理 ----
  enqueueNotice(notice) {
    this.noticeQueue.push(notice)
    this.drainNotices()
  },

  drainNotices() {
    if (this.notifying || !this.noticeQueue.length) return
    const notice = this.noticeQueue.shift()
    this.notifying = true
    const done = () => { this.notifying = false; this.drainNotices() }
    if (notice.kind === 'portrait') {
      wx.showToast({ title: '定妆照已完成', icon: 'success' })
      setTimeout(done, 1600)
    } else if (notice.kind === 'completed') {
      wx.showModal({
        title: '生成完成',
        content: '《' + (notice.title || '我的旅行电影') + '》旅行电影已生成，是否现在查看？',
        confirmText: '去查看',
        cancelText: '稍后',
        success: res => { if (res.confirm) this.openWork(notice.pid) },
        complete: done
      })
    } else if (notice.kind === 'failed') {
      wx.showModal({
        title: '生成未完成',
        content: (notice.error || '生成未完成，可以选择重新生成或申请全额退款。') + '，是否前往处理？',
        confirmText: '去处理',
        cancelText: '稍后',
        success: res => { if (res.confirm) this.openWork(notice.pid) },
        complete: done
      })
    } else if (notice.kind === 'refunded') {
      wx.showModal({
        title: '退款已完成',
        content: '全额退款已完成，作品已归档，不能继续播放、保存或分享。',
        confirmText: '知道了',
        showCancel: false,
        complete: done
      })
    } else {
      done()
    }
  },

  openWork(pid) {
    const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : []
    const target = pages.find(p => p.route === 'pages/phase2/work/work' && p.pid === pid)
    if (target) {
      wx.navigateBack({ delta: Math.max(1, pages.length - pages.indexOf(target) - 1) })
      return
    }
    ui.open('work', pid)
  },

  onShareAppMessage() {
    return { title: '贺见旅拍 · 旅行电影', path: '/pages/phase2/entry/entry' }
  },

  onShareTimeline() {
    return { title: '贺见旅拍 · 旅行电影', query: '' }
  }
})
