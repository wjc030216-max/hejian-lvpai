'use strict'
const $ = id => document.getElementById(id)
const state = { csrf: '', mediaOrigins: [], view: 'templates', lastLoadedView: '', items: [], selected: null, q: '', offset: 0, next: null, logKind: 'operations', logHours: 0, taskStatus: '', taskQ: '', refundStatus: '', refundQ: '', projectStatus: '', projectPayment: '', projectQ: '', userQ: '', monitorOwner: '', templateActor: '', templateScene: '', templateDuration: '', templateStatus: '', templateSort: 'updated', templateSize: 30, svcCategory: '', svcLevel: '', svcService: '', svcQ: '', svcHours: 0, selecting: false, checked: new Set(), loading: false, pendingLoad: false, controller: null, transactionQ: '', transactionBucket: '', transactionPayment: '', transactionHours: 0, transactionActionOnly: false, monitorTab: 'users', logTab: 'admin', auditQ: '', auditHours: 0, programQ: '', programHours: 0, noticeTimer: null, actionResolve: null }
const statusNames = {pending:'等待制作',running:'制作中',recovering:'恢复原任务',completed:'已完成',failed:'已确认失败',needs_review:'提交结果待核实'}
const kindNames = {character:'定妆照',video:'视频',image:'角色图 / 图片'}
const mediaKindNames={face:'人脸',three_view:'人物三视图',scene:'场景参考',costume:'服装参考',template_character:'模板角色'}
const paymentNames={allinpay:'通联支付','dev-bypass':'测试跳过',unconfigured:'未配置',disabled:'支付已关闭'}
const refundStatusNames={pending:'等待退款',submitting:'提交退款',processing:'退款处理中',needs_review:'退款待核实',succeeded:'退款成功',failed:'退款失败'}
const projectStatusNames={draft:'草稿',uploading:'上传照片',pending_payment:'待支付',portrait_submit:'提交定妆照',portrait_unknown:'定妆照待核实',portrait_processing:'生成定妆照',portrait_saving:'保存定妆照',portrait_retry:'准备重试定妆照',video_submit:'提交视频',video_unknown:'视频待核实',video_processing:'生成视频',video_retry:'准备重试视频',needs_review:'任务待核实',completed:'已完成',terminated:'生成失败',refund_pending:'退款中',refund_failed:'退款失败',refunded:'已退款归档',archived:'历史归档',expired:'数据已到期清理'}
const urlStateKeys=['offset','templateStatus','templateSort','templateSize','monitorTab','logTab','templateScene','templateActor','templateDuration','transactionQ','transactionBucket','transactionPayment','transactionHours','transactionActionOnly','taskStatus','taskQ','projectStatus','projectPayment','projectQ','userQ','monitorOwner','refundStatus','refundQ','auditQ','auditHours','programQ','programHours','svcCategory','svcLevel','svcService','svcQ','svcHours']
function element(tag, text, cls) { const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(cls)el.className=cls; return el }
function notice(message,type='success',duration=5000) {
  const box=$('notice'),text=$('notice-text');clearTimeout(state.noticeTimer)
  if(!message){box.hidden=true;text.textContent='';return}
  const tone=type===true?'error':(type===false?'success':type)
  text.textContent=message;box.hidden=false;box.dataset.type=tone
  if(tone!=='error'&&duration)state.noticeTimer=setTimeout(()=>notice(''),duration)
}
$('notice-close').onclick=()=>notice('')
function date(value) { return value ? new Date(value*1000).toLocaleString('zh-CN') : '—' }
function shortDate(value) { return value ? new Date(value*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—' }
function duration(value) { const seconds=Math.max(0,Number(value)||0);if(seconds<60)return Math.round(seconds)+' 秒';if(seconds<3600)return Math.floor(seconds/60)+' 分钟';if(seconds<86400)return Math.floor(seconds/3600)+' 小时';return Math.floor(seconds/86400)+' 天' }
function debounce(fn,delay=280){let timer=null;return(...args)=>{clearTimeout(timer);timer=setTimeout(()=>fn(...args),delay)}}
function liveSearch(input,apply,delay=280){input.addEventListener('input',debounce(()=>apply(input.value.trim()),delay))}
function applyUrlState(){
  Object.assign(state,{offset:0,monitorTab:'users',logTab:'admin',templateScene:'',templateActor:'',templateDuration:'',templateStatus:'',templateSort:'updated',templateSize:30,transactionQ:'',transactionBucket:'',transactionPayment:'',transactionHours:0,transactionActionOnly:false,taskStatus:'',taskQ:'',projectStatus:'',projectPayment:'',projectQ:'',userQ:'',monitorOwner:'',refundStatus:'',refundQ:'',auditQ:'',auditHours:0,programQ:'',programHours:0,svcCategory:'',svcLevel:'',svcService:'',svcQ:'',svcHours:0,q:''})
  const params=new URLSearchParams(location.search),view=params.get('view')
  if(['templates','transactions','monitor','logs'].includes(view))state.view=view
  if(params.get('q')!==null)state.q=params.get('q')
  for(const key of urlStateKeys){
    if(!params.has(key))continue
    const raw=params.get(key)
    if(key==='transactionActionOnly')state[key]=raw==='1'
    else if(['offset','transactionHours','auditHours','programHours','svcHours'].includes(key))state[key]=Math.max(0,Number(raw)||0)
    else state[key]=raw
  }
}
function syncUrl(push=false){
  const params=new URLSearchParams();params.set('view',state.view)
  if(state.q)params.set('q',state.q)
  for(const key of urlStateKeys){
    const value=state[key]
    if(value!==''&&value!==0&&value!==false&&value!==null&&value!==undefined)params.set(key,key==='transactionActionOnly'?'1':String(value))
  }
  if($('task-dialog').open)params.set('task',$('task-dialog').dataset.taskId||'')
  if($('project-dialog').open)params.set('project',$('project-dialog').dataset.projectId||'')
  const url=location.pathname+(params.toString()?'?'+params.toString():'')+location.hash
  history[push?'pushState':'replaceState'](null,'',url)
}
function copyButton(value,label='复制'){
  return button(label,async event=>{
    event.stopPropagation()
    try{await navigator.clipboard.writeText(String(value||''));notice('已复制')}
    catch{const area=element('textarea');area.value=String(value||'');document.body.append(area);area.select();document.execCommand('copy');area.remove();notice('已复制')}
    event.currentTarget?.blur()
  },'copy-button')
}
function redacted(value){
  if(Array.isArray(value))return value.map(redacted)
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,/token|secret|password/i.test(key)?'<redacted>':redacted(item)]))
  if(typeof value==='string')return value.replace(/([?&](?:access|token|signature|X-Amz-Signature|X-Tos-Signature)=)[^&\s]+/gi,'$1<redacted>')
  return value
}
function downloadCsv(filename,headers,rows){
  const quote=value=>{const raw=String(value??'');const safe=/^[=+\-@]/.test(raw)?"'"+raw:raw;return '"'+safe.replaceAll('"','""')+'"'}
  const csv='\ufeff'+[headers,...rows].map(row=>row.map(quote).join(',')).join('\r\n')
  const link=element('a');link.href=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));link.download=filename;link.click();URL.revokeObjectURL(link.href)
}
async function api(path, options={}) {
  const headers={...(options.headers||{})}; if(options.method&&options.method!=='GET') headers['X-CSRF-Token']=state.csrf
  if(options.body && !(options.body instanceof FormData)) { headers['Content-Type']='application/json'; options.body=JSON.stringify(options.body) }
  if(state.controller&&!options.signal)options.signal=state.controller.signal
  const response=await fetch('/admin/api/'+path,{...options,headers,credentials:'same-origin'})
  const body=await response.json().catch(()=>({message:response.status===413?'单个 ZIP 超过 256MB':'请求失败，请稍后重试'}))
  if(response.status===401 && path!=='login') { showLogin(); throw new Error('登录已过期，请重新登录') }
  if(!response.ok) throw new Error(body.message||'请求失败，请稍后重试')
  return body
}
function showLogin(){ state.controller?.abort();$('workspace').hidden=true; $('login').hidden=false; $('password').value='';document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());syncUrl() }
async function openWorkspace(session){
  state.csrf=session.csrf;state.mediaOrigins=session.media_origins||[];$('account').textContent='管理员 '+session.username;$('login').hidden=true;$('workspace').hidden=false
  const params=new URLSearchParams(location.search),taskId=params.get('task'),projectId=params.get('project')
  await load()
  if(projectId)openProjectDetail(projectId)
  else if(taskId)openTaskDetail(taskId)
  if(!state.autoTimer)state.autoTimer=setInterval(autoRefresh,12000)
}
$('login-form').onsubmit=async event=>{ event.preventDefault();const button=event.submitter;button.disabled=true; $('login-error').textContent=''; try{await openWorkspace(await api('login',{method:'POST',body:{username:$('username').value,password:$('password').value}}))}catch(error){$('login-error').textContent=error.message}finally{button.disabled=false} }
$('logout').onclick=async()=>{try{await api('logout',{method:'POST'});showLogin()}catch(error){notice(error.message,true)}}
function requestLoad(){load(false,true,true)}
$('navigation').onclick=event=>{const button=event.target.closest('[data-view]');if(!button)return;if(state.view!==button.dataset.view)state.q='';state.view=button.dataset.view;state.offset=0;state.monitorOwner='';requestLoad()}
$('refresh').onclick=()=>load(false,true,false)
function table(headers){const wrap=element('div',undefined,'table-wrap'),t=element('table'),head=element('thead'),row=element('tr'),body=element('tbody');const caption=element('caption',headers.join('、'),'sr-only');headers.forEach(x=>{const th=element('th',x);th.scope='col';row.append(th)});head.append(row);t.append(caption,head,body);wrap.append(t);const decorate=()=>[...body.rows].forEach(tr=>[...tr.cells].forEach((cell,i)=>cell.dataset.label=headers[i]||''));new MutationObserver(decorate).observe(body,{childList:true});return {wrap,body}}
function button(text,fn,cls){const b=element('button',text,cls);b.type='button';b.onclick=fn;return b}
function safeMedia(url){try{const u=new URL(url,location.origin);const allowed=[location.origin,...(state.mediaOrigins||[])];return allowed.includes(u.origin)?u.href:''}catch{return ''}}
async function textHash(value){
  if(!value||!crypto.subtle)return ''
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))
  return [...new Uint8Array(digest)].map(item=>item.toString(16).padStart(2,'0')).join('')
}
function askAction(options){
  if(state.actionResolve)state.actionResolve(null)
  $('action-title').textContent=options.title||'确认操作'
  $('action-message').textContent=options.message||''
  $('action-submit').textContent=options.confirmText||'确认'
  $('action-submit').className=options.danger?'primary danger':'primary'
  $('action-error').textContent=''
  const field=$('action-field')
  field.value=options.fieldValue||''
  field.placeholder=options.fieldPlaceholder||''
  $('action-field-label').hidden=!options.fieldTitle
  $('action-field-title').textContent=options.fieldTitle||''
  $('action-check').checked=false
  $('action-check-label').hidden=!options.checkText
  $('action-check-text').textContent=options.checkText||''
  $('action-dialog').showModal()
  setTimeout(()=>options.fieldTitle?field.focus():$('action-check').focus(),0)
  return new Promise(resolve=>{state.actionResolve=resolve})
}
function closeAction(result=null){
  const resolve=state.actionResolve;state.actionResolve=null
  $('action-dialog').close();resolve?.(result)
}
$('action-close').onclick=$('action-cancel').onclick=()=>closeAction(null)
$('action-dialog').addEventListener('cancel',event=>{event.preventDefault();closeAction(null)})
$('action-form').onsubmit=event=>{
  event.preventDefault()
  if(!$('action-check-label').hidden&&!$('action-check').checked){
    $('action-error').textContent='请先确认上述影响';return
  }
  closeAction({value:$('action-field').value.trim(),checked:$('action-check').checked})
}
function drawerLoading(body,label='正在读取详情…'){
  body.replaceChildren(element('div',label,'empty'))
}
function timeline(items,title){
  const section=element('section',undefined,'drawer-section')
  section.append(element('h3',title))
  const list=element('ol',undefined,'timeline')
  items.forEach(item=>{
    const node=element('li',undefined,'timeline-item'+(item.alert?' alert':''))
    node.append(element('span','','timeline-dot'),element('time',date(item.time),'timeline-time'),element('strong',item.title||'—'),item.note?element('p',item.note,'muted'):'')
    list.append(node)
  })
  if(!items.length)list.append(element('li','暂无记录','muted'))
  section.append(list);return section
}
function detailGrid(entries){
  const grid=element('div',undefined,'detail-grid')
  entries.forEach(([label,value,copy])=>{
    const item=element('div',undefined,'detail-field');item.append(element('span',label),element('strong',String(value??'—')))
    if(copy&&value)item.append(copyButton(value,'复制'))
    grid.append(item)
  })
  return grid
}
async function openTaskDetail(taskId,pushHistory=true){
  const dialog=$('task-dialog'),body=$('task-dialog-body')
  dialog.dataset.taskId=taskId
  if(!dialog.open)dialog.showModal()
  drawerLoading(body)
  if(pushHistory)syncUrl(true)
  try{
    const task=await api('tasks/'+encodeURIComponent(taskId))
    $('task-dialog-title').textContent=(kindNames[task.kind]||task.kind||'任务')+' · '+(statusNames[task.status]||task.status||'')
    body.replaceChildren()
    body.append(detailGrid([
      ['任务编号',task.task_id,true],['项目编号',task.project_id,true],['订单号',task.order_no],
      ['状态',statusNames[task.status]||task.status],['轮次',task.attempt||1],
      ['云任务号',task.cloud_task_id||'—',task.cloud_task_id],
      ['分段云任务',Object.entries(task.cloud_tasks||{}).map(([code,id])=>code+': '+id).join(' / ')||'—'],
      ['创建时间',date(task.created_at)],['完成时间',date(task.finished_at)]
    ]))
    if(task.error)body.append(element('div',task.error,'inline-error'))
    const refs=task.reference_images||[]
    const retryMedia=()=>openTaskDetail(taskId,false)
    if(refs.length)body.append(monitorMedia(refs,'最终参考图',retryMedia))
    else if((task.input_records||task.inputs||[]).length)body.append(monitorMedia(task.input_records||task.inputs,'输入图片',retryMedia))
    if((task.audit_inputs||[]).length&&refs.length)body.append(monitorMedia(task.audit_records||task.audit_inputs,'角色与服装参考素材（非最终 Seedream 输入）',retryMedia))
    if((task.outputs||[]).length)body.append(monitorMedia(task.outputs,'输出图片 / 视频',retryMedia))
    if(task.prompt){
      const section=element('details',undefined,'drawer-section prompt-section')
      section.open=true
      const promptText=Array.isArray(task.prompt)
        ? task.prompt.map((item,index)=>'【'+(item&&item.group?item.group:('片段'+(index+1)))+'】\n'+String(item&&item.text!==undefined?item.text:item||'')).join('\n\n')
        : String(task.prompt||'')
      const heading=element('summary');heading.append(element('strong','提示词'),element('span',promptText.length+' 字','muted'),copyButton(promptText,'复制提示词'))
      section.append(heading,element('pre',promptText,'prompt-text'))
      const imageNumbers=[...promptText.matchAll(/图片(\d+)为/g)].map(match=>Number(match[1]))
      const expected=imageNumbers.length?Math.max(...imageNumbers):0
      if(refs.length&&expected&&expected!==refs.length)section.append(element('p','提示词包含 '+expected+' 个图片编号，但实际引用 '+refs.length+' 张图片','inline-warning'))
      const hash=await textHash(promptText)
      if(hash)section.append(element('p','SHA-256 '+hash.slice(0,16)+'…','prompt-hash'))
      body.append(section)
    }
    const steps=Object.entries(task.steps||{}).map(([name,step])=>({
      title:name,time:step.finished_at||step.started_at||task.created_at,
      note:[step.status,step.attempt?('第 '+step.attempt+' 次'):'',step.error||''].filter(Boolean).join(' · '),
      alert:['failed','needs_review'].includes(step.status)
    }))
    body.append(timeline(steps,'任务步骤'))
    const events=(task.events||[]).map(item=>({
      title:statusNames[item.status]||item.status||'状态变化',time:item.created,note:item.message,
      alert:['failed','needs_review'].includes(item.status)
    }))
    body.append(timeline(events,'状态日志'))
    const raw=element('details',undefined,'drawer-section');raw.append(element('summary','原始任务数据'),element('pre',JSON.stringify(redacted(task),null,2),'metadata'));body.append(raw)
  }catch(error){body.replaceChildren(element('div',error.message,'empty'),button('重试',()=>openTaskDetail(taskId)))}
}
async function openProjectDetail(projectId,pushHistory=true){
  const dialog=$('project-dialog'),body=$('project-dialog-body')
  dialog.dataset.projectId=projectId
  if(!dialog.open)dialog.showModal()
  drawerLoading(body)
  if(pushHistory)syncUrl(true)
  try{
    const [project,monitor]=await Promise.all([
      api('projects/'+encodeURIComponent(projectId)),
      api('projects/'+encodeURIComponent(projectId)+'/monitor')
    ])
    $('project-dialog-title').textContent=project.template_name||monitor.template_name||'项目详情'
    body.replaceChildren()
    body.append(detailGrid([
      ['项目编号',project.project_id,true],['订单号',project.order_no,true],['用户编号',project.owner_id,true],
      ['业务状态',projectStatusNames[project.status]||project.status],['支付方式',paymentNames[project.payment_mode]||project.payment_mode||'—'],
      ['实付金额',money(project.amount_paid||project.amount_fen)],['创建时间',date(project.created_at)],['支付时间',date(project.paid_at)]
    ]))
    if(project.error)body.append(element('div',project.error,'inline-error'))
    const roles=project.roles||[]
    if(roles.length){
      const section=element('section',undefined,'drawer-section'),{wrap,body:roleBody}=table(['角色','来源','单人模式','人脸检查'])
      roles.forEach(role=>{const row=element('tr');row.append(element('td',role.display_name||role.character_id),element('td',role.source==='template'?'模板人物':'用户照片'),element('td',role.person_uid||'—'),element('td',role.check_status||'—'));roleBody.append(row)})
      section.append(element('h3','角色绑定'),wrap);body.append(section)
    }
    if(monitor.portrait_url||monitor.video_url)body.append(monitorMedia([monitor.portrait_url,monitor.video_url],'最终作品',()=>openProjectDetail(projectId,false)))
    if((monitor.tasks||[]).length){
      const section=element('section',undefined,'drawer-section');section.append(element('h3','制作任务'))
      monitor.tasks.forEach(task=>{
        const row=element('div',undefined,'task-link-row'),copy=element('div',undefined,'task-link-copy')
        copy.append(element('strong',(kindNames[task.kind]||task.kind)+' · '+(statusNames[task.status]||task.status)),element('small',task.task_id,'id-cell'))
        row.append(copy)
        row.append(button('查看任务',()=>openTaskDetail(task.task_id)))
        section.append(row)
      })
      body.append(section)
    }
    body.append(timeline((monitor.events||[]).map(event=>({title:event.title||event.kind,time:event.created})),'项目进度'))
    const raw=element('details',undefined,'drawer-section');raw.append(element('summary','原始项目数据'),element('pre',JSON.stringify(redacted(project),null,2),'metadata'));body.append(raw)
  }catch(error){body.replaceChildren(element('div',error.message,'empty'),button('重试',()=>openProjectDetail(projectId)))}
}
document.querySelectorAll('[data-close-dialog]').forEach(button=>button.onclick=()=>{
  const dialog=$(button.dataset.closeDialog);dialog.close()
  if(dialog.id==='project-dialog')dialog.dataset.projectId=''
  if(dialog.id==='task-dialog')dialog.dataset.taskId=''
  syncUrl()
})
$('task-dialog').addEventListener('close',()=>{syncUrl()})
$('project-dialog').addEventListener('close',()=>{syncUrl()})
function openDetail(title,data){$('detail-title').textContent=title;$('detail-content').textContent=JSON.stringify(redacted(data),null,2);$('detail-dialog').showModal()}
$('detail-close').onclick=()=>$('detail-dialog').close()
function addPagination(container,next){if(next!==null&&next!==undefined)container.append(button('下一页',()=>{state.offset=next;load()},'load-more'));if(state.offset)container.append(button('回到第一页',()=>{state.offset=0;load()},'load-more'))}
async function load(silent=false,force=false,pushHistory=false){
  if(state.loading){
    if(!force){state.pendingLoad=true;return}
    state.controller?.abort()
  }
  const controller=new AbortController();state.controller=controller;state.loading=true
  const titles={templates:['模板中心','管理模板素材、标准价格与用户端展示热度。'],transactions:['交易与退款','统一查看订单、支付、交付和退款状态，优先处理异常资金。'],monitor:['运营监控','从用户作品、制作任务和服务状态三个维度定位问题。'],logs:['日志中心','分别查看管理员操作、程序运行和服务日志。']}
  const [title,description]=titles[state.view],viewChanged=state.lastLoadedView!==state.view;$('page-title').textContent=title;$('breadcrumb').textContent=title;$('page-description').textContent=description;$('upload-open').hidden=state.view!=='templates'
  const bulk=$('bulk-toggle');if(bulk){bulk.hidden=state.view!=='templates';bulk.textContent=state.selecting?'退出多选':'多选操作';bulk.className=state.selecting?'active':'';bulk.onclick=()=>toggleSelect()}
  if(!silent&&viewChanged)$('page-title').focus({preventScroll:true})
  document.querySelectorAll('nav button[data-view]').forEach(b=>{const current=b.dataset.view===state.view;b.classList.toggle('active',current);b.setAttribute('aria-current',current?'page':'false')})
  const content=$('content'),scrollY=window.scrollY,hadContent=content.childElementCount>0
  if(!silent){notice('');if(!hadContent)content.replaceChildren(element('div','正在读取…','empty'))}
  content.setAttribute('aria-busy','true');$('refresh').disabled=true;$('load-indicator').hidden=false
  try{await ({templates:renderTemplates,transactions:renderTransactions,monitor:renderMonitorHub,logs:renderLogsHub}[state.view])();state.lastLoadedView=state.view;$('last-updated').textContent='更新于 '+shortDate(Date.now()/1000);syncUrl(pushHistory);refreshBadges()}
  catch(error){if(error.name==='AbortError')return;notice(error.message,'error');if(!hadContent)content.replaceChildren(element('div','暂时无法加载，可以点击右上角刷新。','empty'))}
  finally{if(state.controller===controller){state.controller=null;state.loading=false;content.removeAttribute('aria-busy');$('refresh').disabled=false;$('load-indicator').hidden=true;if(silent)requestAnimationFrame(()=>window.scrollTo(0,scrollY));if(state.pendingLoad){state.pendingLoad=false;setTimeout(()=>load(true),0)}}}
}
function templateQueryParams(withPaging=true){
  const params=new URLSearchParams()
  params.set('q',state.q)
  if(withPaging){
    params.set('offset',String(state.offset))
    params.set('size',String(state.templateSize||30))
    params.set('sort',state.templateSort||'updated')
  }
  if(state.templateStatus)params.set('status',state.templateStatus)
  if(state.templateActor)params.set('actor_count',state.templateActor)
  if(state.templateScene)params.set('scene',state.templateScene)
  if(state.templateDuration)params.set('duration',state.templateDuration)
  return params
}

function templatePager(container,total){
  const size=Number(state.templateSize)||30,pages=Math.max(1,Math.ceil(total/size)),current=Math.floor(state.offset/size)+1
  if(pages<=1)return
  const bar=element('div',undefined,'pager')
  if(current>1)bar.append(button('上一页',()=>{state.offset=Math.max(0,state.offset-size);load()},'ghost'))
  bar.append(element('span','第 '+current+' / '+pages+' 页 · 共 '+Number(total).toLocaleString()+' 个','pager-info'))
  if(current<pages)bar.append(button('下一页',()=>{state.offset=state.offset+size;load()},'ghost'))
  container.append(bar)
}

async function renderTemplates(){
  const [result,campaign]=await Promise.all([
    api('templates?'+templateQueryParams().toString()),
    api('free-quota').catch(()=>null)
  ])
  state.items=result.items;state.next=result.next_offset
  const content=$('content');content.replaceChildren()
  const counts=result.status_counts||{},filtered=!!(state.q||state.templateStatus||state.templateActor||state.templateScene||state.templateDuration)
  const metrics=element('div',undefined,'metrics')
  metrics.append(
    metricCard('模板总数',Number(counts.all||0).toLocaleString(),'全部素材'),
    metricCard('已上架',Number(counts.published||0).toLocaleString(),'游客端可见','teal'),
    metricCard('已下架',Number(counts.unpublished||0).toLocaleString(),'暂不展示','warn'),
    metricCard('当前筛选',Number(result.total||0).toLocaleString(),filtered?'已应用筛选条件':'未筛选','accent'))
  content.append(metrics)

  if(campaign){
    const bar=element('div',undefined,'campaign-bar')
    const copy=element('div',undefined,'campaign-copy')
    copy.append(element('span','今日免费体验名额'),element('strong',(campaign.remaining||0)+' / '+(campaign.total||0)))
    const form=element('form',undefined,'campaign-form')
    const input=element('input');input.type='number';input.min=0;input.max=100000;input.value=campaign.total;input.setAttribute('aria-label','每日免费名额')
    const save=element('button','保存','primary');save.type='submit'
    form.append(element('label','每日名额'),input,save)
    form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{const r=await api('free-quota',{method:'PATCH',body:{total:Number(input.value)}});notice(r.message);load()}catch(error){notice(error.message,true);save.disabled=false}}
    bar.append(copy,form,element('span',campaign.total>0?'每天 0 点重置；名额用完后该模板按标准价支付':'当前已关闭，游客全程按标准价支付','campaign-note'))
    content.append(bar)
  }

  const toolbar=element('form',undefined,'filter-bar')
  const search=element('input');search.type='search';search.placeholder='搜索模板名称、景区、类型';search.value=state.q;search.setAttribute('aria-label','搜索模板')
  const status=element('select');status.setAttribute('aria-label','上架状态');status.append(new Option('全部状态',''),new Option('已上架','1'),new Option('已下架','0'));status.value=state.templateStatus;status.onchange=()=>{state.templateStatus=status.value;state.offset=0;load()}
  const actor=element('select');actor.setAttribute('aria-label','人数筛选');actor.append(new Option('全部人数',''),new Option('1 人','1'),new Option('2 人','2'),new Option('3 人','3'));actor.value=state.templateActor;actor.onchange=()=>{state.templateActor=actor.value;state.offset=0;load()}
  const durationFilter=element('select');durationFilter.setAttribute('aria-label','时长筛选');durationFilter.append(new Option('全部时长',''),new Option('15 秒','15'),new Option('30 秒','30'));durationFilter.value=state.templateDuration;durationFilter.onchange=()=>{state.templateDuration=durationFilter.value;state.offset=0;load()}
  let scene=null
  if((result.scenes||[]).length>1){scene=element('select');scene.setAttribute('aria-label','景区筛选');scene.append(new Option('全部景区',''));result.scenes.forEach(v=>scene.append(new Option(v,v)));scene.value=state.templateScene;scene.onchange=()=>{state.templateScene=scene.value;state.offset=0;load()}}
  const sort=element('select');sort.setAttribute('aria-label','排序方式');sort.append(new Option('最近更新','updated'),new Option('热度从高到低','heat'),new Option('名称排序','name'));sort.value=state.templateSort;sort.onchange=()=>{state.templateSort=sort.value;state.offset=0;load()}
  const size=element('select');size.setAttribute('aria-label','每页数量');[30,50,100].forEach(n=>size.append(new Option(n+' 条/页',String(n))));size.value=String(state.templateSize||30);size.onchange=()=>{state.templateSize=Number(size.value);state.offset=0;load()}
  const submit=element('button','筛选');submit.type='submit';submit.className='primary'
  const reset=button('清空',()=>{Object.assign(state,{q:'',templateStatus:'',templateActor:'',templateScene:'',templateDuration:'',offset:0});load()},'ghost')

  toolbar.append(search,status,actor,durationFilter);if(scene)toolbar.append(scene);toolbar.append(sort,size,submit,reset,element('span',Number(result.total).toLocaleString()+' 个模板','result-count'))
  toolbar.onsubmit=e=>{e.preventDefault();state.q=search.value.trim();state.offset=0;load()}
  liveSearch(search,value=>{if(state.q!==value){state.q=value;state.offset=0;load()}})
  content.append(toolbar)

  const chips=element('div',undefined,'filter-chips')
  const addChip=(label,clear)=>{const chip=element('span',undefined,'chip');chip.append(element('span',label));const x=button('×',clear,'chip-x');x.setAttribute('aria-label','移除筛选 '+label);chip.append(x);chips.append(chip)}
  if(state.q)addChip('关键词：'+state.q,()=>{state.q='';state.offset=0;load()})
  if(state.templateStatus)addChip(state.templateStatus==='1'?'已上架':'已下架',()=>{state.templateStatus='';state.offset=0;load()})
  if(state.templateActor)addChip(state.templateActor+' 人',()=>{state.templateActor='';state.offset=0;load()})
  if(state.templateDuration)addChip(state.templateDuration+' 秒',()=>{state.templateDuration='';state.offset=0;load()})
  if(state.templateScene)addChip('景区：'+state.templateScene,()=>{state.templateScene='';state.offset=0;load()})
  if(chips.childElementCount)content.append(chips)

  if(!result.items.length){content.append(element('div','没有符合条件的模板。调整筛选后再试，或上传新的模板 ZIP。','empty'));return}
  const layout=element('div',undefined,state.selecting?'catalog-layout solo':'catalog-layout'),list=element('div')
  const headers=state.selecting?['','模板','配置','状态','热度']:['模板','配置','状态','热度']
  const {wrap,body}=table(headers);const preview=element('aside',undefined,'preview');layout.append(list);if(!state.selecting)layout.append(preview);list.append(wrap)
  let allCb=null,updateBar=()=>{},selectionBar=null
  const syncHeader=()=>{if(allCb)allCb.checked=result.items.length>0&&result.items.every(x=>state.checked.has(x.template_code))}
  const setChecked=on=>{const codes=result.items.map(x=>x.template_code);if(on)codes.forEach(c=>state.checked.add(c));else codes.forEach(c=>state.checked.delete(c));body.querySelectorAll('tr').forEach(r=>{const cb=r.querySelector('input[type=checkbox]');if(cb){const code=r.dataset.select;cb.checked=state.checked.has(code);r.classList.toggle('checked',cb.checked)}});syncHeader();updateBar()}
  if(state.selecting){
    const head=wrap.querySelector('thead tr th');head.className='check-cell';allCb=element('input');allCb.type='checkbox';allCb.setAttribute('aria-label','全选本页');allCb.onchange=()=>{setChecked(allCb.checked);updateBar()};head.replaceChildren(allCb);syncHeader()
    const bar=element('div',undefined,'selection-bar');const all=button('全选本页',()=>{setChecked(!allCb.checked)})
    const allFiltered=button('全选筛选结果',async()=>{try{const data=await api('templates/codes?'+templateQueryParams(false).toString());state.checked=new Set(data.codes||[]);body.querySelectorAll('tr').forEach(r=>{const cb=r.querySelector('input[type=checkbox]'),on=state.checked.has(r.dataset.select);if(cb){cb.checked=on;r.classList.toggle('checked',on)}});syncHeader();updateBar()}catch(error){notice(error.message,true)}})
    const heat=element('input');heat.type='number';heat.min=0;heat.max=100000000;heat.step=1;heat.placeholder='统一热度';heat.className='batch-heat';heat.setAttribute('aria-label','批量热度')
    const applyHeat=button('应用热度',()=>batchHeatChecked(heat.value),'primary')
    updateBar=()=>{all.textContent=allCb.checked?'取消本页全选':'全选本页';applyHeat.disabled=!state.checked.size;bar.replaceChildren(element('span','已选 '+state.checked.size+' 个 · 共 '+Number(result.total).toLocaleString()+' 个'),all,allFiltered,button('批量上架',()=>batchStatus(1)),button('批量下架',()=>batchStatus(0),'ghost'),heat,applyHeat,button('删除所选('+state.checked.size+')',deleteChecked,state.checked.size?'primary danger':''),button('取消',()=>toggleSelect()))};updateBar();selectionBar=bar
  }
  function choose(item,row){if(state.selecting)return;state.selected=item.template_code;body.querySelectorAll('tr').forEach(r=>r.classList.toggle('selected',r===row));renderPreview(preview,item)}
  result.items.forEach(item=>{
    const row=element('tr');row.dataset.select=item.template_code
    if(state.selecting){const cell=element('td',undefined,'check-cell'),cb=element('input');cb.type='checkbox';cb.checked=state.checked.has(item.template_code);cb.onchange=()=>{if(cb.checked)state.checked.add(item.template_code);else state.checked.delete(item.template_code);row.classList.toggle('checked',cb.checked);syncHeader();updateBar()};cell.append(cb);row.append(cell)}
    row.tabIndex=0;row.onkeydown=e=>{if(e.key==='Enter'){if(state.selecting){row.querySelector('input[type=checkbox]').click()}else choose(item,row)}}
    const name=element('td',undefined,'name-cell'),title=element('div',undefined,'template-name'),img=element('img',undefined,'thumb');img.src=safeMedia(item.cover);img.alt=item.video_name;img.loading='lazy';const text=element('div');text.append(element('strong',item.video_name),element('small',item.location_association+' · '+item.actor_count+' 人 · '+item.video_genre),element('small','标准价 ¥'+(item.standard_price_fen/100).toFixed(1)));title.append(img,text);name.append(title)
    const groups=(item.segments||[]).map(group=>group.code).filter(Boolean).join('/'),config=element('td');config.append(element('strong',(item.duration_seconds||15)+' 秒'),element('small',(item.segment_count||1)+' 段'+(groups?' · '+groups+' 组':'')+(item.portrait_group?' · 定妆 '+item.portrait_group:'')),element('small',String(item.video_specification||'')))
    const stateCell=element('td');stateCell.append(element('span',item.status?'已上架':'已下架','status '+(item.status?'on':'')))
    const heat=element('td');heat.append(element('div',Number(item.heat_base||0).toLocaleString(),'heat-value'),element('small','用户端排序值'));row.append(name,config,stateCell,heat)
    row.onclick=e=>{if(e.target.closest('input,button,a'))return;if(state.selecting){const cb=row.querySelector('input[type=checkbox]');cb.checked=!cb.checked;cb.onchange()}else choose(item,row)}
    body.append(row)
  })
  content.append(layout)
  if(selectionBar)content.insertBefore(selectionBar,layout)
  if(!state.selecting){const selected=result.items.find(x=>x.template_code===state.selected)||result.items[0];choose(selected,body.children[result.items.indexOf(selected)])}
  templatePager(list,result.total)
}

function money(fen){return '¥'+((Number(fen)||0)/100).toFixed(2)}
function tabStrip(items,active,onPick){
  const bar=element('div',undefined,'module-tabs')
  bar.setAttribute('role','toolbar');bar.setAttribute('aria-label','页面模块')
  items.forEach(([key,label,note])=>{const b=button(label,()=>onPick(key),active===key?'active':'');b.setAttribute('aria-pressed',String(active===key));if(note)b.append(element('small',note));bar.append(b)})
  return bar
}
function metricCard(label,value,note='',tone=''){
  const card=element('article','','metric-card '+tone)
  card.append(element('span',label),element('strong',String(value)),element('small',note))
  return card
}
async function renderTransactions(){
  const params=new URLSearchParams({offset:state.offset,q:state.transactionQ,bucket:state.transactionBucket,payment:state.transactionPayment,hours:state.transactionHours})
  if(state.transactionActionOnly)params.set('action','1')
  const result=await api('transactions?'+params.toString()),content=$('content');content.replaceChildren()
  const summary=result.summary||{},refunds=summary.refund_counts||{}
  const refundPending=Object.entries(refunds).filter(([key])=>!['succeeded','failed'].includes(key)).reduce((sum,[,v])=>sum+(v.count||0),0)
  const refundFailed=(refunds.failed||{}).count||0,refunded=(refunds.succeeded||{}).amount_fen||0
  const metrics=element('div',undefined,'metrics')
  metrics.append(
    metricCard('累计成交',money(summary.revenue_fen),'已支付订单总额','accent'),
    metricCard('近 24 小时',money(summary.revenue_24h_fen),'资金流入','teal'),
    metricCard('有效订单',Number(summary.paid_orders||0).toLocaleString(),'进入支付流程'),
    metricCard('退款待处理',refundPending.toLocaleString(),'处理中或待核实','warn'),
    metricCard('退款失败',refundFailed.toLocaleString(),'需要人工跟进','danger'),
    metricCard('累计退款',money(refunded),'已完成原路退款','muted'))
  content.append(metrics)

  const form=element('form',undefined,'filter-bar')
  const search=element('input');search.type='search';search.value=state.transactionQ;search.placeholder='订单号、项目号、用户、模板、退款流水';search.setAttribute('aria-label','搜索交易')
  const bucket=element('select');[['','全部交易'],['pending_payment','待支付'],['paid','已支付'],['processing','制作中'],['completed','已完成'],['failed','制作失败'],['refund_pending','退款处理中'],['refund_failed','退款失败'],['refunded','已退款']].forEach(([v,t])=>bucket.append(new Option(t,v)));bucket.value=state.transactionBucket
  const payment=element('select');[['','全部支付'],['allinpay','通联支付'],['dev-bypass','测试订单']].forEach(([v,t])=>payment.append(new Option(t,v)));payment.value=state.transactionPayment
  const hours=element('select');[['0','全部时间'],['24','近 24 小时'],['168','近 7 天'],['720','近 30 天'],['2160','近 90 天']].forEach(([v,t])=>hours.append(new Option(t,v)));hours.value=String(state.transactionHours)
  const action=element('label',undefined,'filter-check');const actionBox=element('input');actionBox.type='checkbox';actionBox.checked=state.transactionActionOnly;action.append(actionBox,document.createTextNode('仅看需处理'))
  const submit=element('button','筛选');submit.type='submit';submit.className='primary'
  const reset=button('清空条件',()=>{Object.assign(state,{transactionQ:'',transactionBucket:'',transactionPayment:'',transactionHours:0,transactionActionOnly:false,offset:0});load()})
  form.append(search,bucket,payment,hours,action,submit,reset,element('span','共 '+result.total.toLocaleString()+' 条','result-count'))
  form.onsubmit=e=>{e.preventDefault();state.transactionQ=search.value.trim();state.transactionBucket=bucket.value;state.transactionPayment=payment.value;state.transactionHours=Number(hours.value);state.transactionActionOnly=actionBox.checked;state.offset=0;load()}
  liveSearch(search,value=>{if(state.transactionQ!==value){state.transactionQ=value;state.offset=0;load()}})
  content.append(form)

  const {wrap,body}=table(['订单 / 项目','用户与模板','业务状态','实付金额','退款状态','时间','操作'])
  result.items.forEach(item=>{
    const row=element('tr'),identity=element('td'),subject=element('td'),projectState=element('td'),amount=element('td'),refund=element('td'),timeCell=element('td'),actions=element('td')
    identity.append(element('strong',item.order_no||'尚未下单'),element('div',item.project_id,'id-cell'))
    subject.append(element('div',item.owner_id||'—','id-cell'),element('div',(item.template_name||'—')+' · '+(item.actor_count||0)+'人','muted'))
    projectState.append(element('span',projectStatusNames[item.status]||item.status,'status '+(['terminated','refund_failed','needs_review'].includes(item.status)?'alert':item.status==='completed'?'on':'')))
    amount.append(element('strong',money(item.amount_paid||item.amount_fen)),element('small',paymentNames[item.payment_mode]||item.payment_mode||'—'))
    if(item.refund_no){
      refund.append(element('span',refundStatusNames[(item.refund||{}).refund_record_status||item.refund_status]||'处理中','status '+(item.needs_action?'alert':item.refund_status==='succeeded'?'on':'')))
      refund.append(element('small',money(item.refund_amount_fen)))
    }else refund.append(element('span','无退款','muted'))
    timeCell.append(element('div',date(item.paid_at||item.created_at)),element('small',item.order_no?'支付时间':'创建时间'))
    actions.append(button('详情',()=>openProjectDetail(item.project_id)))
    if(item.refund_no&&item.needs_action)actions.append(button('处理退款',()=>openRefundDialog({...item,...(item.refund||{}),status:(item.refund||{}).refund_record_status||item.refund_status},(item.refund||{}).refund_record_status==='failed'?'failed':'succeeded'),'primary'))
    row.append(identity,subject,projectState,amount,refund,timeCell,actions);body.append(row)
  })
  content.append(result.items.length?wrap:element('div','没有符合条件的交易记录。调整筛选条件后再试。','empty'))
  addPagination(content,result.next_offset)
}
async function renderProjects(){
  const params=new URLSearchParams({offset:state.offset,q:state.projectQ});if(state.projectStatus)params.set('status',state.projectStatus);if(state.projectPayment)params.set('payment',state.projectPayment)
  const result=await api('projects?'+params.toString()),content=$('content');content.replaceChildren()
  const toolbar=element('form',undefined,'toolbar'),search=element('input');search.placeholder='项目号、订单号、用户或模板';search.value=state.projectQ;search.setAttribute('aria-label','搜索项目')
  const status=element('select');status.setAttribute('aria-label','项目状态');status.append(new Option('全部状态',''));Object.entries(projectStatusNames).forEach(([k,v])=>status.append(new Option(v,k)));status.value=state.projectStatus
  const payment=element('select');payment.setAttribute('aria-label','支付方式');payment.append(new Option('全部支付',''),new Option('通联支付','allinpay'),new Option('测试跳过','dev-bypass'));payment.value=state.projectPayment
  status.onchange=()=>{state.projectStatus=status.value;state.offset=0;load()};payment.onchange=()=>{state.projectPayment=payment.value;state.offset=0;load()}
  const submit=element('button','搜索');submit.type='submit';toolbar.append(search,status,payment,submit,element('span',result.total+' 个项目','total'));toolbar.onsubmit=e=>{e.preventDefault();state.projectQ=search.value.trim();state.offset=0;load()};liveSearch(search,value=>{if(state.projectQ!==value){state.projectQ=value;state.offset=0;load()}});content.append(toolbar)
  if(!result.items.length){content.append(element('div','暂无符合条件的项目。','empty'));return}
  const {wrap,body}=table(['项目 / 订单','用户','模板','状态','金额','时间'])
  result.items.forEach(item=>{
    const row=element('tr'),identity=element('td'),statusCell=element('td')
    identity.append(element('div',item.project_id,'id-cell'),element('div',item.order_no||'未下单','muted'))
    statusCell.append(element('span',projectStatusNames[item.status]||item.status,'status '+(['needs_review','refund_failed','terminated'].includes(item.status)?'alert':item.status==='completed'?'on':'')))
    row.append(identity,element('td',item.owner_id||'—','id-cell'),element('td',(item.template_name||'—')+' · '+item.actor_count+'人'),statusCell,element('td','实付 ¥'+((item.amount_paid||0)/100).toFixed(2)),element('td',date(item.paid_at||item.created_at)))
    row.tabIndex=0
    row.onclick=()=>openProjectDetail(item.project_id)
    row.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openProjectDetail(item.project_id)}}
    body.append(row)
  })
  content.append(wrap);addPagination(content,result.next_offset)
}

function monitorMedia(items,label,onRetry=null){
  const section=element('div',undefined,'monitor-media-section');section.append(element('h4',label))
  const grid=element('div',undefined,'monitor-media-grid')
  ;(items||[]).forEach(item=>{
    const record=typeof item==='string'?{url:item}:(item||{})
    const safe=safeMedia(record.url);if(!safe)return
    const cell=element('div',undefined,'monitor-media-cell')
    const video=/\.mp4(?:[?#]|$)/i.test(safe)||/\/api\/v2\/results\//.test(safe)
    // Generated videos and portraits redirect to TOS. Admin pages restrict
    // media to this origin, so those assets must use the same-origin stream.
    const redirectedMedia=video||/\/api\/v2\/portraits\//.test(safe)
    const source=redirectedMedia?(()=>{const playback=new URL(safe);playback.searchParams.set('download','1');return playback.href})():safe
    const card=element('a',undefined,'monitor-media-card');card.href=source;card.target='_blank';card.rel='noopener'
    const media=element(video?'video':'img');media.src=source
    if(video){media.controls=true;media.preload='metadata';media.playsInline=true}else media.loading='lazy'
    const caption=[]
    if(record.group)caption.push('片段'+record.group)
    if(record.index)caption.push('图片'+record.index)
    if(record.kind)caption.push(mediaKindNames[record.kind]||record.kind)
    if(record.character_id)caption.push(record.character_id)
    if(record.note)caption.push(record.note)
    if(caption.length)card.append(element('span',caption.join(' · '),'monitor-media-caption'))
    card.append(media)
    if(onRetry){
      media.addEventListener('error',()=>{
        if(cell.querySelector('.monitor-media-retry'))return
        card.removeAttribute('href');card.removeAttribute('target');media.style.visibility='hidden'
        const message=element('div',undefined,'monitor-media-retry')
        message.append(element('span','媒体加载失败，请重新获取链接'),button('重新获取',onRetry,'primary'))
        cell.append(message)
      })
    }
    cell.append(card);grid.append(cell)
  })
  if(!grid.children.length)grid.append(element('span','无图片','muted'))
  section.append(grid);return section
}

function monitorTaskCard(task){
  const card=element('details',undefined,'monitor-task')
  const summary=element('summary',(kindNames[task.kind]||task.kind)+' · '+(statusNames[task.status]||task.status)+' · '+(task.task_id||''))
  card.append(summary)
  if(task.prompt){const promptText=Array.isArray(task.prompt)?task.prompt.map((item,index)=>'【'+(item&&item.group?item.group:('片段'+(index+1)))+'】\n'+String(item&&item.text!==undefined?item.text:item||'')).join('\n\n'):String(task.prompt||'');const block=element('div',undefined,'monitor-prompt');block.append(element('strong','提示词'),element('pre',promptText));card.append(block)}
  if((task.reference_images||[]).length){
    card.append(monitorMedia(task.reference_images,'定妆照最终参考图（提交给 Seedream 的顺序）'))
    if((task.audit_inputs||[]).length)card.append(monitorMedia(task.audit_inputs,'角色与服装参考素材（非最终 Seedream 输入）'))
  }else card.append(monitorMedia(task.input_records||task.inputs,'输入图片'))
  card.append(monitorMedia(task.outputs,'输出图片 / 视频'))
  if((task.events||[]).length){const history=element('details');history.append(element('summary','任务状态日志'));const list=element('div',undefined,'monitor-event-list');task.events.forEach(event=>list.append(element('div',date(event.created)+' · '+(event.status||'')+' · '+(event.message||''),'monitor-event')));history.append(list);card.append(history)}
  if(task.steps&&Object.keys(task.steps).length){const details=element('details');details.append(element('summary','任务步骤'),element('pre',JSON.stringify(task.steps,null,2),'metadata'));card.append(details)}
  if(task.error)card.append(element('p',task.error,'error'))
  return card
}

function monitorProjectCard(project){
  const card=element('article',undefined,'monitor-project')
  const head=element('div',undefined,'monitor-project-head')
  const title=element('div',undefined,'monitor-project-title');title.append(element('strong',project.template_name||project.template_code||'项目'),element('span',projectStatusNames[project.status]||project.status,'status '+(project.retention_expired?'alert':project.status==='completed'?'on':'')))
  head.append(title,button('项目详情',()=>openProjectDetail(project.project_id)))
  card.append(head,element('p',(project.project_id||'')+' · '+date(project.created_at)+' · 数据到期 '+date(project.retention_expires_at),'muted'))
  card.append(monitorMedia([project.portrait_url,project.video_url],'最终作品'))
  if((project.events||[]).length){const history=element('details');history.append(element('summary','项目进度日志'));const list=element('div',undefined,'monitor-event-list');project.events.forEach(event=>list.append(element('div',date(event.created)+' · '+(event.title||event.kind||''),'monitor-event')));history.append(list);card.append(history)}
  const roles=(project.roles||[]).filter(role=>role.photo_url)
  if(roles.length){const roleSection=element('div',undefined,'monitor-role-section');roleSection.append(element('h4','用户输入照片'));roles.forEach(role=>roleSection.append(monitorMedia([role.photo_url],(role.display_name||role.character_id||'角色'))));card.append(roleSection)}
  if((project.tasks||[]).length){const tasks=element('div',undefined,'monitor-tasks');tasks.append(element('h4','生成过程'));project.tasks.forEach(task=>tasks.append(monitorTaskCard(task)));card.append(tasks)}
  if(project.retention_expired)card.append(element('p','该用户数据已超过 90 天保留期，过程数据不可查看。','error'))
  return card
}

async function renderUserMonitor(root=$('content')){
  const params=new URLSearchParams({offset:state.offset,q:state.userQ})
  const result=await api('user-monitor?'+params.toString()),content=root;content.replaceChildren()
  const toolbar=element('form',undefined,'toolbar'),search=element('input');search.placeholder='搜索用户编号';search.value=state.userQ;search.setAttribute('aria-label','搜索用户编号')
  const submit=element('button','搜索');submit.type='submit';toolbar.append(search,submit,element('span',result.total+' 位用户','total'))
  toolbar.onsubmit=event=>{event.preventDefault();state.userQ=search.value.trim();state.offset=0;state.monitorOwner='';load()};liveSearch(search,value=>{if(state.userQ!==value){state.userQ=value;state.offset=0;state.monitorOwner='';load()}});content.append(toolbar)
  const {wrap,body}=table(['用户','项目 / 订单','最近活动','查看'])
  result.items.forEach(user=>{const row=element('tr'),action=element('td');action.append(button('查看过程',()=>{state.monitorOwner=user.owner_id;requestLoad()}));row.append(element('td',user.owner_id,'id-cell'),element('td',user.project_count+' / '+user.order_count),element('td',date(user.last_activity)),action);body.append(row)})
  content.append(result.items.length?wrap:element('div','暂无用户数据。','empty'));addPagination(content,result.next_offset)
  if(state.monitorOwner){
    const detail=await api('user-monitor/'+encodeURIComponent(state.monitorOwner)+'?tasks=0')
    const heading=element('div',undefined,'monitor-project-head');heading.append(element('h2','用户 '+state.monitorOwner),button('关闭',()=>{state.monitorOwner='';load()}))
    content.append(heading)
    detail.projects.forEach(project=>content.append(monitorProjectCard(project)))
    if(!detail.projects.length)content.append(element('div','该用户当前没有可查看的数据。','empty'))
  }
}
function toggleSelect(){state.selecting=!state.selecting;state.checked=new Set();state.offset=0;load()}
function codeChunks(codes,size=200){const out=[];for(let i=0;i<codes.length;i+=size)out.push(codes.slice(i,i+size));return out}
async function deleteChecked(){
  const codes=[...state.checked];if(!codes.length)return
  const answer=await askAction({title:'删除模板',message:'将删除所选 '+codes.length+' 个模板，并清除展示热度与选择记录。此操作不可撤销。',confirmText:'删除 '+codes.length+' 个模板',checkText:'我已核对模板名称，确认删除',danger:true})
  if(!answer)return
  try{
    let deleted=0
    for(const part of codeChunks(codes)){const result=await api('templates/batch-delete',{method:'POST',body:{codes:part}});deleted+=Number(result.deleted||0)}
    notice('已删除 '+deleted+' 个模板');state.selecting=false;state.checked=new Set();state.offset=0;load()
  }catch(error){notice(error.message,true)}
}
async function batchStatus(status){
  const codes=[...state.checked];if(!codes.length)return
  if(!status){
    const ok=await askAction({title:'批量下架',message:'将下架所选 '+codes.length+' 个模板，游客端立即不可见；已经开始的制作不受影响。',confirmText:'下架 '+codes.length+' 个模板'})
    if(!ok)return
  }
  try{
    for(const part of codeChunks(codes))await api('templates/batch-status',{method:'POST',body:{codes:part,status}})
    notice((status?'已上架 ':'已下架 ')+codes.length+' 个模板');load()
  }catch(error){notice(error.message,true)}
}
async function batchHeatChecked(value){
  const codes=[...state.checked];if(!codes.length)return
  const heat=Number(value);if(!Number.isInteger(heat)||heat<0||heat>100000000){notice('热度需为 0 至 1 亿的整数',true);return}
  try{
    for(const part of codeChunks(codes))await api('templates/batch-heat',{method:'POST',body:{codes:part,heat_base:heat}})
    notice('已设置 '+codes.length+' 个模板的热度');load()
  }catch(error){notice(error.message,true)}
}
function renderPreview(panel,item){
  panel.replaceChildren();const video=element('video');video.controls=true;video.preload='metadata';video.src=safeMedia(item.example);video.poster=safeMedia(item.cover);video.playsInline=true;panel.append(video,element('h2',item.video_name),element('p',item.story_hook,'muted'))
  const spec=element('div',undefined,'template-spec');spec.append(element('span',(item.duration_seconds||15)+' 秒','spec-badge'),element('span',(item.segment_count||1)+' 段','spec-badge'));if(item.portrait_group)spec.append(element('span','定妆 '+item.portrait_group+' 组','spec-badge gold'));panel.append(spec)
  if((item.segments||[]).length){const list=element('div',undefined,'segment-list');item.segments.forEach(group=>{const card=element('div',undefined,'segment-card'),roles=(group.characters||[]).map(character=>character.character_id).join('、');card.append(element('strong','片段 '+group.code),element('span',group.total_seconds+' 秒 · '+group.character_count+' 个角色'+(roles?'（'+roles+'）':'')+(group.has_storyboard?' · 已配置分镜':' · 缺分镜')));list.append(card)});panel.append(list)}
  const roles=element('p',(item.character_settings||[]).map((x,i)=>(i+1)+'号位：'+(x.display_name||x.character_id)).join('；'));panel.append(roles)
  const form=element('form');const base=element('input');base.type='number';base.min=0;base.max=100000000;base.step=1;base.value=item.heat_base;const baseLabel=element('label','展示热度');baseLabel.append(base)
  const explanation=element('p','用户端模板完全按这个热度从高到低展示。热度由管理员自行设置，不叠加真实选择次数。','heat-explanation');const save=element('button','保存热度','primary');save.type='submit';const toggleStatus=button(item.status?'下架模板':'重新上架',async()=>{if(item.status){const ok=await askAction({title:'下架模板',message:'下架后游客端立即不再展示该模板，已经开始的制作不受影响。',confirmText:'下架模板'});if(!ok)return}try{const result=await api('templates/'+encodeURIComponent(item.template_code),{method:'PATCH',body:{heat_base:Number(item.heat_base||0),status:item.status?0:1,edit_revision:item.edit_revision}});await load();notice(result.message)}catch(error){notice(error.message,true)}},item.status?'danger':'ghost');const actions=element('div',undefined,'preview-actions');actions.append(save,toggleStatus);form.append(baseLabel,explanation,actions);form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{const result=await api('templates/'+encodeURIComponent(item.template_code),{method:'PATCH',body:{heat_base:Number(base.value),edit_revision:item.edit_revision}});await load();notice(result.message)}catch(error){notice(error.message,true)}finally{save.disabled=false}};panel.append(form)
  const details=element('details'),summary=element('summary','查看基础元数据');const basic={...(item.source_metadata||{模板名称:item.video_name,人数:item.actor_count,角色:item.character_settings,景区:item.location_association,类型:item.video_genre,关系标签:item.relationship_tags,性别:item.lead_gender_tags,年龄:item.age_tags}),视频时长:item.duration_seconds||15,片段数量:item.segment_count||1,定妆片段组:item.portrait_group||'',分段摘要:item.segments||[]};const meta=element('pre',JSON.stringify(basic,null,2),'metadata');details.append(summary,meta);panel.append(details)
}
async function renderTasks(root=$('content')){
  const params=new URLSearchParams({offset:state.offset,status:state.taskStatus,q:state.taskQ})
  const result=await api('tasks?'+params.toString()),content=root;content.replaceChildren()
  const toolbar=element('form',undefined,'toolbar'),search=element('input');search.placeholder='项目、任务或云任务编号';search.value=state.taskQ;search.setAttribute('aria-label','搜索制作任务')
  const select=element('select');select.setAttribute('aria-label','制作状态');select.append(new Option('全部状态',''));Object.entries(statusNames).forEach(([k,v])=>select.append(new Option(v,k)));select.value=state.taskStatus;select.onchange=()=>{state.taskStatus=select.value;state.offset=0;load()}
  const submit=element('button','搜索');submit.type='submit';toolbar.append(search,select,submit,element('span',result.total+' 条记录','total'));toolbar.onsubmit=e=>{e.preventDefault();state.taskQ=search.value.trim();state.offset=0;load()};liveSearch(search,value=>{if(state.taskQ!==value){state.taskQ=value;state.offset=0;load()}});content.append(toolbar)
  const {wrap,body}=table(['任务 / 云任务','项目 / 订单','状态','轮次','开始时间','说明','操作'])
  result.items.forEach(item=>{
    const row=element('tr'),task=element('td'),project=element('td'),status=element('td'),actions=element('td')
    task.append(element('div',kindNames[item.kind]||item.kind),element('div',item.task_id,'id-cell'),element('div',item.cloud_task_id||'—','id-cell'))
    project.append(element('div',item.project_id||item.owner_id||'—','id-cell'),element('div',item.project_id?((item.template_name||item.template_code||'—')+' · '+(item.order_no||'未下单')):'角色图请求','muted'))
    if(item.project_status)project.append(element('div','项目状态：'+(projectStatusNames[item.project_status]||item.project_status),'muted'))
    status.append(element('span',statusNames[item.status]||item.status,'status '+(['needs_review','failed'].includes(item.status)?'alert':item.status==='completed'?'on':'')))
    actions.append(button('详情',()=>openTaskDetail(item.task_id)))
    if(item.status==='needs_review'){
      actions.append(button('确认未提交',()=>taskResolve(item,'not_submitted')))
      if(item.kind==='video')actions.append(button('关联视频任务',()=>taskResolve(item,'attach_video'),'primary'))
    }
    row.append(task,project,status,element('td',item.attempt),element('td',date(item.created_at)),element('td',item.message||'—','log-detail'),actions);body.append(row)
  })
  content.append(result.items.length?wrap:element('div','还没有制作任务。','empty'));addPagination(content,result.next_offset)
}
async function taskResolve(item,action){
  let cloudTaskId='',groupCode=''
  if(action==='attach_video'){
    const groups=item.video_groups||Object.keys(item.cloud_tasks||{})
    const multi=groups.length>1
    const answer=await askAction({title:'关联视频云任务',message:'请粘贴已在火山方舟创建的视频任务号。关联后会继续轮询原任务，不会重新创建视频。'+(multi?' 双段任务请按“A:任务号”格式填写，可选片段：'+groups.join('、'):''),fieldTitle:'视频云任务号',fieldPlaceholder:multi?'例如 B: cgt-xxxxxxxx':'例如 cgt-xxxxxxxx',confirmText:'确认关联'})
    if(!answer)return
    const raw=answer.value.trim()
    const match=raw.match(/^([A-Za-z][A-Za-z0-9_-]{0,15})\s*:\s*(.+)$/)
    groupCode=match?match[1].toUpperCase():''
    cloudTaskId=match?match[2].trim():raw
    if(!/^[A-Za-z0-9_-]{4,128}$/.test(cloudTaskId)){notice('视频任务号格式无效',true);return}
    if(multi&&!groups.includes(groupCode)){notice('请按 A:任务号 或 B:任务号 指定片段',true);return}
  }else{
    const answer=await askAction({title:'确认云端未提交',message:'请先在云端核实该请求确实没有提交成功。继续后系统会重新提交，并可能再次产生费用。',confirmText:'确认重新提交',checkText:'我已核实云端不存在该任务',danger:true})
    if(!answer)return
  }
  try{const result=await api('tasks/'+encodeURIComponent(item.task_id)+'/resolve',{
    method:'POST',body:{action,cloud_task_id:cloudTaskId,group_code:groupCode}
  });notice(result.message);await load()}catch(error){notice(error.message,true)}
}
const refundNames={pending:'等待提交',submitting:'提交中',processing:'处理中',needs_review:'待核实',succeeded:'退款成功',failed:'退款失败'}
const refundDialogState={action:'',refundNo:'',expectedStatus:''}
async function renderRefunds(){
  const params=new URLSearchParams({offset:state.offset,q:state.refundQ});if(state.refundStatus)params.set('status',state.refundStatus)
  const result=await api('refunds?'+params.toString()),content=$('content');content.replaceChildren()
  content.append(element('p','用户无需退款审核，生成失败后可自行选择重新生成或申请全额退款。只有渠道结果异常时才需要管理员核对原退款单。','section-note'))
  const toolbar=element('form',undefined,'toolbar'),search=element('input');search.placeholder='退款单、项目、订单或流水号';search.value=state.refundQ
  const select=element('select');select.append(new Option('全部状态',''));Object.entries(refundNames).forEach(([k,v])=>select.append(new Option(v,k)));select.value=state.refundStatus;select.onchange=()=>{state.refundStatus=select.value;state.offset=0;load()}
  const submit=element('button','搜索');submit.type='submit';toolbar.append(search,select,submit,element('span',result.total+' 条退款','total'));toolbar.onsubmit=e=>{e.preventDefault();state.refundQ=search.value.trim();state.offset=0;load()};content.append(toolbar)
  if(!result.items.length){content.append(element('div','暂无退款记录。','empty'));return}
  const {wrap,body}=table(['申请时间','退款单 / 项目','用户 / 模板','金额','状态','通联流水','操作'])
  result.items.forEach(item=>{
    const row=element('tr'),identity=element('td'),status=element('td'),provider=element('td'),actions=element('td')
    identity.append(element('div',item.refund_no,'id-cell'),element('div','项目 '+item.project_id,'muted'))
    status.append(element('span',refundNames[item.status]||item.status,'status '+(['failed','needs_review'].includes(item.status)?'alert':'on')))
    provider.append(element('div',item.provider_order_no||'—'),element('div',item.provider_trxid||'','id-cell'))
    if(item.media_cleanup&&item.media_cleanup!=='done')provider.append(element('div',item.media_cleanup==='failed'?'媒体清理待重试':'媒体清理排队中','id-cell'))
    if(['needs_review','failed'].includes(item.status)){
      actions.append(button('原单号重试',()=>openRefundDialog(item,'retry')),button('登记渠道已退款',()=>openRefundDialog(item,'succeeded'),'primary'),button('登记渠道失败',()=>openRefundDialog(item,'failed')))
    }else if(item.status==='processing'){
      actions.append(button('登记已到账',()=>openRefundDialog(item,'succeeded'),'primary'),button('登记失败',()=>openRefundDialog(item,'failed')))
    }
    row.append(element('td',date(item.requested_at)),identity,element('td',(item.owner_id||'—')+' · '+(item.template_name||'—'),'log-detail'),element('td','¥'+(item.amount_fen/100).toFixed(2)),status,provider,actions);body.append(row)
  })
  content.append(wrap);addPagination(content,result.next_offset)
}
function openRefundDialog(item,action){
  refundDialogState.action=action;refundDialogState.refundNo=item.refund_no;refundDialogState.expectedStatus=item.status
  $('refund-no-view').value=item.refund_no
  $('refund-provider-order').value=item.provider_order_no||''
  $('refund-provider-trxid').value=item.provider_trxid||''
  $('refund-note').value=''
  $('refund-error').textContent=''
  $('refund-submit').textContent=action==='retry'?'按原退款单号重试':action==='succeeded'?'登记渠道已退款':'登记渠道失败'
  $('refund-dialog').showModal()
}
$('refund-close').onclick=$('refund-cancel').onclick=()=>$('refund-dialog').close()
$('refund-form').onsubmit=async event=>{
  event.preventDefault();const note=$('refund-note').value.trim()
  if(!note||note.length<2){$('refund-error').textContent='请填写处理说明';return}
  const button=$('refund-submit');button.disabled=true;$('refund-error').textContent=''
  const body={action:refundDialogState.action,expected_status:refundDialogState.expectedStatus,note}
  if(refundDialogState.action!=='retry'){
    body.provider_order_no=$('refund-provider-order').value.trim()
    body.provider_trxid=$('refund-provider-trxid').value.trim()
    if(refundDialogState.action==='succeeded'&&!body.provider_trxid){$('refund-error').textContent='确认到账必须填写通联退款流水号';button.disabled=false;return}
  }
  try{const result=await api('refunds/'+encodeURIComponent(refundDialogState.refundNo),{method:'PATCH',body});$('refund-dialog').close();notice(result.message);await load()}
  catch(error){$('refund-error').textContent=error.message}finally{button.disabled=false}
}
async function renderMonitor(){const [summary,monitor]=await Promise.all([api('overview'),api('monitor')]),content=$('content');content.replaceChildren();const metrics=element('div',undefined,'metrics');const refundPending=Object.entries(summary.refunds||{}).filter(([k])=>k!=='failed').reduce((sum,[,v])=>sum+v.count,0),refundFailed=(summary.refunds&&summary.refunds.failed&&summary.refunds.failed.count)||0,projectCount=Object.values(summary.projects||{}).reduce((sum,value)=>sum+value,0);[['项目总数',projectCount],['已支付未启动',summary.paid_not_started||0],['待核实任务',summary.tasks.needs_review||0],['退款待处理 / 失败',refundPending+' / '+refundFailed],['上架 / 全部模板',summary.published+' / '+summary.templates],['今日免费剩余',summary.free_quota?summary.free_quota.remaining+' / '+summary.free_quota.total:'未开启']].forEach(([name,value])=>{const m=element('div',undefined,'metric');m.append(element('span',name),element('strong',String(value)));metrics.append(m)});content.append(metrics,element('h2','服务连通情况'));const {wrap,body}=table(['服务','当前状态','响应耗时']);monitor.items.forEach(item=>{const row=element('tr'),status=element('td');status.append(element('span',item.ok?'正常':'需要检查','status '+(item.ok?'on':'alert')));row.append(element('td',item.name),status,element('td',item.latency_ms+' 毫秒'));body.append(row)});content.append(wrap,element('p',monitor.note+' 最近检查：'+date(monitor.checked_at),'section-note'));const disk=summary.disk;content.append(element('h2','管理节点存储'),element('p','已用 '+(disk.used/1024**3).toFixed(1)+' GB / 总计 '+(disk.total/1024**3).toFixed(1)+' GB，可用 '+(disk.free/1024**3).toFixed(1)+' GB。','section-note'),element('p','手动展示基数合计 '+summary.display_baseline.toLocaleString()+'，不计入上述真实选择次数（按模板去重）。','section-note'))}
async function renderLogs(){const result=await api('logs?kind='+state.logKind+'&hours='+state.logHours+'&offset='+state.offset),content=$('content');content.replaceChildren();const toolbar=element('div',undefined,'toolbar');[['operations','管理员操作'],['progress','制作进度'],['tasks','制作状态日志']].forEach(([kind,label])=>toolbar.append(button(label,()=>{state.logKind=kind;state.offset=0;load()},state.logKind===kind?'primary':'')));const hours=element('select');hours.append(new Option('全部时间','0'),new Option('近 24 小时','24'),new Option('近 7 天','168'),new Option('近 30 天','720'),new Option('近 90 天','2160'));hours.value=String(state.logHours);hours.onchange=()=>{state.logHours=Number(hours.value);state.offset=0;load()};toolbar.append(hours);content.append(toolbar)
  if(state.logKind==='progress'){const {wrap,body}=table(['时间','作品编号','进展']);result.items.forEach(item=>{const row=element('tr');row.append(element('td',date(item.created)),element('td',item.project_id,'id-cell'),element('td',item.title||item.kind,'log-detail'));body.append(row)});content.append(result.items.length?wrap:element('div','暂无制作进度。游客下单制作后会自动记录。','empty'));addPagination(content,result.next_offset);return}
  const {wrap,body}=table(['时间','操作 / 制作类型','对象','详情']);result.items.forEach(item=>{const row=element('tr');let detail=item.message||statusNames[item.status]||'';if(item.detail){try{const data=JSON.parse(item.detail);detail=data.after?'热度：'+data.before.heat_base+' → '+data.after.heat_base+'；'+(data.after.status?'已上架':'已下架'):data.name?'模板：'+data.name+'；'+(data.duration_seconds?data.duration_seconds+' 秒 / '+(data.segment_count||1)+' 段；':'')+(data.status||'已上传'):item.actor+' 登录'}catch{detail='已记录'}}row.append(element('td',date(item.created)),element('td',item.action||kindNames[item.kind]||item.kind),element('td',item.target||item.task_id||item.actor,'id-cell'),element('td',detail,'log-detail'));body.append(row)});content.append(result.items.length?wrap:element('div','暂无日志。模板修改和制作状态变化后会自动记录。','empty'));addPagination(content,result.next_offset)}
const svcCategoryNames={login:'登录',payment:'支付',image:'生图',video:'视频生成',asset:'资产库',tos:'TOS',file:'文件',face:'人脸',orchestration:'编排',admin:'管理端',api:'接口请求',system:'系统'}
const svcLevelNames={info:'信息',warning:'警告',error:'错误'}
async function renderServiceLogs(root=$('content')){
  const params=new URLSearchParams({offset:state.offset,hours:String(state.svcHours)});if(state.svcCategory)params.set('category',state.svcCategory);if(state.svcLevel)params.set('level',state.svcLevel);if(state.svcService)params.set('service',state.svcService);if(state.svcQ)params.set('q',state.svcQ)
  const result=await api('service-logs?'+params.toString()),content=root;content.replaceChildren();const toolbar=element('form',undefined,'filter-bar')
  const category=element('select');category.setAttribute('aria-label','日志类别');category.append(new Option('全部类别',''));Object.entries(svcCategoryNames).forEach(([k,v])=>category.append(new Option(v,k)));category.value=state.svcCategory;category.onchange=()=>{state.svcCategory=category.value;state.offset=0;load()}
  const level=element('select');level.setAttribute('aria-label','日志级别');level.append(new Option('全部级别',''));Object.entries(svcLevelNames).forEach(([k,v])=>level.append(new Option(v,k)));level.value=state.svcLevel;level.onchange=()=>{state.svcLevel=level.value;state.offset=0;load()}
  const search=element('input');search.placeholder='搜索内容、请求ID、项目ID或任务ID';search.value=state.svcQ;search.setAttribute('aria-label','搜索日志')
  const hours=element('select');hours.append(new Option('全部时间','0'),new Option('近 24 小时','24'),new Option('近 7 天','168'),new Option('近 30 天','720'),new Option('近 90 天','2160'));hours.value=String(state.svcHours);hours.onchange=()=>{state.svcHours=Number(hours.value);state.offset=0;load()}
  const submit=element('button','搜索');submit.type='submit';const exportButton=button('导出当前页',()=>downloadCsv('service-logs.csv',['时间','请求ID','项目ID','任务ID','服务','类别','级别','请求','状态','耗时ms','错误类型','内容','上下文'],result.items.map(item=>[date(item.created),item.request_id,item.project_id,item.task_id,item.service,svcCategoryNames[item.category]||item.category,svcLevelNames[item.level]||item.level,[item.method,item.path].filter(Boolean).join(' ').trim(),item.status_code||'',item.duration_ms||'',item.error_type||'',item.message,item.context])));toolbar.append(category,level,hours,search,submit,exportButton,element('span',result.total+' 条日志','total'));toolbar.onsubmit=e=>{e.preventDefault();state.svcQ=search.value.trim();state.offset=0;load()};liveSearch(search,value=>{if(state.svcQ!==value){state.svcQ=value;state.offset=0;load()}});content.append(toolbar)
  if(!result.items.length){content.append(element('div','暂无服务日志。各服务上报后会自动汇总到这里。','empty'));return}
  const {wrap,body}=table(['时间','请求 / 对象','服务','类别','级别','内容'])
  result.items.forEach(item=>{
    const row=element('tr'),levelCell=element('td');levelCell.append(element('span',svcLevelNames[item.level]||item.level,'status '+(item.level==='error'?'alert':item.level==='warning'?'':'on')))
    const message=element('td',undefined,'log-detail');message.append(element('div',item.message||'—'))
    const trace=element('div',undefined,'id-cell')
    if(item.request_id)trace.append(element('div','请求 '+item.request_id))
    if(item.project_id)trace.append(element('div','项目 '+item.project_id))
    if(item.task_id)trace.append(element('div','任务 '+item.task_id))
    if(item.method||item.path)trace.append(element('div',[item.method,item.path,item.status_code?('· '+item.status_code):'',item.duration_ms?('· '+item.duration_ms+'ms'):''].filter(Boolean).join(' ')))
    if(item.error_type)trace.append(element('div','错误 '+item.error_type))
    if(item.context){const details=element('details'),context=element('pre',item.context,'log-context');details.append(element('summary','查看上下文'),context);message.append(details)}
    const traceCell=element('td');traceCell.append(trace)
    row.append(element('td',date(item.created)),traceCell,element('td',item.service),element('td',svcCategoryNames[item.category]||item.category),levelCell,message);body.append(row)
  })
  content.append(wrap);addPagination(content,result.next_offset)
}

async function renderMonitorStatus(root){
  const [summary,monitor]=await Promise.all([api('overview'),api('monitor')])
  root.replaceChildren()
  const refundPending=Object.entries(summary.refunds||{}).filter(([key])=>key!=='failed').reduce((sum,[,v])=>sum+v.count,0)
  const refundFailed=(summary.refunds&&summary.refunds.failed&&summary.refunds.failed.count)||0
  const projectCount=Object.values(summary.projects||{}).reduce((sum,value)=>sum+value,0)
  const queues=summary.queue||{}
  const queued=Object.values(queues).reduce((sum,item)=>sum+Number(item.count||0),0)
  const oldest=Math.max(0,...Object.values(queues).map(item=>Number(item.oldest_age_seconds||0)))
  const metrics=element('div',undefined,'metrics')
  metrics.append(
    metricCard('项目总数',projectCount.toLocaleString(),'全部制作项目'),
    metricCard('已支付未启动',Number(summary.paid_not_started||0).toLocaleString(),'需要关注启动延迟','warn'),
    metricCard('活动队列',queued.toLocaleString(),oldest?'最老等待 '+duration(oldest):'当前无积压',queued?'warn':'teal'),
    metricCard('待核实任务',Number((summary.tasks||{}).needs_review||0).toLocaleString(),'需要人工确认','danger'),
    metricCard('退款待处理 / 失败',refundPending+' / '+refundFailed,'资金异常优先处理','warn'),
    metricCard('已上架模板',summary.published+' / '+summary.templates,'模板可用率'))
  root.append(metrics,element('div','服务连通情况','section-title'))
  const {wrap,body}=table(['服务','当前状态','响应耗时'])
  monitor.items.forEach(item=>{const row=element('tr'),status=element('td');status.append(element('span',item.ok?'正常':'需要检查','status '+(item.ok?'on':'alert')));row.append(element('td',item.name),status,element('td',item.latency_ms+' 毫秒'));body.append(row)})
  root.append(wrap,element('p',monitor.note+' 最近检查：'+date(monitor.checked_at),'section-note'))
  const disk=summary.disk
  root.append(element('div','管理节点存储','section-title'),element('p','已用 '+(disk.used/1024**3).toFixed(1)+' GB / 总计 '+(disk.total/1024**3).toFixed(1)+' GB，可用 '+(disk.free/1024**3).toFixed(1)+' GB。','section-note'),element('p','当前模板手工热度合计 '+summary.display_baseline.toLocaleString()+'，用户端按该热度从高到低排序。','section-note'))
}

async function renderMonitorHub(){
  const content=$('content');content.replaceChildren()
  const tabs=tabStrip([['users','用户与作品','查过程与成品'],['tasks','制作任务','查异常与云任务'],['system','系统状态','服务与容量']],state.monitorTab,key=>{state.monitorTab=key;state.offset=0;state.monitorOwner='';requestLoad()})
  const panel=element('section',undefined,'module-panel')
  content.append(tabs,panel)
  if(state.monitorTab==='tasks')await renderTasks(panel)
  else if(state.monitorTab==='system')await renderMonitorStatus(panel)
  else await renderUserMonitor(panel)
}

async function renderAdminLogs(root){
  const params=new URLSearchParams({kind:'operations',q:state.auditQ,hours:String(state.auditHours),offset:state.offset})
  const result=await api('logs?'+params.toString());root.replaceChildren()
  const form=element('form',undefined,'filter-bar'),search=element('input');search.type='search';search.value=state.auditQ;search.placeholder='管理员、动作、对象或详情';search.setAttribute('aria-label','搜索管理员日志')
  const hours=element('select');[['0','全部时间'],['24','近 24 小时'],['168','近 7 天'],['720','近 30 天'],['2160','近 90 天']].forEach(([v,t])=>hours.append(new Option(t,v)));hours.value=String(state.auditHours)
  const submit=element('button','筛选');submit.type='submit';submit.className='primary'
  const exportButton=button('导出当前页',()=>downloadCsv('admin-logs.csv',['时间','管理员','动作','对象','详情'],result.items.map(item=>[date(item.created),item.actor,item.action,item.target,item.detail])))
  form.append(search,hours,submit,exportButton,element('span','共 '+result.total.toLocaleString()+' 条','result-count'));form.onsubmit=e=>{e.preventDefault();state.auditQ=search.value.trim();state.auditHours=Number(hours.value);state.offset=0;load()};liveSearch(search,value=>{if(state.auditQ!==value){state.auditQ=value;state.offset=0;load()}});root.append(form)
  const {wrap,body}=table(['时间','管理员','动作','对象','详情'])
  result.items.forEach(item=>{const row=element('tr');let detail=item.detail||'';try{const data=JSON.parse(item.detail);detail=data.after?'热度：'+data.before.heat_base+' → '+data.after.heat_base:data.name?'模板：'+data.name+'；'+(data.duration_seconds?data.duration_seconds+' 秒 / '+(data.segment_count||1)+' 段；':'')+(data.status||'已上传'):data.action||''}catch{}row.append(element('td',date(item.created)),element('td',item.actor||'—'),element('td',item.action||'—'),element('td',item.target||'—','id-cell'),element('td',detail,'log-detail'));body.append(row)})
  root.append(result.items.length?wrap:element('div','暂无管理员日志。','empty'));addPagination(root,result.next_offset)
}

async function renderProgramLogs(root){
  const params=new URLSearchParams({kind:'program',q:state.programQ,hours:String(state.programHours),offset:state.offset})
  const result=await api('logs?'+params.toString());root.replaceChildren()
  const form=element('form',undefined,'filter-bar'),search=element('input');search.type='search';search.value=state.programQ;search.placeholder='项目、任务、类别或消息';search.setAttribute('aria-label','搜索程序日志')
  const hours=element('select');[['0','全部时间'],['24','近 24 小时'],['168','近 7 天'],['720','近 30 天'],['2160','近 90 天']].forEach(([v,t])=>hours.append(new Option(t,v)));hours.value=String(state.programHours)
  const submit=element('button','筛选');submit.type='submit';submit.className='primary'
  const exportButton=button('导出当前页',()=>downloadCsv('program-logs.csv',['时间','来源','对象','类别','状态','消息'],result.items.map(item=>[date(item.created),item.source,item.subject,item.category,item.status,item.message])))
  form.append(search,hours,submit,exportButton,element('span','共 '+result.total.toLocaleString()+' 条','result-count'));form.onsubmit=e=>{e.preventDefault();state.programQ=search.value.trim();state.programHours=Number(hours.value);state.offset=0;load()};liveSearch(search,value=>{if(state.programQ!==value){state.programQ=value;state.offset=0;load()}});root.append(form)
  const {wrap,body}=table(['时间','来源','对象','类别','状态','消息'])
  result.items.forEach(item=>{const row=element('tr'),stateCell=element('td');stateCell.append(element('span',statusNames[item.status]||item.status||'记录','status '+(['failed','needs_review'].includes(item.status)?'alert':'on')));row.append(element('td',date(item.created)),element('td',item.source||'—'),element('td',item.subject||'—','id-cell'),element('td',kindNames[item.category]||item.category||'—'),stateCell,element('td',item.message||'—','log-detail'));body.append(row)})
  root.append(result.items.length?wrap:element('div','暂无程序日志。','empty'));addPagination(root,result.next_offset)
}

async function renderLogsHub(){
  const content=$('content');content.replaceChildren()
  const tabs=tabStrip([['admin','管理员日志','操作与审计'],['program','程序日志','任务与作品进度'],['service','服务日志','登录支付生图视频']],state.logTab,key=>{state.logTab=key;state.offset=0;requestLoad()})
  const panel=element('section',undefined,'module-panel')
  content.append(tabs,panel)
  if(state.logTab==='program')await renderProgramLogs(panel)
  else if(state.logTab==='service')await renderServiceLogs(panel)
  else await renderAdminLogs(panel)
}

const uploadState={files:[],rows:[],running:false,stop:false,controller:null}
function updateUploadSummary(){
  const statuses=uploadState.files.map(file=>file.status),done=statuses.filter(x=>['uploaded','processing','skipped','failed','invalid'].includes(x)).length
  const uploaded=statuses.filter(x=>['uploaded','processing'].includes(x)).length,skipped=statuses.filter(x=>x==='skipped').length,failed=statuses.filter(x=>['failed','invalid'].includes(x)).length
  $('upload-progress').max=Math.max(uploadState.files.length,1);$('upload-progress').value=done
  $('upload-summary').textContent=`已处理 ${done} / ${uploadState.files.length} · 成功 ${uploaded} · 跳过 ${skipped} · 失败 ${failed}`
  $('upload-submit').disabled=uploadState.running||!statuses.some(x=>x==='pending'||x==='failed')
  $('upload-submit').textContent=done?'继续或重试上传':'开始上传'
}
function setUploadStatus(index,status,message){
  const entry=uploadState.files[index];entry.status=status;entry.message=message
  const label=uploadState.rows[index];label.textContent=message;label.className='upload-state '+status
  updateUploadSummary()
}
function resetUploadSelection(){
  const list=$('upload-queue');list.replaceChildren();uploadState.files=[];uploadState.rows=[]
  for(const file of $('archive-file').files){
    const row=element('li'),name=element('span',file.name,'upload-filename'),status=element('span','等待上传','upload-state')
    row.append(name,status);list.append(row);uploadState.files.push({file,status:'pending',message:''});uploadState.rows.push(status)
    const index=uploadState.files.length-1
    if(!file.name.toLowerCase().endsWith('.zip'))setUploadStatus(index,'invalid','只接受 ZIP')
    else if(file.size>=256*1024**2)setUploadStatus(index,'invalid','文件超过 256MB')
  }
  $('upload-batch').hidden=!uploadState.files.length;$('upload-error').textContent='';updateUploadSummary()
}
function stopUpload(){
  if(!uploadState.running)return
  uploadState.stop=true;uploadState.controller?.abort();$('upload-stop').disabled=true;$('upload-stop').textContent='正在停止…'
}
$('upload-open').onclick=()=>{
  const select=$('upload-mode');select.replaceChildren(new Option('批量新增模板',''))
  state.items.forEach(item=>select.append(new Option('替换：'+item.video_name,item.template_code)))
  $('archive-file').multiple=true;$('archive-file').value='';resetUploadSelection();$('upload-dialog').showModal()
}
$('upload-mode').onchange=()=>{$('archive-file').multiple=!$('upload-mode').value;$('archive-file').value='';resetUploadSelection()}
$('archive-file').onchange=resetUploadSelection
$('upload-close').onclick=()=>{if(uploadState.running)stopUpload();else $('upload-dialog').close()}
$('upload-stop').onclick=stopUpload
$('upload-dialog').addEventListener('cancel',event=>{if(uploadState.running){event.preventDefault();stopUpload()}})
$('upload-form').onsubmit=async event=>{
  event.preventDefault()
  if(uploadState.running||!uploadState.files.length)return
  const replacement=$('upload-mode').value
  if(replacement&&uploadState.files.length!==1){$('upload-error').textContent='替换版本时只能选择一个 ZIP';return}
  uploadState.running=true;uploadState.stop=false;$('upload-error').textContent=''
  $('archive-file').disabled=true;$('upload-mode').disabled=true;$('upload-stop').hidden=false;$('upload-stop').disabled=false
  for(let index=0;index<uploadState.files.length;index++){
    const entry=uploadState.files[index]
    if(uploadState.stop)break
    if(!['pending','failed'].includes(entry.status))continue
    setUploadStatus(index,'uploading','正在上传…')
    const form=new FormData();form.append('file',entry.file)
    if(replacement)form.append('template_code',replacement);else form.append('batch_upload','1')
    uploadState.controller=new AbortController()
    try{
      const result=await api('templates/upload',{method:'POST',body:form,signal:uploadState.controller.signal})
      setUploadStatus(index,result.status==='skipped'?'skipped':(result.status==='processing'?'processing':'uploaded'),
        result.status==='skipped'?'同景区同名称模板已存在，跳过':
        result.status==='processing'?'已上传，正在后台压缩 · 完成后自动上架':
        '已自动上架 · '+(result.duration_seconds||15)+' 秒 · '+(result.segment_count||1)+' 段')
    }catch(error){
      if(error.name==='AbortError'){setUploadStatus(index,'pending','已停止，可重试');break}
      setUploadStatus(index,'failed',error.message)
      if(error.message.includes('登录已过期')){uploadState.stop=true;break}
    }finally{uploadState.controller=null}
  }
  uploadState.running=false;$('archive-file').disabled=false;$('upload-mode').disabled=false;$('upload-stop').hidden=true;$('upload-stop').textContent='停止上传'
  updateUploadSummary()
  const count=uploadState.files.filter(entry=>entry.status==='uploaded').length
  if(count){state.q='';state.offset=0;await load();notice(`已处理 ${count} 个模板`)}
}
function refreshBadges(){
  if(!state.csrf)return Promise.resolve()
  return api('overview').then(summary=>{
    const setBadge=(view,count,urgent=false)=>{
      const nav=document.querySelector('nav button[data-view="'+view+'"]');if(!nav)return
      let badge=nav.querySelector('.nav-badge');if(!badge){badge=element('span','','nav-badge');nav.append(badge)}
      badge.textContent=count>99?'99+':String(count);badge.hidden=!count;badge.classList.toggle('urgent',urgent)
    }
    const review=Number((summary.tasks||{}).needs_review||0)
    const failedRefunds=Number(((summary.refunds||{}).failed||{}).count||0)
    setBadge('monitor',review,review>0)
    setBadge('transactions',failedRefunds,failedRefunds>0)
  }).catch(()=>{})
}
function autoRefresh(){
  if(document.hidden||state.loading||document.querySelector('dialog[open]'))return
  const eligible=state.view==='monitor'&&(state.monitorTab==='tasks'||state.monitorTab==='system'||(state.monitorTab==='users'&&!state.monitorOwner))
  if(eligible)load(true)
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)autoRefresh()})
window.addEventListener('popstate',async()=>{
  document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close())
  applyUrlState()
  const params=new URLSearchParams(location.search),taskId=params.get('task'),projectId=params.get('project')
  await load()
  if(projectId)openProjectDetail(projectId)
  else if(taskId)openTaskDetail(taskId)
})
applyUrlState()
api('session').then(openWorkspace).catch(showLogin)
