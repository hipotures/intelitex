// Regenerates the README screenshots into docs/images/<name>.png.
//
//   npm --prefix web run screenshots                  # demo data from the disposable offline fixture
//   SCREENSHOT_URL=http://HOST:PORT/work/workspaces/ID npm --prefix web run screenshots   # a real server
//   ... npm --prefix web run screenshots -- review    # only shots whose name contains "review"
//
// Live mode (SCREENSHOT_URL) opens that workspace on a running server. It only issues GET requests: every
// other method is aborted, so nothing can be saved, started or changed. Collapsed panels are expanded
// through browser preferences of a throwaway browser profile, and anything that does not belong in a
// screenshot (other workspaces, books outside Project Gutenberg) is hidden or filtered before capture.
// A shot's `theme` (dark, light, sepia) switches the page theme before capture.
// Edit FIXTURE_SHOTS / LIVE_SHOTS to add or change a screenshot. Set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
// to use a system Chromium.
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { mkdir, readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { chromium } from 'playwright'

// The app asks for the system font `Inter` and does not bundle it, so a machine without Inter would
// render a fallback face. Embed the same family from devDependencies so shots look identical anywhere.
const INTER = ['latin', 'latin-ext']
const fontCss = (await Promise.all(INTER.map(async subset => {
  const file = resolve(import.meta.dirname, `../node_modules/@fontsource-variable/inter/files/inter-${subset}-wght-normal.woff2`)
  return `@font-face{font-family:Inter;font-weight:100 900;font-style:normal;src:url(data:font/woff2;base64,${(await readFile(file)).toString('base64')}) format('woff2')}`
}))).join('\n')

const OUTPUT = resolve(import.meta.dirname, '../../docs/images')
// Every shot is 1440 CSS px wide at 2x pixel density (2880 px files): uniform in a gallery and sharp on
// HiDPI screens or when the browser scales the image down. Shots use a 1640 px tall window, which holds the
// tallest page (the workspace overview, 1635 px) without cutting anything.
const VIEWPORT = { width: 1440, height: 1640 }
const SCALE = 2
const THEME = 'dark'

// ---- Fixture mode -------------------------------------------------------------------------------------
// Pipeline stages the fixture workspace passes through, in order. `ready` is heading:<name>,
// text:<string> or css:<selector>.
const STAGES = ['prepared', 'review', 'translated']
const FIXTURE_SHOTS = [
  { name: 'work-home', stage: 'prepared', path: '/work', ready: 'heading:Books in progress' },
  { name: 'workspace-overview', stage: 'prepared', path: '/work/workspaces/prepared', ready: 'css:[data-ui-debug-id="SCT"]' },
  { name: 'review-terminology', stage: 'review', path: '/work/workspaces/prepared/review', ready: 'css:.review-term-row' },
  { name: 'reader', stage: 'translated', path: '/reader/prepared', ready: 'text:Translated: Relay-A moved steadily in section 1.' },
]

// ---- Live mode ----------------------------------------------------------------------------------------
// `W` is the workspace id. `prepare` runs after the page is ready and may only change what the browser shows.
const gutenbergOnly = async page => {
  await page.getByRole('heading', { name: 'Library', exact: true }).scrollIntoViewIfNeeded()
  await page.locator('.book-card').first().waitFor()
  const label = await page.locator('.library-view-label').textContent()
  const checked = await page.getByLabel('Show only Project Gutenberg books').isChecked()
  if (label !== 'Project Gutenberg' || !checked) throw new Error('Refusing to capture the Library: the Project Gutenberg filter is not active.')
}
// Open the first real chapter, the one after Contents (the Reader starts at the table of contents).
// Chapter names follow the translation, so pick by position, not by name.
const firstChapter = async page => {
  await page.locator('.reader-chapter-list button').nth(1).click()
  await page.locator('.reader-chapter-list button.active').nth(0).waitFor()
  await page.waitForFunction(() => document.querySelector('.reader-chapter-list button:nth-of-type(2)')?.classList.contains('active'))
}
const LIVE_SHOTS = W => [
  // The main page is taller than the window: one shot from the very top, one scrolled to the very bottom.
  { name: 'work-top', path: '/work', ready: 'heading:Library', prepare: async page => { await gutenbergOnly(page); await page.evaluate(() => window.scrollTo(0, 0)) } },
  { name: 'work-bottom', path: '/work', ready: 'heading:Library', prepare: async page => {
    await gutenbergOnly(page)
    // The Library loads further pages as the reader scrolls; keep going to the bottom until the list stops growing.
    let count = -1
    for (let step = 0; step < 12; step++) {
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
      await page.waitForTimeout(1200)
      const now = await page.locator('.book-card').count()
      if (now === count) break
      count = now
    }
  } },
  // Everything is dark except the Reader, which is shown in all three themes.
  { name: 'workspace-overview', path: `/work/workspaces/${W}`, ready: 'css:[data-ui-debug-id="SCT"]' },
  { name: 'workspace-section-preview', path: `/work/workspaces/${W}`, ready: 'css:[data-ui-debug-id="SCT"]',
    prepare: async page => {
      const row = page.locator('[data-ui-debug-id="SCT"] tr').filter({ has: page.getByText('F', { exact: true }) }).first()
      await row.locator('.section-name').click()
      await page.locator('[data-ui-debug-id="PVD"]').waitFor()
    } },
  { name: 'phase-prepare', path: `/work/workspaces/${W}/prepare`, ready: 'css:[data-ui-debug-id="PTT"]' },
  { name: 'phase-analysis', path: `/work/workspaces/${W}/analyse`, ready: 'css:.phase-detail-page' },
  // Translate and Publish select the first Full (F) entry: Contents is translate-only (T) and not worth showing.
  { name: 'phase-translate', path: `/work/workspaces/${W}/translate`, ready: 'css:.phase-detail-page',
    prepare: async page => {
      const row = page.locator('tbody tr').filter({ has: page.locator('.translate-mode-badge.full') }).first()
      await row.locator('.translate-chunk-choice').click()
      await row.and(page.locator('.selected')).waitFor()
    } },
  { name: 'phase-publish', path: `/work/workspaces/${W}/publish`, ready: 'css:.phase-detail-page',
    prepare: async page => {
      const row = page.locator('tbody tr').filter({ has: page.locator('.processing-readonly.full') }).first()
      await row.locator('.publish-section-choice').click()
      await row.locator('.publish-section-choice[aria-pressed="true"]').waitFor()
    } },
  { name: 'review-terminology', path: `/work/workspaces/${W}/review`, ready: 'css:.review-term-row' },
  ...['dark', 'light', 'sepia'].map(theme => ({ name: `reader-${theme}`, path: `/reader/${W}`, ready: 'css:.reader-page p', prepare: firstChapter, theme })),
  { name: 'reader-settings', path: `/reader/${W}`, ready: 'css:.reader-page p',
    prepare: async page => { await firstChapter(page); await page.getByRole('button', { name: 'Reader settings' }).click(); await page.locator('.reader-settings').waitFor() } },
  // Settings dialog on the Models tab, over the workspace page (not /work, which lists every workspace).
  { name: 'settings-models', path: `/work/workspaces/${W}`, ready: 'css:[data-ui-debug-id="SCT"]',
    prepare: async page => {
      await page.getByRole('button', { name: 'Settings', exact: true }).click()
      await page.getByRole('tab', { name: 'Models' }).click()
      await page.locator('[data-ui-debug-id="SPM"]').waitFor()
    } },
]

const live = (() => {
  if (!process.env.SCREENSHOT_URL) return null
  const url = new URL(process.env.SCREENSHOT_URL)
  const workspace = url.pathname.match(/^\/work\/workspaces\/([A-Za-z0-9_-][A-Za-z0-9_.-]*)/)?.[1]
  if (!workspace) throw new Error('SCREENSHOT_URL must look like http://HOST:PORT/work/workspaces/<workspace id>')
  return { origin: url.origin, workspace }
})()
const SHOTS = live ? LIVE_SHOTS(live.workspace) : FIXTURE_SHOTS

const filter = process.argv.slice(2)
const shots = SHOTS.filter(shot => !filter.length || filter.some(part => shot.name.includes(part)))
if (!shots.length) throw new Error(`No shot matches ${filter.join(', ')}; known: ${SHOTS.map(s => s.name).join(', ')}`)
const lastStage = live ? 0 : Math.max(...shots.map(shot => STAGES.indexOf(shot.stage)))

let server, base
if (live) base = live.origin
else {
  server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'],
    { cwd: resolve(import.meta.dirname, '../..'), stdio: ['pipe', 'pipe', 'inherit'] })
  const config = await new Promise((done, fail) => {
    createInterface({ input: server.stdout }).on('line', line => { try { const value = JSON.parse(line); if (value.url) done(value) } catch { /* wait for readiness */ } })
    server.once('exit', () => fail(new Error('Fixture server exited before it was ready')))
  })
  base = config.url
}
const browser = await chromium.launch(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
  ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {})

const wait = async (fn, description) => {
  const end = Date.now() + 30000
  while (Date.now() < end) {
    if (await fn()) return
    await new Promise(done => setTimeout(done, 100))
  }
  throw new Error(`Timed out: ${description}`)
}
const waitFor = (page, ready) => {
  if (!ready) return page.locator('.topbar').waitFor()
  const [kind, text] = [ready.slice(0, ready.indexOf(':')), ready.slice(ready.indexOf(':') + 1)]
  if (kind === 'heading') return page.getByRole('heading', { name: text }).waitFor()
  return kind === 'css' ? page.locator(text).first().waitFor() : page.getByText(text).first().waitFor()
}

// Pages show placeholders ("Loading…", "Reading the saved workspace…") until their data arrives, and a CSS
// selector alone matches that skeleton. Wait until no visible placeholder is left.
const settled = page => page.waitForFunction(() => !/Loading|Reading the saved workspace/.test(document.body.innerText), null, { timeout: 30000 })

try {
  await mkdir(OUTPUT, { recursive: true })
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: SCALE, colorScheme: THEME, bypassCSP: true, reducedMotion: 'reduce' })
  if (live) {
    // Browser preferences of this throwaway profile: expanded panels and the Project Gutenberg Library filter.
    const scope = (await (await context.request.get(`${base}/api/capabilities`)).json()).scope_id
    const preferences = { theme: THEME, 'library.gutenberg.show': 'true', 'library.gutenberg': 'true',
      [`${live.workspace}.models`]: 'true', [`${live.workspace}.activity`]: 'true', [`${live.workspace}.recent-execution`]: 'true' }
    await context.addInitScript(({ scope, preferences }) => {
      for (const [key, value] of Object.entries(preferences)) localStorage.setItem(`intelitex.ui.v1.${scope}.${key}`, value)
    }, { scope, preferences })
    // Read-only guarantee: nothing but GET may reach a real server.
    await context.route('**/*', route => {
      const method = route.request().method()
      if (method === 'GET' || method === 'HEAD') return route.continue()
      console.warn(`Blocked ${method} ${route.request().url()}`)
      return route.abort()
    })
  }
  const page = await context.newPage()
  page.setDefaultTimeout(15000)
  const errors = []
  page.on('pageerror', error => errors.push(error.message))

  // Drive the fixture to the next stage with the same UI actions the browser tests use.
  const advance = {
    review: async () => {
      await page.goto(`${base}/work/workspaces/prepared`)
      await page.getByRole('button', { name: 'Run', exact: true }).click()
      await page.getByRole('button', { name: 'Open Review', exact: true }).waitFor()
    },
    translated: async () => {
      await page.goto(`${base}/work/workspaces/prepared/review`)
      await page.getByRole('button', { name: 'Review individually & next', exact: true }).click()
      await page.getByRole('button', { name: 'Approve glossary', exact: true }).click()
      await page.getByRole('dialog', { name: 'Approve glossary?' }).getByRole('button', { name: 'Approve selected forms' }).click()
      await page.getByText('✓ Glossary approved', { exact: true }).waitFor()
      await page.goto(`${base}/work/workspaces/prepared`)
      await page.getByRole('button', { name: 'Run', exact: true }).click()
      await wait(async () => (await (await context.request.get(`${base}/api/workspaces/prepared/pipeline`)).json()).publication.current,
        'offline translation and publication')
    },
  }

  async function capture(shot) {
    await page.goto(base + shot.path)
    await waitFor(page, shot.ready)
    await settled(page)
    await shot.prepare?.(page)
    await settled(page)
    if (shot.theme) await page.evaluate(theme => { document.documentElement.dataset.theme = theme }, shot.theme)
    await page.addStyleTag({ content: fontCss })
    await page.evaluate(() => Promise.all([400, 500, 600, 700, 800].map(w => document.fonts.load(`${w} 16px Inter`))))
    await page.evaluate(() => document.fonts.ready)
    const file = `${OUTPUT}/${shot.name}.png`
    await page.screenshot({ path: file, animations: 'disabled' })
    console.log('wrote', file)
  }

  for (let stage = 0; stage <= lastStage; stage++) {
    if (!live && stage) await advance[STAGES[stage]]()
    for (const shot of shots.filter(s => live || STAGES.indexOf(s.stage) === stage)) await capture(shot)
  }
  if (errors.length) console.warn('Page errors while capturing:\n' + errors.join('\n'))
} finally {
  await browser.close()
  if (server) {
    server.stdin.end('quit\n')
    await new Promise(done => { server.once('exit', done); setTimeout(() => { server.kill('SIGTERM'); done() }, 3000).unref() })
  }
}
