import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { mkdir,readFile,writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { chromium } from 'playwright'
import pixelmatch from 'pixelmatch'
import { PNG } from 'pngjs'
const output='/tmp/intelitex-visual-evidence'
await mkdir(output,{recursive:true})
const server=spawn('uv',['run','--group','dev','python','tests/web_fixture_server.py'],{cwd:'..',stdio:['pipe','pipe','inherit']})
const lines=createInterface({input:server.stdout})
const config=await new Promise((resolve,reject)=>{lines.on('line',line=>{try{const data=JSON.parse(line);if(data.url)resolve(data)}catch{}});server.once('exit',reject)})
const browser=await chromium.launch()
try {
 const reference=await browser.newPage({viewport:{width:1440,height:1000}})
 const actual=await browser.newPage({viewport:{width:1440,height:1000},colorScheme:'dark'})
 const errors=[];actual.on('pageerror',e=>errors.push(e.message));actual.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
 const referenceURL=pathToFileURL(resolve('../.agents/skills/intelitex-web/assets/intelitex_workspace_mockup_v33.html')).href
 await reference.goto(referenceURL)
 await reference.screenshot({path:output+'/original-work-dark-1440.png',fullPage:true,animations:'disabled'})
 await reference.locator('.workspace-row').first().click()
 await reference.screenshot({path:output+'/original-workspace-dark-1440.png',fullPage:true,animations:'disabled'})
 await reference.locator('.section-name').first().click()
 await reference.locator('#previewDrawer.open').waitFor()
 await reference.screenshot({path:output+'/original-drawer-dark-1440.png',fullPage:true,animations:'disabled'})
 await reference.keyboard.press('Escape')
 await reference.evaluate(()=>{const key='intelitex-mockup-v22';const s=JSON.parse(localStorage.getItem(key));s.route={kind:'review',id:'silent-ws'};localStorage.setItem(key,JSON.stringify(s))})
 await reference.reload();await reference.screenshot({path:output+'/original-review-dark-1440.png',fullPage:true,animations:'disabled'})
 await reference.setViewportSize({width:390,height:844})
 await reference.screenshot({path:output+'/original-review-dark-390.png',fullPage:true,animations:'disabled'})
 await reference.setViewportSize({width:1440,height:1000})
 for (const [phase,id] of [['prepare','silent-ws'],['p1','silent-ws'],['translation','glass-ws'],['publish','archive-ws']]) {
  await reference.evaluate(({phase,id})=>{const key='intelitex-mockup-v22';const s=JSON.parse(localStorage.getItem(key));s.route={kind:'phase',id,phase};localStorage.setItem(key,JSON.stringify(s))},{phase,id})
  await reference.reload();await reference.locator('.phase-detail-page').waitFor()
  await reference.screenshot({path:`${output}/original-phase-${phase}-dark-1440.png`,fullPage:true,animations:'disabled'})
 }
 await reference.getByRole('button',{name:'Reader',exact:true}).click()
 await reference.screenshot({path:output+'/original-reader-dark-1440.png',fullPage:true,animations:'disabled'})
 await reference.setViewportSize({width:390,height:844})
 await reference.screenshot({path:output+'/original-reader-dark-390.png',fullPage:true,animations:'disabled'})
 await reference.getByRole('button',{name:'Work',exact:true}).click()
 await reference.evaluate(()=>{const key='intelitex-mockup-v22';const s=JSON.parse(localStorage.getItem(key));s.route={kind:'home'};localStorage.setItem(key,JSON.stringify(s))})
 await reference.reload()
 await reference.getByRole('button',{name:'Archive →'}).click()
 await reference.screenshot({path:output+'/original-archive-dark-390.png',fullPage:true,animations:'disabled'})
 await reference.keyboard.press('Escape')
 await reference.getByRole('button',{name:'Settings',exact:true}).click()
 await reference.screenshot({path:output+'/original-settings-dark-390.png',fullPage:true,animations:'disabled'})
 await reference.keyboard.press('Escape')
 await reference.setViewportSize({width:1440,height:1000})
 // Test-only fixture adapts data, never the reference bytes, CSS, or production state.
 const fixture=await reference.evaluate(()=>{const key='intelitex-mockup-v22';const s=JSON.parse(localStorage.getItem(key));s.route={kind:'home'};s.settings.sourcePath='Configured';s.library=[{id:'source',title:'Relay Book',author:'Test Author',words:'—',lang:'en',cover:1,workspaceId:'prepared'},{id:'second',title:'Relay Book',author:'Test Author',words:'—',lang:'en',cover:2,workspaceId:null}];s.workspaces=[{...s.workspaces[2],id:'prepared',bookId:'source',analysisDone:false,prepared:true,error:null,lastUpdated:Date.now(),sections:[{id:'one',name:'Chapter 1',mode:'Full',passes:{1:'pending',2:'pending',3:'pending',4:'pending',5:'pending'}},{id:'two',name:'Chapter 2',mode:'Full',passes:{1:'pending',2:'pending',3:'pending',4:'pending',5:'pending'}}]}];return JSON.stringify(s)})
 const results=[]
 for(const [width,height] of [[1440,1000],[1920,1080],[1024,1000],[820,1000],[720,1000],[390,844]]) {
  for(const theme of ['dark','light']) {
   const fixtureState=JSON.parse(fixture);fixtureState.theme=theme
   const referenceView=await browser.newPage({viewport:{width,height}})
   await referenceView.addInitScript(value=>localStorage.setItem('intelitex-mockup-v22',value),JSON.stringify(fixtureState))
   await referenceView.goto(referenceURL)
   await referenceView.locator('.book-card').first().waitFor()
   await actual.setViewportSize({width,height})
   await actual.goto(config.url+'/work');await actual.locator('.book-card').first().waitFor()
   if(await actual.locator('html').getAttribute('data-theme')!==theme)await actual.getByRole('button',{name:'Toggle theme'}).click()
   await actual.locator('.workspace-state').waitFor({state:'attached'})
   await actual.evaluate(()=>document.fonts.ready);await referenceView.evaluate(()=>document.fonts.ready)
   // Disable only visual transition timing, equally on both pages.
   for(const page of [actual,referenceView])await page.emulateMedia({reducedMotion:'reduce'})
   const name=`work-${theme}-${width}`
   const a=await actual.screenshot({path:`${output}/${name}-actual.png`,animations:'disabled'})
   const b=await referenceView.screenshot({path:`${output}/${name}-reference.png`,animations:'disabled'})
   const one=PNG.sync.read(a),two=PNG.sync.read(b),diff=new PNG({width,height})
   const pixels=pixelmatch(one.data,two.data,diff.data,width,height,{threshold:.15,includeAA:false})
   await writeFile(`${output}/${name}-diff.png`,PNG.sync.write(diff))
   assert.ok(pixels/(width*height) <= .01, `${name}: ${100*pixels/(width*height)}% exceeds the 1% stable-fixture budget`)
   const geometry=await actual.locator('.topbar').evaluate(n=>n.getBoundingClientRect().height)
   assert.equal(geometry,64)
   assert.equal(await actual.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
   const measure=page=>page.locator('.book-card,.cover').evaluateAll(nodes=>nodes.map(n=>({class:n.className,rect:n.getBoundingClientRect().toJSON(),font:getComputedStyle(n).font,align:getComputedStyle(n).alignItems,padding:getComputedStyle(n).padding})))
   results.push({name,differingPixels:pixels,percent:100*pixels/(width*height),actualGeometry:await measure(actual),referenceGeometry:await measure(referenceView)})
   await referenceView.close()
  }
 }
 assert.deepEqual(errors,[])
 await writeFile(output+'/report.json',JSON.stringify({browser:browser.version(),unmasked:true,results,consoleErrors:errors},null,2))
 console.log(JSON.stringify(results,null,2))
} finally { await browser.close();server.stdin.end('quit\n') }
