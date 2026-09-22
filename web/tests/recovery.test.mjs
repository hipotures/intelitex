import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import test from 'node:test'
import { chromium } from 'playwright'

// Test-only HTTP fixtures for specified large-data and stream-burst workloads.
test('1000 sections, 2000 terms, stream bursts, offline/refocus and one shared EventSource', {timeout:90000}, async t=>{
 const server=spawn('uv',['run','--group','dev','python','tests/web_fixture_server.py'],{cwd:'..',stdio:['pipe','pipe','pipe']})
 let stderr='';server.stderr.on('data',s=>stderr+=s)
 t.after(()=>server.stdin.end('quit\n'))
 const lines=createInterface({input:server.stdout})
 const {url}=await new Promise((resolve,reject)=>{lines.on('line',line=>{try{const data=JSON.parse(line);if(data.url)resolve(data)}catch{}});server.once('exit',()=>reject(Error(stderr)))})
 const browser=await chromium.launch();t.after(()=>browser.close())
 const context=await browser.newContext({viewport:{width:1440,height:1000}})
 await context.addInitScript(()=>{const Native=window.EventSource;window.__eventSources=[];window.EventSource=class extends Native{constructor(...args){super(...args);window.__eventSources.push(this)}close(){super.close();window.__eventSources=window.__eventSources.filter(s=>s!==this)}}})
 const page=await context.newPage();page.setDefaultTimeout(20000)
 const errors=[];page.on('pageerror',e=>errors.push(e.message))
 page.on('console',m=>{if(m.type()==='error' && !/ERR_INTERNET_DISCONNECTED/.test(m.text()))errors.push(m.text())})
 await page.goto(url+'/work/workspaces/prepared')
 await page.getByRole('button',{name:'Run',exact:true}).click()
 await page.getByRole('button',{name:'Open Review',exact:true}).waitFor()
 const original=await (await context.request.get(url+'/api/workspaces/prepared/pipeline')).json()
 const review=await (await context.request.get(url+'/api/workspaces/prepared/review')).json()
 const terms=Array.from({length:2000},(_,i)=>({...review.terms[0],id:`T${String(i+1).padStart(6,'0')}`,source:`Term ${String(i+1).padStart(4,'0')}`,custom:'',reviewed:false}))
 await page.route('**/api/workspaces/prepared/review/terms/*/evidence',route=>route.fulfill({json:{term_id:new URL(route.request().url()).pathname.split('/').at(-2),entries:[],warnings:[],choice_pending_approval:false}}))
 await page.route('**/api/workspaces/prepared/review',route=>route.fulfill({json:{...review,terms}}))
 await page.route('**/api/workspaces/prepared/pipeline',route=>route.fulfill({json:{...original,active_job:null,busy:false,sections:Array.from({length:1000},(_,i)=>({...original.sections[0],id:`section${i}`,ordinal:i+1,title:`Section ${i+1}`}))}}))
 await page.reload();await page.locator('.sections-table tbody tr').last().waitFor()
 assert.equal(await page.locator('.sections-table tbody tr').count(),1000)
 const before=performance.now()
 await page.getByRole('button',{name:'Open Review',exact:true}).click()
 await page.locator('.review-term-row').last().waitFor()
 assert.equal(await page.locator('.review-term-row').count(),2000)
 assert.equal(await page.evaluate(()=>window.__eventSources.length),1)
 const snapshot=await (await context.request.get(url+'/api/jobs')).json();const job=snapshot.jobs.at(-1)
 await page.evaluate(({job,cursor})=>{
  let n=0
  window.__burst=setInterval(()=>{
   n++;const payload={id:cursor+n,job_id:job.job_id,workspace_id:'prepared',sequence:job.sequence+n,timestamp:new Date().toISOString(),event:{kind:n===200?'job_state':'provider_waiting',values:n===200?{state:'succeeded'}:{pass_no:1}}}
   window.__eventSources[0]?.dispatchEvent(new MessageEvent('progress',{data:JSON.stringify(payload)}))
   if(n===200)clearInterval(window.__burst)
  },10)
 },{job,cursor:snapshot.cursor})
 const search=page.getByRole('textbox',{name:'Search terminology'})
 await search.fill('Term 1999')
 await page.locator('.review-term-row').filter({hasText:'Term 1999'}).waitFor()
 await page.waitForFunction(()=>document.querySelectorAll('.review-term-row').length===1)
 assert.equal(await search.inputValue(),'Term 1999')
 assert.equal(await search.evaluate(n=>document.activeElement===n),true)
 await page.screenshot({path:'/tmp/intelitex-browser-evidence/large-review.png',fullPage:true})
 const elapsed=performance.now()-before
 assert.ok(elapsed<10000,`large-data navigation and filtering took ${elapsed}ms`)
 await context.setOffline(true)
 await page.getByRole('status').filter({hasText:'Offline'}).waitFor()
 assert.equal(await page.getByRole('button',{name:'Review & next',exact:true}).isDisabled(),true)
 assert.equal(await search.inputValue(),'Term 1999')
 await context.setOffline(false)
 await page.evaluate(()=>window.dispatchEvent(new Event('focus')))
 await page.getByRole('status').filter({hasText:'Live'}).waitFor()
 assert.equal(await page.evaluate(()=>window.__eventSources.length),1)
 assert.equal(await search.inputValue(),'Term 1999')
 assert.deepEqual(errors,[])
 console.log(`Large-data navigation/filter: ${Math.round(elapsed)} ms; 100 events/s; no page errors; one EventSource`)
})
