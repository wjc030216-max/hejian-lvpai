// Behaviour tests for page/API boundaries; runs with Node and no WeChat/cloud calls.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')
const root = path.resolve(__dirname, '../../client')
function page(name, api, state = {}, wx = {}, modules = {}) {
  let definition
  const ui = { layout: () => ({}), projectView: p => ({...p,terminal:p.status==='completed'}), toast: () => {}, message: e => e.message, open: (...a) => ui.opened.push(a), opened: [] }
  const pageState = {
    browse: () => ({}), saveBrowse() {}, draft: () => null, saveDraft() {}, save() {},
    guideSeen: () => false, markGuideSeen() {}, swipeHintSeen: () => false,
    markSwipeHintSeen() {}, leavePrompted: () => false, markLeavePrompted() {},
    ...state
  }
  const ctx = { getApp: () => ({ syncWorkState() {}, setWorkFocus() {}, clearWorkFocus() {} }),
    require: id => Object.entries(modules).find(([key]) => id.includes(key))?.[1]
      || (id.includes('phase2Api') ? api : id.includes('phase2State') ? pageState : ui),
    wx, Page: p => { definition = p }, setTimeout: () => 1, clearTimeout: () => {}, console }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'pages/phase2', name, name + '.js'), 'utf8'), ctx)
  definition.data = JSON.parse(JSON.stringify(definition.data))
  definition.setData = (patch, cb) => {
    Object.entries(patch).forEach(([key, value]) => {
      if (!key.includes('.')) { definition.data[key] = value; return }
      const parts = key.split('.')
      let target = definition.data
      for (let i = 0; i < parts.length - 1; i++) target = target[parts[i]] ||= {}
      target[parts[parts.length - 1]] = value
    })
    if (cb) cb()
  }
  return { p: definition, ui }
}
async function main() {
  let calls = 0, resolvePayment, navigations = []
  const order = new Promise(resolve => { resolvePayment = resolve })
  const { p: upload } = page('upload', { createOrder: () => { calls++; return order } }, {},
    { redirectTo: o => navigations.push(o.url), showModal: o => o.success({ cancel: true }) },
    { phase2Payment: { pay: async () => {} } })
  upload.pid = 'one'; upload.data.all = true; upload.data.consent = false
  await upload.next(); assert.equal(calls, 0, 'AI-disabled configurations must not create an order')
  upload.data.aiEnabled = true
  await upload.next(); assert.equal(calls, 0, 'photo consent is required before payment')
  upload.data.consent = true
  const pending = upload.next(); await upload.next()
  assert.equal(calls, 1, 'double tap must not submit a second order')
  resolvePayment({ order_no: 'DEV' }); await pending
  assert.equal(navigations[0], '/pages/phase2/work/work?id=one')
  let disabledCalls = 0
  const { p: paymentDisabled } = page('upload', {
    createOrder: () => { disabledCalls++; return Promise.resolve({}) }
  }, {}, { showModal: o => o.success({ confirm: true }) })
  paymentDisabled.pid = 'disabled'
  paymentDisabled.data.all = true
  paymentDisabled.data.consent = true
  paymentDisabled.data.aiEnabled = true
  paymentDisabled.data.config = { payment_enabled: false, payment_mode: 'disabled' }
  await paymentDisabled.next()
  assert.equal(disabledCalls, 0, 'disabled payment must not create an order')

  const roles = [{person_uid:'1',character_id:'a',photo_url:'first',check_status:'passed'}, {person_uid:'2',character_id:'b',photo_url:'second',check_status:'passed'}, {person_uid:'3',character_id:'c',check_status:'empty'}]
  let modeCall
  const {p: solo} = page('upload', {setParticipation: async (pid, mode, uid) => { modeCall=[pid,mode,uid]; return {roles,template:{},participation_mode:mode,selected_person_uid:uid} }})
  solo.pid='solo'
  solo.update({roles,template:{},participation_mode:'multi'})
  assert.equal(solo.data.all,false,'multiplayer requires every visitor photo')
  await solo.changeParticipation('single','')
  assert.equal(solo.data.all,false,'single mode never guesses a selected role')
  await solo.changeParticipation('single','2')
  assert.deepEqual(modeCall,['solo','single','2'])
  assert.equal(solo.data.required,1); assert.equal(solo.data.all,true)
  assert.equal(solo.data.roles[0].uses_template,true)
  assert.equal(solo.data.roles[1].uses_template,false)
  assert.equal(solo.data.roles[0].photo_url,'first','unused photo remains a draft')
  await solo.switchRole({ currentTarget: { dataset: { uid: '3' } } })
  assert.deepEqual(modeCall,['solo','single','3'],'tapping the role tag switches who plays without uploading')
  await solo.changeParticipation('multi','')
  assert.equal(solo.data.roles[0].photo_url,'first'); assert.equal(solo.data.all,false)

  let selectedCall, uploadCall, chooseDone
  const { p: direct } = page('upload', {
    setParticipation: async (pid, mode, uid) => { selectedCall=[pid,mode,uid]; return {roles,template:{},participation_mode:'single',selected_person_uid:uid} },
    uploadRolePhoto: async (pid, uid) => { uploadCall=[pid,uid] },
    getProject: async () => ({roles,template:{},participation_mode:'single',selected_person_uid:'2'})
  }, {}, { chooseMedia: o => { chooseDone = o.success({ tempFiles: [{ tempFilePath: '/tmp/p.jpg' }] }) } })
  direct.pid='direct'; direct.data.consent=true; direct.data.mode='single'
  direct.update({roles,template:{},participation_mode:'single',selected_person_uid:''})
  direct.choose({ currentTarget: { dataset: { index: 1 } } })
  await chooseDone
  assert.deepEqual(selectedCall,['direct','single','2'])
  assert.deepEqual(uploadCall,['direct','2'])

  let created = 0, drafts = { code: { projectId: 'draft1', version: 'v1' } }
  const template = { template_code: 'code', version: 'v1' }
  const state = { draft: c => drafts[c], saveDraft: (c,d) => { drafts[c] = d }, save: () => {}, saveBrowse: () => {} }
  const { p: entry, ui } = page('entry', {
    getProject: async () => ({ project_id: 'draft1', template, roles: [{ person_uid: '1', photo_url: 'kept-photo' }] }),
    createProject: async () => { created++; return { project_id: 'new', template, roles: [] } }
  }, state)
  entry.data.selected = template; await entry.shoot()
  assert.equal(created, 0, 'returning to same template must resume photos')
  assert.deepEqual(ui.opened[0], ['upload', 'draft1'])
  entry.data.selected = { template_code: 'other', version: 'v2' }; await entry.shoot()
  assert.equal(created, 1, 'different template must use an independent draft')

  const { p: work } = page('work', { getProjectStatus: async id => ({ project_id: id, status: 'completed' }), markProjectRead: async () => {} }, { get: () => ({ projectId: 'unrelated-draft' }) })
  work.onLoad({ id: 'opened-work' }); assert.equal(work.pid, 'opened-work', 'opening a work must not read global draft identity')
  const { p: blockedWork } = page('work', {
    getProjectStatus: async id => ({ project_id: id, status: 'portrait_submit', ai_enabled: false, progress: 0 }),
    markProjectRead: async () => {}
  })
  blockedWork.pid = 'blocked-work'; blockedWork.active = true; await blockedWork.refresh()
  assert.ok(blockedWork.data.project, blockedWork.data.error || 'blocked work should load')
  assert.equal(blockedWork.data.project.generating, false, 'disabled AI must not animate generation')
  assert.equal(blockedWork.data.project.progress, 0)
  assert.equal(blockedWork.data.project.progressLabel, '已停止')
  assert.equal(blockedWork.data.project.statusKind, 'failed')
  const entryMarkup = fs.readFileSync(path.join(root, 'pages/phase2/entry/entry.wxml'), 'utf8')
  assert.match(entryMarkup, /filter-label">人数</)
  assert.match(entryMarkup, /filter-label">关系\/性别</)
  assert.equal((entryMarkup.match(/class="filter-picker"/g) || []).length, 3,
    'the second slot contains mutually exclusive gender and relationship pickers')
  assert.doesNotMatch(entryMarkup, /选择景区|清除景区|filter-label">景区/, 'single scenic site has no scenic filter')
  assert.doesNotMatch(entryMarkup, /换一批/)
  const workMarkup = fs.readFileSync(path.join(root, 'pages/phase2/work/work.wxml'), 'utf8')
  assert.match(workMarkup, /class="progress-card/)
  assert.match(workMarkup, /class="work-action-row"/)
  assert.match(workMarkup, /AI服务未启用/)
  assert.doesNotMatch(workMarkup, /整体进度|订单号|退款成功后作品归档/)
  const adminHtml = fs.readFileSync(path.join(root, '../server/admin/static/index.html'), 'utf8')
  const adminJs = fs.readFileSync(path.join(root, '../server/admin/static/app.js'), 'utf8')
  assert.match(adminHtml, /贺见旅拍 · 管理工作台/)
  assert.match(adminHtml, /30 秒 A\/B 双段模板/)
  assert.match(adminHtml, /256MB/)
  assert.match(adminJs, /templateDuration/)
  assert.match(adminJs, /segment-card/)

  let seenQuery
  const entryApi = {
    listTemplates: async q => { seenQuery = q; return { items: Array.from({length: 8}, (_,i) => ({template_code: 't'+i})), facets: {}, scenes: [], total: 503, next_offset: 32 } },
    getConfig: async () => ({}), getMessages: async () => ({ items: [], total: 0 })
  }
  const { p: resume } = page('entry', entryApi, { browse: () => ({ index: 25, filters: { actor_count: '2' } }), saveBrowse: () => {} })
  resume.onLoad(); await new Promise(resolve => setImmediate(resolve))
  assert.equal(seenQuery.offset, 0); assert.equal(resume.data.current, 0)
  assert.equal('seed' in seenQuery, false, 'catalogue requests must not use a random seed')
  assert.equal('random' in seenQuery, false, 'catalogue requests must not switch to random mode')
  const examples = [{template_code:'a',example:'a.mp4'},{template_code:'b',example:'b.mp4'}]
  const {p: feed} = page('entry', {listTemplates: async()=>({items:examples,facets:{},scenes:[],total:2,next_offset:null}),
    getConfig: async()=>({}),getMessages: async()=>({items:[],total:0})}, {browse:()=>({}),saveBrowse:()=>{}})
  feed.onLoad(); feed.onShow(); await new Promise(resolve=>setImmediate(resolve))
  assert.equal(feed.data.playing,true,'first template must autoplay')
  assert.equal(feed.data.muted,true,'autoplay stays muted')
  feed.openMore(); assert.equal(feed.data.playing,false,'template drawer pauses the player')
  feed.closeDrawer(); assert.equal(feed.data.playing,true,'closing drawer resumes current template')
  feed.play(); assert.equal(feed.data.playing,false,'tap pauses')
  feed.fetch(false); assert.equal(feed.data.playing,false,'preloading must not override manual pause')
  feed.slide({detail:{current:1}}); assert.equal(feed.data.playing,true,'swipe autoplays next template')
  assert.equal(feed.data.selected.template_code,'b')
  feed.onHide(); assert.equal(feed.data.playing,false)
  feed.onShow(); assert.equal(feed.data.playing,true,'return to page resumes playback')
  feed.videoFailed(); assert.equal(feed.data.playing,false); assert.equal(feed.data.videoError,true)
  feed.data.gridFilters={actor_count:'3',relationship:'三口之家'}
  feed.data.peopleOptions=['不限','1人','2人','3人']; feed.data.gridLoading=false
  feed.onPeoplePick({detail:{value:2}})
  assert.equal(feed.data.gridFilters.actor_count,'2')
  assert.equal(feed.data.gridFilters.relationship,'','changing count clears incompatible downstream choices')
  await new Promise(resolve=>setImmediate(resolve))
  const {p: large} = page('entry', {}, {saveBrowse:()=>{}})
  large.catalog=Array.from({length:503},(_,i)=>({template_code:'template-'+i,example:'sample-'+i}))
  large.data.feedItems=large.catalog;large.data.current=0;large.data.swiperCurrent=0
  large.slide({detail:{current:3,source:'touch'}})
  assert.equal(large.data.current,3);assert.equal(large.data.selected.template_code,'template-3')
  large.slide({detail:{current:large.data.swiperCurrent,source:''}})
  assert.equal(large.data.current,3,'programmatic recenter must not advance the feed')

  let queryIds
  const {p: works} = page('works',{getWorksStatus:async ids=>{queryIds=ids;return {items:ids.map(project_id=>({project_id,status:'completed'}))}}})
  works.active=true; works.data.items=Array.from({length:45},(_,i)=>({project_id:'p2_'+i,terminal:i!==40}));works.data.nextOffset=45
  await works.refreshLoaded()
  assert.equal(works.data.items.length,45);assert.equal(works.data.nextOffset,45)
  assert.equal(works.data.items[40].terminal,true);assert.equal(queryIds.join(','),'p2_40')
  console.log('PASS: payment/drafts/browse, autoplay, mute, pause, template drawer, switching templates, hide/resume, playback failure')
}
main().catch(error => { console.error(error); process.exit(1) })
