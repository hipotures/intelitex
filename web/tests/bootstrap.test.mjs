import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { mkdir } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'

const evidence = '/tmp/intelitex-browser-evidence'
const wait = async (fn, description) => {
 const end=Date.now()+30000
 while(Date.now()<end) { if(await fn()) return; await new Promise(r=>setTimeout(r,100)) }
 throw new Error(`Timed out: ${description}`)
}
test('production same-origin offline workflow, Review, Reader, archive, themes and responsive smoke', {timeout:180000}, async t => {
 await mkdir(evidence,{recursive:true})
 const server=spawn('uv',['run','--group','dev','python','tests/web_fixture_server.py'],{cwd:'..',stdio:['pipe','pipe','pipe']})
 let serverError='';server.stderr.on('data',s=>serverError+=s)
 t.after(async()=>{server.stdin.end('quit\n'); await new Promise(resolve=>{server.once('exit',resolve);setTimeout(()=>{server.kill('SIGTERM');resolve()},3000).unref()})})
 const lines=createInterface({input:server.stdout})
 const config=await new Promise((resolve,reject)=>{lines.on('line',line=>{try{const v=JSON.parse(line);if(v.url)resolve(v)}catch{}});server.once('exit',()=>reject(new Error(serverError)))})
 const browser=await chromium.launch();t.after(()=>browser.close())
 const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'dark'})
 const page=await context.newPage();page.setDefaultTimeout(15000);const errors=[],failed=[]
 page.on('console',m=>{if(m.type()==='error'){errors.push(m.text()); console.log('Console:',m.text())}});page.on('pageerror',e=>errors.push(e.message))
 page.on('response',r=>{if(r.status()>=400){failed.push(`${r.status()} ${r.url()}`);void r.text().then(body=>console.log('HTTP failure',r.url(),body))}})
 await page.goto(config.url+'/work'); await page.getByRole('status').filter({hasText:'Live'}).waitFor({state:'attached'})
  await page.getByRole('heading',{name:'Books in progress'}).waitFor()
 await page.screenshot({path:evidence+'/work-dark-1440.png',fullPage:true,animations:'disabled'})
 await page.locator('.workspace-title').first().click()
 await page.getByRole('button',{name:'Run',exact:true}).click()
 await wait(async()=>await page.getByRole('button',{name:'Open Review',exact:true}).isVisible(),'analysis stops at Review')
 await page.screenshot({path:evidence+'/workspace-review-dark-1440.png',fullPage:true,animations:'disabled'})
 await page.getByRole('button',{name:'Open Review',exact:true}).click()
 await page.getByRole('heading',{name:'Terminology Review'}).waitFor()
 await page.getByRole('button',{name:'Review individually & next',exact:true}).waitFor()
 await page.screenshot({path:evidence+'/review-dark-1440.png',fullPage:true,animations:'disabled'})
 await page.getByRole('button',{name:'Review individually & next',exact:true}).click()
 await wait(async()=>await page.getByRole('button',{name:'Approve glossary',exact:true}).isEnabled(),'review acknowledged')
 await page.getByRole('button',{name:'Approve glossary',exact:true}).click()
 await page.getByRole('dialog',{name:'Approve glossary?'}).getByText('1/1 individually reviewed', {exact:false}).waitFor()
 await page.getByRole('dialog',{name:'Approve glossary?'}).getByRole('button',{name:'Approve selected forms'}).click()
 await page.getByText('✓ Glossary approved',{exact:true}).waitFor()
 await page.getByRole('link',{name:'Workspace',exact:true}).click()
 await page.getByRole('button',{name:'Run',exact:true}).click()
 // Route changes and a reload must not cancel workers.
 await page.getByRole('button',{name:'Reader',exact:true}).click(); await page.reload()
 await wait(async()=>{const response=await context.request.get(config.url+'/api/workspaces/prepared/pipeline');return (await response.json()).publication.current},'automatic publication')
 await page.getByRole('button',{name:'Refresh availability',exact:true}).click()
 await page.getByText('Translated: Relay-A moved steadily in section 1.',{exact:true}).waitFor()
 await page.screenshot({path:evidence+'/reader-dark-1440.png',fullPage:true,animations:'disabled'})
 await page.locator('.reader-page p').filter({hasText:'Translated: Relay-A moved'}).evaluate(node=>{const text=node.firstChild;const range=document.createRange();range.setStart(text,12);range.setEnd(text,19);const s=window.getSelection();s.removeAllRanges();s.addRange(range);node.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}))})
 await page.getByRole('button',{name:'Mark selection',exact:true}).click()
 await page.getByText('Markers · 1',{exact:true}).waitFor()
 await page.getByRole('button',{name:'Work',exact:true}).click()
 await wait(async()=>await page.getByRole('button',{name:'Finished',exact:true}).isVisible(),'completed workflow projection')
 // An external editor must not overwrite focused local text, and approval freshness is real.
 await page.goto(config.url+'/work/workspaces/prepared/review')
 await page.getByText('✓ Glossary approved',{exact:true}).waitFor()
 const review=await (await context.request.get(config.url+'/api/workspaces/prepared/review')).json()
 const custom=page.getByRole('textbox',{name:'Custom Polish form'})
 await custom.fill('Relay-C')
 const remote=await context.request.patch(config.url+'/api/workspaces/prepared/review/terms/'+review.terms[0].id,{data:{revision:review._revision,user_notes:'Concurrent offline editor'}})
 assert.equal(remote.status(),200)
 const conflict=await context.request.patch(config.url+'/api/workspaces/prepared/review/terms/'+review.terms[0].id,{data:{revision:review._revision,custom:'Should not overwrite'}})
 assert.equal(conflict.status(),409)
 await page.getByRole('button',{name:'Reapply my form',exact:true}).waitFor()
 assert.equal(await custom.inputValue(),'Relay-C')
 assert.equal(await custom.evaluate(n=>document.activeElement===n),true)
 await page.getByRole('button',{name:'Reapply my form',exact:true}).click()
 await wait(async()=>{const r=await (await context.request.get(config.url+'/api/workspaces/prepared/review')).json();return r.terms[0].custom==='Relay-C'},'explicit custom reapply')
 await wait(async()=>{const p=await (await context.request.get(config.url+'/api/workspaces/prepared/pipeline')).json();return !p.approved && p.actions.translate.reason==='approval_required'},'backend freshness gate')
 const retained=await (await context.request.get(config.url+'/api/workspaces/prepared/pipeline')).json()
 assert.equal(retained.translation_complete,true)
 await page.getByRole('button',{name:'Review individually & next',exact:true}).click()
 await page.getByRole('button',{name:'Approve glossary',exact:true}).click()
 await page.getByRole('dialog',{name:'Approve glossary?'}).getByRole('button',{name:'Approve selected forms'}).click()
 try { await page.getByText('✓ Glossary approved',{exact:true}).waitFor() } catch(error) { console.log('Confirmation diagnostics',await page.locator('body').innerText());throw error }
 await page.getByRole('link',{name:'Workspace',exact:true}).click()
 await page.getByRole('button',{name:'Run',exact:true}).click()
 await wait(async()=>await page.getByRole('button',{name:'Finished',exact:true}).isVisible(),'retranslation of application-stale chunks')
 await page.locator('.sections-table .section-open').first().click()
 await page.getByRole('dialog').waitFor();await page.screenshot({path:evidence+'/drawer-dark-1440.png',fullPage:true,animations:'disabled'})
 await page.getByRole('button',{name:'Close',exact:true}).click()
 await page.getByRole('button',{name:'Publish',exact:false}).filter({has:page.locator('.phase-name')}).click()
 await page.getByRole('link',{name:'Open EPUB'}).waitFor();await page.screenshot({path:evidence+'/publish-dark-1440.png',fullPage:true,animations:'disabled'})
 for (const phase of ['prepare','analyse','translate','publish']) {
  await page.goto(config.url+'/work/workspaces/prepared/'+phase)
  await page.locator('.phase-detail-grid').waitFor()
  if (phase === 'translate') await page.locator('.activity-line').first().waitFor()
  await page.screenshot({path:`${evidence}/phase-${phase}-dark-1440.png`,fullPage:true,animations:'disabled'})
 }
 await page.getByRole('link',{name:'Workspace',exact:true}).click()
 await page.getByRole('button',{name:/Archive workspace$/}).click()
 await page.getByRole('dialog').getByRole('button',{name:/Archive workspace$/}).click()
 await page.getByRole('heading',{name:'Books in progress'}).waitFor()
 await page.getByRole('button',{name:'Archive →',exact:true}).click()
 await page.getByRole('button',{name:'Restore',exact:true}).click()
 await page.locator('.workspace-title').waitFor()
 // A second source opens read-only details; Save creates a persisted draft before Prepare.
 await page.locator('[data-source-id="second-book"]').click()
 await page.getByRole('button',{name:'Add to workspace'}).click()
 await page.getByRole('button',{name:'Save',exact:true}).click()
 await page.getByRole('heading',{name:'Prepare this workspace'}).waitFor()
 const draftPath=new URL(page.url()).pathname
 const draftId=draftPath.split('/').at(-1)
 await page.locator('.archive-workspace-btn').click()
 await page.getByRole('dialog').getByRole('button',{name:'Archive workspace',exact:true}).click()
 await page.getByRole('button',{name:'Archive →'}).click()
 await page.getByRole('dialog').getByRole('button',{name:'Restore'}).click()
 await page.locator(`[data-ui-debug-id="WRC"][data-entity-id="${draftId}"] .workspace-title`).click()
 assert.equal(new URL(page.url()).pathname,draftPath)
 await page.getByRole('button',{name:'Prepare',exact:true}).first().click()
 await wait(async()=>await page.locator('.sections-table').isVisible(),'real Prepare import')
 assert.equal(new URL(page.url()).pathname,draftPath)
 for(const width of [1920,1024,820,720,390]) {
  await page.setViewportSize({width,height:width===390?844:1080})
  for(const theme of ['dark','light']) {
   if(await page.locator('html').getAttribute('data-theme')!==theme)await page.getByRole('button',{name:'Toggle theme'}).click()
   await page.screenshot({path:`${evidence}/workspace-${theme}-${width}.png`,fullPage:true,animations:'disabled'})
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`horizontal overflow ${width} ${theme}`)
  }
 }
 await page.getByRole('button',{name:'Settings',exact:true}).click()
 await page.getByRole('tab',{name:'Models',exact:true}).click()
 await page.getByText('Configured · not tested',{exact:true}).first().waitFor()
 await page.screenshot({path:evidence+'/settings-light-390.png',fullPage:true,animations:'disabled'})
 await page.keyboard.press('Escape')
 await page.getByRole('button',{name:'Toggle theme'}).click()
 for(const [route,name,ready] of [
  ['/work/workspaces/prepared/review','review','.review-layout'],
  ['/reader/prepared','reader','.reader-page'],
 ]) {
  await page.goto(config.url+route);await page.locator(ready).waitFor()
  await page.screenshot({path:`${evidence}/${name}-dark-390.png`,fullPage:true,animations:'disabled'})
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`${name} mobile overflow`)
 }
 await page.goto(config.url+'/work')
 await page.getByRole('button',{name:'Archive →'}).click()
 await page.screenshot({path:evidence+'/archive-dark-390.png',fullPage:true,animations:'disabled'})
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'archive mobile overflow')
 await page.keyboard.press('Escape')
 await page.getByRole('button',{name:'Settings',exact:true}).click()
 await page.screenshot({path:evidence+'/settings-dark-390.png',fullPage:true,animations:'disabled'})
 assert.deepEqual(failed,[]);assert.deepEqual(errors,[])
 console.log(`Browser evidence: ${evidence}; console errors: ${errors.length}; failed HTTP responses: ${failed.length}`)
})
