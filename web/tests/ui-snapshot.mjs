// Deterministic before/after evidence for CSS refactoring: screenshots plus computed styles of representative views.
//
//   node tests/ui-snapshot.mjs record             drive a real offline pipeline once and save every API response
//   node tests/ui-snapshot.mjs capture <dir>      replay the recording against the built dist/ and capture each view
//   node tests/ui-snapshot.mjs compare <a> <b>    report pixel and computed-style differences between two captures
//
// Replays are deterministic: fixed API responses, a stubbed event stream, a frozen clock, embedded Inter, reduced
// motion and explicit readiness waits. The recording lives under the test evidence root (./tmp in this repo);
// INTELITEX_UI_RECORDING overrides its path. Run `npm run build` before `capture` so dist/ holds the code under test.
// A refactor that preserves behavior must report zero pixel and zero computed-style differences.
import { testEvidence } from './paths.mjs'
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { extname, resolve } from 'node:path'
import { gunzipSync, gzipSync } from 'node:zlib'
import { chromium } from 'playwright'
import { compare as pixelCompare, embedInter, setTheme, startFixtureServer } from './visual-support.mjs'

const BASE = 'http://localhost'
const WORKSPACE = 'prepared'
const recordingPath = process.env.INTELITEX_UI_RECORDING ?? testEvidence('intelitex-ui-recording.json')
const desktop = [1440, 1000], mobile = [390, 844]

/** Waits until no API request (other than the event stream) has been in flight for `quiet` ms. */
function apiTracker(page) {
  let inflight = 0, last = Date.now()
  const api = request => { const url = new URL(request.url()); return url.pathname.startsWith('/api/') && url.pathname !== '/api/events' }
  page.on('request', r => { if (api(r)) { inflight++; last = Date.now() } })
  const done = r => { if (api(r)) { inflight = Math.max(0, inflight - 1); last = Date.now() } }
  page.on('requestfinished', done); page.on('requestfailed', done)
  return async (quiet = 400, timeout = 20000) => {
    const end = Date.now() + timeout
    while (Date.now() < end) {
      if (inflight === 0 && Date.now() - last >= quiet) return
      await new Promise(next => setTimeout(next, 50))
    }
    throw new Error(`API requests still in flight after ${timeout} ms`)
  }
}

const visible = (page, selector) => page.locator(selector).first().waitFor()
const noText = (page, pattern) => page.waitForFunction(source => !new RegExp(source).test(document.querySelector('main')?.innerText ?? ''), pattern.source)
const workspacePath = phase => `/work/workspaces/${WORKSPACE}${phase ? '/' + phase : ''}`

// Each view: route, viewport, theme, readiness and optional interaction. Names are stable file names.
const pages = [
  ['work', '/work', page => visible(page, '.book-card')],
  ['workspace', workspacePath(), page => visible(page, '.sections-table tbody tr')],
  ['prepare', workspacePath('prepare'), page => visible(page, '.prepare-structure tbody tr')],
  ['analyse', workspacePath('analyse'), page => visible(page, '.phase-metrics')],
  ['translate', workspacePath('translate'), page => visible(page, '.translate-chunk-table tbody tr')],
  ['publish', workspacePath('publish'), page => visible(page, '.publish-section-table tbody tr')],
  ['review', workspacePath('review'), page => visible(page, '.review-term-row')],
  ['reader', `/reader/${WORKSPACE}`, page => visible(page, '.reader-page p')],
]
const route = Object.fromEntries(pages.map(([name, path, ready]) => [name, { path, ready }]))
const view = (base, theme, [width, height], extra = {}) => ({ name: `${extra.suffix ? base + '-' + extra.suffix : base}-${theme}-${width}`, ...route[base], theme, width, height, ...extra })
export const VIEWS = [
  view('work', 'dark', desktop),
  view('workspace', 'dark', desktop), view('workspace', 'light', desktop), view('workspace', 'dark', mobile),
  view('workspace', 'dark', desktop, { suffix: 'debug', debug: true }),
  view('prepare', 'dark', desktop), view('prepare', 'light', mobile),
  view('analyse', 'dark', desktop), view('analyse', 'dark', mobile),
  view('translate', 'dark', desktop), view('translate', 'light', desktop), view('translate', 'dark', mobile),
  view('publish', 'dark', desktop), view('publish', 'light', mobile),
  view('review', 'dark', desktop), view('review', 'light', desktop), view('review', 'dark', mobile),
  view('reader', 'dark', desktop), view('reader', 'light', desktop), view('reader', 'sepia', desktop),
  view('reader', 'dark', mobile), view('reader', 'sepia', mobile),
  view('reader', 'dark', desktop, { suffix: 'settings', act: async page => { await page.getByRole('button', { name: 'Reader settings' }).click(); await visible(page, 'aside.reader-settings') } }),
  view('work', 'dark', desktop, { suffix: 'settings', act: async page => { await page.getByRole('button', { name: 'Settings', exact: true }).click(); await visible(page, 'dialog[open] .settings-tabs') } }),
  view('work', 'light', mobile, { suffix: 'settings', act: async page => { await page.getByRole('button', { name: 'Settings', exact: true }).click(); await visible(page, 'dialog[open] .settings-tabs') } }),
  view('work', 'dark', desktop, { suffix: 'archive', act: async page => { await page.getByRole('button', { name: 'Archive →' }).click(); await visible(page, 'dialog.drawer[open]') } }),
  view('workspace', 'dark', desktop, { suffix: 'section', act: async page => { await page.locator('.sections-table tbody tr td').first().click(); await visible(page, 'dialog.drawer[open] .preview-text p') } }),
  view('workspace', 'light', mobile, { suffix: 'section', act: async page => { await page.locator('.sections-table tbody tr td').first().click(); await visible(page, 'dialog.drawer[open] .preview-text p') } }),
  view('workspace', 'dark', desktop, { suffix: 'archive-confirm', act: async page => { await page.locator('.archive-workspace-btn').click(); await visible(page, 'dialog.modal-card[open]') } }),
]

async function prepareView(page, v, scope) {
  const settle = apiTracker(page)
  if (v.debug) await page.addInitScript(key => { try { localStorage.setItem(key, 'true') } catch { /* optional */ } }, `intelitex.ui.v1.${scope}.debug`)
  await page.goto(page.baseURL + v.path)
  if (page.replay) await embedInter(page)
  await setTheme(page, v.theme)
  await page.locator('.connection-status.live').waitFor({ state: 'attached' })
  await v.ready(page)
  await settle()
  await noText(page, /Loading|Reading the saved|Checking saved/)
  if (v.act) { await v.act(page); await settle() }
  await page.evaluate(() => document.fonts.ready)
  await page.mouse.move(0, 0)
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))))
}

// ---- record ------------------------------------------------------------------------------------------------
async function record() {
  const { config, stop } = await startFixtureServer()
  const browser = await chromium.launch()
  const responses = {}
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
    const page = await context.newPage()
    page.setDefaultTimeout(30000)
    const until = async (fn, what, timeout = 180000) => {
      const end = Date.now() + timeout
      while (Date.now() < end) { if (await fn()) return; await new Promise(next => setTimeout(next, 250)) }
      throw new Error(`Timed out waiting for ${what}`)
    }
    // The same offline pipeline the Reader browser test drives: P1, Review approval, translation and publication.
    await page.goto(config.url + workspacePath())
    await page.getByRole('button', { name: 'Run', exact: true }).click()
    await until(() => page.getByRole('button', { name: 'Open Review', exact: true }).isVisible(), 'the Review gate')
    await page.getByRole('button', { name: 'Open Review', exact: true }).click()
    await page.getByRole('button', { name: 'Review individually & next', exact: true }).click()
    await page.getByRole('button', { name: 'Approve glossary', exact: true }).click()
    await page.getByRole('dialog', { name: 'Approve glossary?' }).getByRole('button', { name: 'Approve selected forms' }).click()
    await page.getByText('✓ Glossary approved', { exact: true }).waitFor()
    await page.getByRole('link', { name: 'Workspace', exact: true }).click()
    await page.getByRole('button', { name: 'Run', exact: true }).click()
    await until(async () => {
      const pipeline = await (await context.request.get(`${config.url}/api/workspaces/${WORKSPACE}/pipeline`)).json()
      const jobs = await (await context.request.get(`${config.url}/api/jobs`)).json()
      return pipeline.publication.current && !pipeline.active_job && !jobs.jobs.some(job => !job.finished_at)
    }, 'the offline publication')
    await context.close()
    const scope = (await (await fetch(config.url + '/api/capabilities')).json()).scope_id
    for (const v of VIEWS) {
      const viewContext = await browser.newContext({ viewport: { width: v.width, height: v.height }, colorScheme: v.theme === 'light' ? 'light' : 'dark', reducedMotion: 'reduce' })
      const viewPage = await viewContext.newPage()
      viewPage.baseURL = config.url
      viewPage.setDefaultTimeout(30000)
      viewPage.on('response', async response => {
        const url = new URL(response.url())
        if (!url.pathname.startsWith('/api/') || url.pathname === '/api/events' || response.request().method() !== 'GET') return
        try { responses[url.pathname + url.search] = { status: response.status(), type: response.headers()['content-type'] ?? 'application/json', body: (await response.body()).toString('base64') } } catch { /* navigation discarded the body */ }
      })
      await prepareView(viewPage, v, scope)
      await viewContext.close()
      console.log(`recorded ${v.name}`)
    }
    const recording = { recordedAt: Date.now(), scope, responses }
    await mkdir(resolve(recordingPath, '..'), { recursive: true })
    await writeFile(recordingPath, JSON.stringify(recording))
    console.log(`Recording: ${Object.keys(responses).length} responses → ${recordingPath}`)
  } finally { await browser.close(); stop() }
}

// ---- capture -----------------------------------------------------------------------------------------------
// Every computed longhand except custom properties: adding a token must not count as a difference, but any change
// in a resolved value must. Pseudo-elements that render content are included.
async function computedStyles(page) {
  return page.evaluate(() => {
    const names = [...getComputedStyle(document.documentElement)].filter(name => !name.startsWith('--')).sort()
    const path = element => {
      const parts = []
      for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
        let index = 1
        for (let sibling = node.previousElementSibling; sibling; sibling = sibling.previousElementSibling) if (sibling.tagName === node.tagName) index++
        parts.push(`${node.tagName.toLowerCase()}:${index}`)
      }
      return parts.reverse().join('>')
    }
    const elements = {}
    for (const element of document.querySelectorAll('*')) {
      const key = path(element)
      elements[key] = names.map(name => getComputedStyle(element).getPropertyValue(name)).join('\u001f')
      for (const pseudo of ['::before', '::after', '::backdrop']) {
        const style = getComputedStyle(element, pseudo)
        if (pseudo === '::backdrop' ? element.matches(':modal') : style.content !== 'none' && style.content !== 'normal')
          elements[key + pseudo] = names.map(name => style.getPropertyValue(name)).join('\u001f')
      }
    }
    return { names, elements }
  })
}

async function capture(outDir) {
  if (!existsSync(recordingPath)) throw new Error(`No recording at ${recordingPath}; run "node tests/ui-snapshot.mjs record" first`)
  const recording = JSON.parse(await readFile(recordingPath, 'utf8'))
  const dist = resolve(import.meta.dirname, '../dist')
  await mkdir(outDir, { recursive: true })
  const browser = await chromium.launch()
  const problems = []
  try {
    for (const v of VIEWS) {
      const context = await browser.newContext({ viewport: { width: v.width, height: v.height }, colorScheme: v.theme === 'light' ? 'light' : 'dark', reducedMotion: 'reduce' })
      const page = await context.newPage()
      page.baseURL = BASE; page.replay = true
      page.setDefaultTimeout(15000)
      const missing = [], errors = []
      page.on('pageerror', error => errors.push(error.message))
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
      // Relative times ("Updated 1 min ago") follow the recording, not the wall clock.
      await page.clock.setFixedTime(recording.recordedAt + 60000)
      await page.addInitScript(() => {
        window.EventSource = class {
          static OPEN = 1; readyState = 1; listeners = {}
          constructor() { setTimeout(() => this.listeners.snapshot?.({ data: JSON.stringify({ jobs: [], cursor: 0 }) }), 20) }
          addEventListener(type, callback) { this.listeners[type] = callback }
          close() {}
        }
      })
      await page.route(BASE + '/**', async request => {
        const url = new URL(request.request().url())
        if (url.pathname.startsWith('/api/')) {
          const hit = request.request().method() === 'GET' && recording.responses[url.pathname + url.search]
          if (!hit) { missing.push(`${request.request().method()} ${url.pathname}${url.search}`); return request.fulfill({ status: 404, contentType: 'application/json', body: '{"error":{"message":"Not recorded"}}' }) }
          return request.fulfill({ status: hit.status, contentType: hit.type, body: Buffer.from(hit.body, 'base64') })
        }
        const file = url.pathname.startsWith('/assets/') || url.pathname === '/favicon.svg' ? resolve(dist, '.' + url.pathname) : resolve(dist, 'index.html')
        if (!file.startsWith(dist + '/') || !existsSync(file)) return request.fulfill({ status: 404, body: '' })
        const types = { '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.html': 'text/html' }
        return request.fulfill({ status: 200, contentType: types[extname(file)] ?? 'application/octet-stream', body: await readFile(file) })
      })
      try {
        await prepareView(page, v, recording.scope)
        await page.screenshot({ path: `${outDir}/${v.name}.png`, fullPage: true, animations: 'disabled' })
        await writeFile(`${outDir}/${v.name}.styles.json.gz`, gzipSync(JSON.stringify(await computedStyles(page))))
        if (missing.length) problems.push(`${v.name}: unrecorded API requests: ${missing.join(', ')}`)
        if (errors.length) problems.push(`${v.name}: console errors: ${errors.join(' | ')}`)
        console.log(`captured ${v.name}`)
      } catch (error) {
        problems.push(`${v.name}: ${error.message.split('\n')[0]}`)
      } finally { await context.close() }
    }
  } finally { await browser.close() }
  await writeFile(`${outDir}/capture.json`, JSON.stringify({ views: VIEWS.map(v => v.name), problems }, null, 2))
  if (problems.length) { console.error(`Capture problems:\n- ${problems.join('\n- ')}`); process.exitCode = 1 }
}

// ---- compare -----------------------------------------------------------------------------------------------
async function compareCaptures(before, after) {
  const names = (await readdir(before)).filter(f => f.endsWith('.png')).map(f => f.slice(0, -4)).sort()
  const report = []
  for (const name of names) {
    const entry = { name }
    if (!existsSync(`${after}/${name}.png`)) { entry.missing = true; report.push(entry); continue }
    const pixels = pixelCompare(await readFile(`${after}/${name}.png`), await readFile(`${before}/${name}.png`))
    entry.pixels = pixels.sizeMismatch ? `size ${pixels.sizeMismatch}` : pixels.pixels
    if (pixels.diff && pixels.pixels) await writeFile(`${after}/${name}.diff.png`, pixels.diff)
    // Exact raw comparison as well: pixelmatch's threshold must not hide small drifts.
    const { PNG } = await import('pngjs')
    const a = PNG.sync.read(await readFile(`${before}/${name}.png`)), b = PNG.sync.read(await readFile(`${after}/${name}.png`))
    entry.rawPixels = a.width !== b.width || a.height !== b.height ? 'size' : (() => { let n = 0; for (let i = 0; i < a.data.length; i += 4) if (a.data.readUInt32BE(i) !== b.data.readUInt32BE(i)) n++; return n })()
    const one = JSON.parse(gunzipSync(await readFile(`${before}/${name}.styles.json.gz`)))
    const two = JSON.parse(gunzipSync(await readFile(`${after}/${name}.styles.json.gz`)))
    const styleDiffs = []
    for (const key of new Set([...Object.keys(one.elements), ...Object.keys(two.elements)])) {
      const x = one.elements[key]?.split('\u001f'), y = two.elements[key]?.split('\u001f')
      if (!x || !y) { styleDiffs.push(`${key}: ${x ? 'removed' : 'added'}`); continue }
      for (let i = 0; i < one.names.length; i++) {
        const j = two.names.indexOf(one.names[i])
        if (x[i] !== y[j]) styleDiffs.push(`${key} ${one.names[i]}: ${x[i]} → ${y[j]}`)
      }
    }
    entry.styleDiffs = styleDiffs.length
    if (styleDiffs.length) entry.styleSamples = styleDiffs.slice(0, 12)
    report.push(entry)
  }
  await writeFile(`${after}/compare.json`, JSON.stringify(report, null, 2))
  let changed = 0
  for (const r of report) {
    // Chromium rasterization jitters a few pixels by 1–2/255 between identical runs; pixelmatch (threshold .15)
    // ignores that, so "identical" means zero computed-style differences and zero pixelmatch pixels.
    const clean = !r.missing && r.pixels === 0 && r.styleDiffs === 0
    if (!clean) changed++
    console.log(`${clean ? r.rawPixels ? 'same*  ' : 'same   ' : 'CHANGED'} ${r.name.padEnd(36)} ${r.missing ? 'missing' : `raw px ${r.rawPixels}, pixelmatch ${r.pixels}, styles ${r.styleDiffs}`}`)
    for (const sample of r.styleSamples ?? []) console.log(`          ${sample}`)
  }
  console.log(changed ? `${changed} of ${report.length} views differ.` : `All ${report.length} views identical (computed styles and pixelmatch; same* = raw rasterization noise only).`)
  if (changed) process.exitCode = 1
}

const [command, ...args] = process.argv.slice(2)
if (command === 'record') await record()
else if (command === 'capture' && args[0]) await capture(resolve(args[0]))
else if (command === 'compare' && args.length === 2) await compareCaptures(resolve(args[0]), resolve(args[1]))
else { console.error('Usage: ui-snapshot.mjs record | capture <dir> | compare <before> <after>'); process.exitCode = 2 }
