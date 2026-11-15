// Historical diagnostic: compares the current Work page with the immutable original v33 mockup.
// v33 is a design reference, not the required target, so this runs only when invoked explicitly
// (npm run test:visual:v33) and never as part of the standard visual suite. It needs the reference HTML:
// set INTELITEX_V33_REFERENCE (it is recoverable from commit b0c40df). The reference bytes are never modified.
import { testEvidence } from './paths.mjs'
import { mkdir, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { chromium } from 'playwright'
import { DIFF_BUDGET, THEMES, WORK_VIEWPORTS, compare, failureCollector, readyWork, setTheme, startFixtureServer } from './visual-support.mjs'

const referencePath = process.env.INTELITEX_V33_REFERENCE
if (!referencePath || !existsSync(referencePath)) {
  console.error(`The v33 diagnostic needs the original mockup. Set INTELITEX_V33_REFERENCE to it (missing: ${referencePath ?? 'unset'}).`)
  process.exit(2)
}
const output = testEvidence('intelitex-visual-v33-evidence')
await mkdir(output, { recursive: true })
const { config, stop } = await startFixtureServer()
const browser = await chromium.launch()
const { failures, check } = failureCollector()
const results = []
try {
 const reference=await browser.newPage({viewport:{width:1440,height:1000}})
 const actual=await browser.newPage({viewport:{width:1440,height:1000},colorScheme:'dark'})
 const errors=[];actual.on('pageerror',e=>errors.push(e.message));actual.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
 const referenceURL=pathToFileURL(referencePath).href
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
 for (const [width, height] of WORK_VIEWPORTS) {
  for (const theme of THEMES) {
   const name = `work-${theme}-${width}`
   await check(name, async () => {
    const fixtureState = JSON.parse(fixture); fixtureState.theme = theme
    const referenceView = await browser.newPage({ viewport: { width, height } })
    try {
     // The mockup renders "Updated N min ago" from lastUpdated; pin it relative to page load so every viewport shows the same text.
     await referenceView.addInitScript(value => { const state = JSON.parse(value); for (const ws of state.workspaces) ws.lastUpdated = Date.now() - 60000; localStorage.setItem('intelitex-mockup-v22', JSON.stringify(state)) }, JSON.stringify(fixtureState))
     await referenceView.goto(referenceURL)
     await referenceView.locator('.book-card').first().waitFor()
     await actual.setViewportSize({ width, height })
     await actual.goto(config.url + '/work')
     await setTheme(actual, theme)
     await readyWork(actual)
     await referenceView.evaluate(() => document.fonts.ready)
     // Disable only visual transition timing, equally on both pages.
     for (const page of [actual, referenceView]) await page.emulateMedia({ reducedMotion: 'reduce' })
     const a = await actual.screenshot({ path: `${output}/${name}-actual.png`, animations: 'disabled' })
     const b = await referenceView.screenshot({ path: `${output}/${name}-reference.png`, animations: 'disabled' })
     const result = compare(a, b)
     if (result.diff) await writeFile(`${output}/${name}-diff.png`, result.diff)
     const topbar = await actual.locator('.topbar').evaluate(n => n.getBoundingClientRect().height)
     const horizontalScroll = await actual.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)
     const measure = page => page.locator('.book-card,.cover').evaluateAll(nodes => nodes.map(n => ({ class: n.className, rect: n.getBoundingClientRect().toJSON(), font: getComputedStyle(n).font, align: getComputedStyle(n).alignItems, padding: getComputedStyle(n).padding })))
     results.push({ name, differingPixels: result.pixels, percent: result.percent, topbar, horizontalScroll, actualGeometry: await measure(actual), referenceGeometry: await measure(referenceView) })
     const problems = []
     if (result.sizeMismatch) problems.push(`size ${result.sizeMismatch}`)
     else if (result.pixels / (width * height) > DIFF_BUDGET) problems.push(`${result.percent}% exceeds the 1% stable-fixture budget`)
     if (topbar !== 64) problems.push(`topbar height ${topbar} ≠ 64`)
     if (horizontalScroll) problems.push('horizontal scroll')
     if (problems.length) throw new Error(problems.join('; '))
    } finally { await referenceView.close() }
   })
  }
 }
 if (errors.length) failures.push(`console errors: ${errors.join(' | ')}`)
} finally { await browser.close(); stop() }
await writeFile(output + '/report.json', JSON.stringify({ reference: referencePath, unmasked: true, results, failures }, null, 2))
for (const r of results) console.log(`${r.name.padEnd(17)} ${r.percent.toFixed(4)}%`)
if (failures.length) {
  console.error(`v33 diagnostic: ${failures.length} of ${WORK_VIEWPORTS.length * THEMES.length} comparisons or checks failed:\n- ${failures.join('\n- ')}`)
  process.exit(1)
}
console.log(`v33 diagnostic: all ${results.length} comparisons within the 1% budget.`)
