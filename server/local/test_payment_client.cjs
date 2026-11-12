const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { test } = require('node:test')
const client = path.resolve(__dirname, '../../client')

function load(relative, dependencies, wx = {}) {
  let page
  const context = { module: { exports: {} }, require: name => dependencies[name], wx,
    getApp: () => ({}),
    Page: definition => { page = definition }, clearTimeout() {}, setTimeout() {}, Date, Map }
  vm.runInNewContext(fs.readFileSync(path.join(client, relative), 'utf8'), context)
  return page || context.module.exports
}

test('W06S opens the cashier with unchanged signed fields; navigation does not claim payment', async () => {
  const extra = { sign: 'signed', body: 'title', trxamt: '1990' }
  let options
  const payment = load('utils/phase2Payment.js', { './phase2Api': {
    prepay: async () => ({ pay_mode: 'w06s', cashier_appid: 'wxef277996acc166c3', extra_data: extra })
  } }, { navigateToMiniProgram: value => { options = value; value.success({}) } })
  await payment.pay('p2_test')
  assert.equal(options.extraData, extra)
  assert.equal(options.envVersion, 'release')
  assert.equal(options.appId, 'wxef277996acc166c3')
})

test('Already paid response skips cashier navigation', async () => {
  const payment = load('utils/phase2Payment.js', { './phase2Api': {
    prepay: async () => ({ payment_status: 'paid' })
  } }, { navigateToMiniProgram() { assert.fail('must not reopen cashier') } })
  await payment.pay('p2_test')
})

test('Cashier cancellation rejects and can be retried with the same project', async () => {
  const ids = []
  const payment = load('utils/phase2Payment.js', { './phase2Api': {
    prepay: async id => { ids.push(id); return { pay_mode: 'w06s', extra_data: {} } }
  } }, { navigateToMiniProgram: value => value.fail({ errMsg: 'navigateToMiniProgram:fail cancel' }) })
  await assert.rejects(payment.pay('p2_test'), /取消/)
  await assert.rejects(payment.pay('p2_test'), /取消/)
  assert.deepEqual(ids, ['p2_test', 'p2_test'])
})

test('Work page onShow reads server status after returning; pending order remains unpaid', async () => {
  let requests = 0
  const ui = load('utils/phase2Ui.js', {})
  const page = load('pages/phase2/work/work.js', {
    '../../../utils/phase2Api': {
      getProjectStatus: async () => { requests++; return { status: 'pending_payment', amount_fen: 1990 } },
      markProjectRead: async () => {}
    }, '../../../utils/phase2Ui': { ...ui, layout: () => ({}) },
    '../../../state/phase2State': {}, '../../../utils/phase2Payment': {}
  })
  page.pid = 'p2_test'
  page.setData = function (value) { Object.assign(this.data, value) }
  page.onShow()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(requests, 1)
  assert.equal(page.data.project.status, 'pending_payment')
  assert.equal(page.data.project.statusText, '待支付')
  assert.equal(page.data.project.priceText, '19.90')
  page.onHide()
})

test('Cashier AppID is declared in the mini program manifest', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(client, 'app.json'), 'utf8'))
  assert.ok(manifest.navigateToMiniProgramAppIdList.includes('wxef277996acc166c3'))
})

test('upload page shows only the template tier price', () => {
  const wxMock = {
    getWindowInfo: () => ({ windowWidth: 375, statusBarHeight: 24 }),
    getMenuButtonBoundingClientRect: () => ({ top: 24, height: 32, left: 300 })
  }
  const ui = load('utils/phase2Ui.js', {}, wxMock)
  const page = load('pages/phase2/upload/upload.js', {
    '../../../utils/phase2Api': {},
    '../../../state/phase2State': {},
    '../../../utils/phase2Ui': ui,
    '../../../utils/phase2Payment': {}
  }, wxMock)
  page.data.config = {
    payment_mode: 'allinpay',
    standard_price_tiers: { '1': 990, '2': 1690, '3': 1990 },
    charge_price_tiers: { '1': 10, '2': 10, '3': 10 }
  }
  page.setData = function (value) { Object.assign(this.data, value) }
  page.update({
    participation_mode: 'multi',
    selected_person_uid: '',
    template: { actor_count: 2, assets: {} },
    roles: [
      { character_id: 'a', person_uid: '1', check_status: 'passed' },
      { character_id: 'b', person_uid: '2', check_status: 'passed' }
    ]
  })
  assert.equal(page.data.standardPriceText, '16.9')
  assert.equal(page.data.chargePriceText, undefined)
  assert.equal(page.data.priceText, '')
})

test('entry uses the relative swiper index and shares the selected template', () => {
  const wxml = fs.readFileSync(path.join(client, 'pages/phase2/entry/entry.wxml'), 'utf8')
  const page = load('pages/phase2/entry/entry.js', {
    '../../../utils/phase2Api': {},
    '../../../state/phase2State': {},
    '../../../utils/phase2Ui': { layout: () => ({}), toast() {}, message: e => e.message, open() {} }
  })
  page.data.selected = { template_code: 'P2T-test', video_name: '测试', cover: 'cover.jpg' }
  const shared = page.onShareAppMessage()
  assert.match(wxml, /index===swiperCurrent/)
  assert.equal(shared.path, '/pages/phase2/entry/entry?template=P2T-test')
  assert.equal(page.data.peopleOptions.includes('4人'), false)
})

test('save button is released after a rejected download', async () => {
  const page = load('pages/phase2/work/work.js', {
    '../../../utils/phase2Api': {},
    '../../../utils/phase2Ui': {
      layout: () => ({}), projectView: p => p, message: e => e.message,
      saveMedia: async () => { throw new Error('download failed') }
    },
    '../../../state/phase2State': { get: () => ({}) },
    '../../../utils/phase2Payment': {}
  })
  page.setData = function (value) { Object.assign(this.data, value) }
  page.data.project = { portrait_url: 'https://example/image.jpg' }
  await page.save({ currentTarget: { dataset: { kind: 'image' } } })
  assert.equal(page.data.saving, false)
})

test('client target is explicit and never points a release build at localhost', () => {
  const target = require(path.join(client, 'config/target.js'))
  assert.ok(['local', 'staging', 'production'].includes(target.environment))
  if (target.environment === 'local') {
    assert.match(target.apiBaseUrl, /^http:\/\/127\.0\.0\.1:\d+$/)
  } else {
    assert.match(target.apiBaseUrl, /^https:\/\/[^/]+$/)
  }
})
