// v3 removes the old people/relationship/style-selection state shape.
const target = require('../config/target')
const namespace = 'phase2.' + target.environment + '.' + target.apiBaseUrl
const KEY = namespace + '.state.v1'

const initial = () => ({ projectId: '' })

function get() {
  return wx.getStorageSync(KEY) || initial()
}

function save(patch) {
  const state = { ...get(), ...patch }
  wx.setStorageSync(KEY, state)
  return state
}

function reset() {
  const state = initial()
  wx.setStorageSync(KEY, state)
  return state
}

const BROWSE = namespace + '.browse.v2'
const DRAFTS = namespace + '.drafts.v2'
const GUIDE = namespace + '.guide.v1'
const LEAVE = namespace + '.leave-prompt.v1'
const SWIPE = namespace + '.swipe-hint.v1'
function browse() { return wx.getStorageSync(BROWSE) || {} }
function saveBrowse(value) { wx.setStorageSync(BROWSE, value) }
function draft(code) { return (wx.getStorageSync(DRAFTS) || {})[code] }
function saveDraft(code, value) { const drafts = wx.getStorageSync(DRAFTS) || {}; drafts[code] = value; wx.setStorageSync(DRAFTS, drafts) }
function guideSeen() { return !!wx.getStorageSync(GUIDE) }
function markGuideSeen() { wx.setStorageSync(GUIDE, true) }
function leavePrompted(pid) { return !!((wx.getStorageSync(LEAVE) || {})[pid]) }
function markLeavePrompted(pid) { const m = wx.getStorageSync(LEAVE) || {}; m[pid] = true; wx.setStorageSync(LEAVE, m) }
function swipeHintSeen() { return !!wx.getStorageSync(SWIPE) }
function markSwipeHintSeen() { wx.setStorageSync(SWIPE, true) }
module.exports = { KEY, initial, get, save, reset, browse, saveBrowse, draft, saveDraft, guideSeen, markGuideSeen, leavePrompted, markLeavePrompted, swipeHintSeen, markSwipeHintSeen }
