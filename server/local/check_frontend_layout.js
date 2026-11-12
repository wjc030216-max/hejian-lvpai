/* Layout preview uses current WXML/WXSS, not a second hand-maintained design.
 * It verifies web layout at phone sizes; the real WeChat compiler is checked separately.
 * NODE_PATH must point to an installed Playwright package. No paid API calls.
 */
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { chromium } = require('playwright')
const root = path.resolve(__dirname, '../../client')
async function main() {
  const output = path.join(os.tmpdir(), 'aitravel-phase2-layout')
  fs.mkdirSync(output, { recursive: true })
  const apiBase = (process.env.LAYOUT_API_BASE || 'http://127.0.0.1:8092').replace(/\/+$/, '')
  const raw = await fetch(apiBase + '/api/v2/templates?limit=12').then(r => r.json())
  const catalog = raw.data.items
  function localMedia(value) {
    if (typeof value === 'string' && /^https?:\/\//.test(value)) return value
    if (typeof value === 'string' && value.includes('/media/') && value.includes('/assets/')) return apiBase + value.slice(value.indexOf('/assets/'))
    if (Array.isArray(value)) return value.map(localMedia)
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,localMedia(v)]))
    return value
  }
  const items = localMedia(catalog), template = items.find(i => i.actor_count === 3) || items[0]
  if (!template) throw new Error('管理层未上传模板，无法执行布局预览')
  const common = { layout: {navTop: 26, navHeight: 32, navRight: 110}, loading: false, error: '' }
  const project = {
    template,
    roles: [{ photo_url: template.cover }],
    timeText: '2026/10/5 10:00',
    status: 'portrait_processing',
    statusKind: 'working',
    statusText: '制作中',
    portrait_url: template.cover,
    portraitCardText: '正在制作中…',
    videoCardText: '将自动生成',
    portraitOverlay: '定妆照生成中',
    videoOverlay: '等待定妆照完成',
    focusTitle: '正在制作定妆照',
    focusEta: '预计需要 1–2 分钟',
    focusDesc: '我们正在努力生成中，请稍候…',
    payment_mode: 'dev-bypass',
    order_no: 'DEV-PREVIEW',
    progress: 60,
    progressLabel: '约 60%',
    generating: true,
    steps: [
      { label: '支付', short: '支付', sub: '已完成', done: true, active: false, failed: false, icon: 'check', linked: false },
      { label: '定妆照', short: '定妆照', sub: '制作中', done: false, active: true, failed: false, icon: 'photo', linked: true },
      { label: '旅行视频', short: '旅行视频', sub: '待开始', done: false, active: false, failed: false, icon: 'play', linked: false }
    ]
  }
  const samples = {
    entry: { ...common, feedItems: items, items, current: 0, swiperCurrent: 0, selected: { ...items[0], heat_label: '3.2万人选择' }, scenes: ['漓江','银子岩'], filters: {}, muted: true, playing: false, drawer: '', total: items.length, videoError: false },
    upload: { ...common, project, roles: template.character_settings.map(r => ({...r, reference: template.assets.roles[r.character_id], check_status: 'empty'})), passed: 0, mode: 'multi', selectedUid: '', config: {}, consent: false, agreement: false },
    work: { ...common, project },
    works: { ...common, items: [project] },
  }
  samples['upload@single'] = { ...samples.upload, mode: 'single', selectedUid: '2', roles: samples.upload.roles.map((r,i) => ({ ...r, uses_template: i !== 1 })) }
  samples['work@completed'] = {
    ...samples.work,
    project: {
      ...project,
      status: 'completed',
      statusKind: 'done',
      statusText: '已完成',
      video_url: template.example,
      videoCardText: '已完成',
      videoOverlay: '',
      portraitCardText: '已完成',
      portraitOverlay: '',
      focusTitle: '作品已生成',
      focusEta: '可以保存和分享',
      focusDesc: '点击作品卡上的保存按钮，或分享给家人朋友',
      progress: 100,
      progressLabel: '100%',
      generating: false,
      steps: project.steps.map((step, index) => ({ ...step, sub: '已完成', done: true, active: false, failed: false, icon: 'check', linked: index > 0 }))
    }
  }
  samples['work@failed'] = {
    ...samples.work,
    project: {
      ...project,
      status: 'terminated',
      statusKind: 'failed',
      statusText: '制作未完成',
      portraitOverlay: '未生成',
      videoOverlay: '未生成',
      portraitCardText: '未生成',
      videoCardText: '未生成',
      focusTitle: '制作未完成',
      focusEta: '可选择重试或退款',
      focusDesc: '本次生成没有完成，重新生成会保留当前照片',
      progress: 45,
      progressLabel: '45%',
      generating: false,
      steps: [
        { ...project.steps[0], sub: '已完成', done: true, active: false, failed: false, icon: 'check', linked: false },
        { ...project.steps[1], sub: '未完成', done: false, active: false, failed: true, icon: 'photo', linked: true },
        { ...project.steps[2], sub: '未完成', done: false, active: false, failed: true, icon: 'play', linked: false }
      ]
    }
  }
  samples['entry@more'] = {
    ...samples.entry,
    drawer: 'more',
    gridItems: items,
    gridTotal: 238,
    gridNextOffset: null,
    gridLoading: false,
    gridFilters: { actor_count: '2', scene: '银子岩', relationship: '情侣' },
    peopleOptions: ['不限','1人','2人','3人'],
    peopleValue: 2,
    sceneOptions: ['不限','漓江','银子岩'],
    sceneValue: 2,
    relationOptions: ['不限','情侣','亲子','朋友'],
    relationValue: 1,
    genderOptions: ['不限','男','女'],
    genderValue: 0,
  }
  const convertCss = css => css
    .replace(/(-?[\d.]+)rpx/g, (_,n) => `calc(${n} * 100vw / 750)`).replace(/(?<![.\w-])page\s*\{/g,'body {')
    .replace(/(?<![.\w-])(view|image|text)(?![\w-])/g, tag => ({view:'div',image:'img',text:'span'})[tag])
  const browser = await chromium.launch({ headless: true, ...(process.env.LAYOUT_BROWSER ? { executablePath: process.env.LAYOUT_BROWSER } : { channel: 'msedge' }) })
  try {
    for (const width of [320, 375, 430]) {
      const page = await browser.newPage({ viewport: { width, height: width === 320 ? 568 : 812 }, deviceScaleFactor: 1 })
      for (const [sampleName, data] of Object.entries(samples)) {
        const name = sampleName.split('@')[0]
        const css = convertCss(fs.readFileSync(path.join(root,'styles/phase2-v2.wxss'),'utf8') + '\n' + fs.readFileSync(path.join(root,'pages/phase2',name,name+'.wxss'),'utf8').replace(/@import[^;]+;/g,''))
        await page.setContent('<html><head><meta charset="UTF-8"><style>html,body{margin:0}button{font-family:inherit;border:none}img{object-fit:contain}.swiper-item{height:100%}.scroll-view{overflow:auto}.block{display:contents}' + css + '</style></head><body></body></html>')
        const wxml = fs.readFileSync(path.join(root,'pages/phase2',name,name+'.wxml'),'utf8')
        await page.evaluate(({wxml,data}) => {
          const expression = (s, scope) => { try { return Function('scope', 'with(scope){ return (' + s + ') }')(new Proxy(scope,{has:()=>true,get:(o,k)=>o[k]})) } catch { return '' } }
          const value = (s,scope) => s.replace(/\{\{(.*?)\}\}/g, (_,e) => { const v=expression(e,scope); return v == null ? '' : String(v) })
          const source = '<root xmlns:wx="wx">' + wxml.replace(/\b(scroll-x|scroll-y|autoplay|loop|controls|wx:else)(?=[\s/>])/g,'$1="true"').replace(/&(?!amp;|lt;|gt;|quot;|apos;)/g,'&amp;') + '</root>'
          const xml = new DOMParser().parseFromString(source,'application/xml')
          if (xml.querySelector('parsererror')) throw Error(xml.querySelector('parsererror').textContent)
          function children(parent, scope, target) {
            let chain = false
            for (const node of parent.childNodes) {
              if (node.nodeType === 3) { target.append(document.createTextNode(value(node.textContent,scope))); continue }
              if (node.nodeType !== 1) continue
              const cond = node.getAttribute('wx:if'), elif = node.getAttribute('wx:elif'), els = node.hasAttribute('wx:else')
              if (cond !== null) { chain = !!expression(cond.slice(2,-2),scope); if (!chain) continue }
              else if (elif !== null) { if (chain) continue; chain = !!expression(elif.slice(2,-2),scope); if (!chain) continue }
              else if (els) { if (chain) continue; chain = true }
              else chain = false
              const each = node.getAttribute('wx:for')
              if (each) { (expression(each.slice(2,-2),scope) || []).forEach((item,index) => render(node,{...scope,[node.getAttribute('wx:for-item')||'item']:item,index},target)); }
              else render(node,scope,target)
            }
          }
          function render(node,scope,target) {
            const tags = {view:'div',text:'span',image:'img',picker:'div','scroll-view':'div',swiper:'div','swiper-item':'div',block:'div'}
            if (node.tagName === 'swiper-item' && scope.index !== scope.current) return
            const el = document.createElement(tags[node.tagName] || node.tagName)
            for (const a of node.attributes) {
              if (a.name.startsWith('wx:') || /^(bind|catch)/.test(a.name)) continue
              if (a.name === 'disabled' && !expression(a.value.slice(2,-2),scope)) continue
              el.setAttribute(a.name,value(a.value,scope))
            }
            if (['scroll-view','swiper-item','block'].includes(node.tagName)) el.classList.add(node.tagName)
            if (node.tagName === 'image') el.style.objectFit = node.getAttribute('mode')==='aspectFill'?'cover':'contain'
            children(node,scope,el); target.append(el)
          }
          children(xml.documentElement,data,document.body)
        }, { wxml, data })
        await page.waitForFunction(() => [...document.images].every(i => i.complete), {timeout: 10000}).catch(() => {})
        const metrics = await page.evaluate(() => {
          window.scrollTo(0,document.body.scrollHeight)
          const rect = el => el ? {top:el.getBoundingClientRect().top,bottom:el.getBoundingClientRect().bottom,height:el.getBoundingClientRect().height}:null
          const fields = [...document.querySelectorAll('.filter-field')].map(rect)
          const captions = [...document.querySelectorAll('.asset-caption-copy')].map(copy => ({
            copy: rect(copy),
            card: rect(copy.closest('.asset-card')),
          }))
          return { overflow: document.documentElement.scrollWidth > innerWidth+1, action: rect(document.querySelector('.fixed-action')), content: rect(document.querySelector('.page-content')), stage: rect(document.querySelector('.stage')), footer: rect(document.querySelector('.home-footer')), fields, captions }
        })
        await page.screenshot({ path:path.join(output,sampleName+'-'+width+'.png'), fullPage:true })
        if (metrics.overflow) {
          console.log(await page.evaluate(() => [...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>e.className).slice(0,15)))
          throw Error(name+' horizontal overflow at '+width)
        }
        if (metrics.action && metrics.content && metrics.content.bottom > metrics.action.top+2) throw Error(name+' content covered at '+width)
        if (metrics.stage && metrics.stage.height < 70) throw Error('template screen too short at '+width)
        if (metrics.footer && metrics.footer.bottom > (width === 320 ? 568 : 812)) throw Error('home footer clipped at '+width)
        if (metrics.fields.length) {
          const tops = metrics.fields.map(f => Math.round(f.top))
          const heights = metrics.fields.map(f => Math.round(f.height))
          if (metrics.fields.length !== 2 || new Set(tops).size !== 1 || new Set(heights).size !== 1) {
            throw Error('filter fields are not aligned at '+width+': '+JSON.stringify(metrics.fields))
          }
        }
        for (const caption of metrics.captions) {
          if (caption.copy.height > 90 || caption.copy.width < caption.card.width * .44) {
            throw Error('asset caption collapsed at '+width+': '+JSON.stringify(caption))
          }
        }
        await page.evaluate(() => window.scrollTo(0,0))
        await page.screenshot({ path:path.join(output,sampleName+'-'+width+'.png'), fullPage:true })
      }
      await page.close()
    }
    console.log('PASS: four page layouts at 320/375/430px, no horizontal overflow or bottom action overlap. Previews: '+output)
  } finally { await browser.close() }
}
main().catch(e => { console.error(e); process.exit(1) })
