// Regenerates the README screenshots from the disposable offline fixture server.
//
//   npm --prefix web run screenshots              # every shot
//   npm --prefix web run screenshots -- review    # only shots whose name contains "review"
//
// Output goes to docs/images/<name>.png; README links those paths, so re-running this
// script is the whole update procedure. Edit SHOTS to add or change a screenshot.
// Uses only the offline fixture (tests/web_fixture_server.py): no real workspace, book or
// model is touched. Set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH to use a system Chromium.
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
// Every shot is the same viewport at 2x pixel density (2880x1800 files): uniform in a gallery
// and sharp on HiDPI screens or when the browser scales the image down.
const VIEWPORT = { width: 1440, height: 900 }
const SCALE = 2
const THEME = 'dark'

// Pipeline stages the fixture workspace passes through, in order. A shot declares the stage it
// needs. The viewport is captured by default; an optional `target` CSS selector (stable panel IDs
// from docs/web/debug-ids.md) clips to one panel instead, at the cost of a different image size. `ready` is heading:<name>, text:<string> or css:<selector>.
const STAGES = ['prepared', 'review', 'translated']
const SHOTS = [
  { name: 'work-home', stage: 'prepared', path: '/work', ready: 'heading:Books in progress' },
  { name: 'workspace-overview', stage: 'prepared', path: '/work/workspaces/prepared', ready: 'css:[data-ui-debug-id="SCT"]' },
  { name: 'review-terminology', stage: 'review', path: '/work/workspaces/prepared/review', ready: 'css:.review-term-row' },
  { name: 'reader', stage: 'translated', path: '/reader/prepared', ready: 'text:Translated: Relay-A moved steadily in section 1.' },
]

const filter = process.argv.slice(2)
const shots = SHOTS.filter(shot => !filter.length || filter.some(part => shot.name.includes(part)))
if (!shots.length) throw new Error(`No shot matches ${filter.join(', ')}; known: ${SHOTS.map(s => s.name).join(', ')}`)
const lastStage = Math.max(...shots.map(shot => STAGES.indexOf(shot.stage)))

const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'],
  { cwd: resolve(import.meta.dirname, '../..'), stdio: ['pipe', 'pipe', 'inherit'] })
const lines = createInterface({ input: server.stdout })
const config = await new Promise((done, fail) => {
  lines.on('line', line => { try { const value = JSON.parse(line); if (value.url) done(value) } catch { /* wait for readiness */ } })
  server.once('exit', () => fail(new Error('Fixture server exited before it was ready')))
})
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

try {
  await mkdir(OUTPUT, { recursive: true })
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: SCALE, colorScheme: THEME, bypassCSP: true, reducedMotion: 'reduce' })
  const page = await context.newPage()
  page.setDefaultTimeout(15000)
  const errors = []
  page.on('pageerror', error => errors.push(error.message))

  // Drive the fixture to the next stage with the same UI actions the browser tests use.
  const advance = {
    review: async () => {
      await page.goto(`${config.url}/work/workspaces/prepared`)
      await page.getByRole('button', { name: 'Run', exact: true }).click()
      await page.getByRole('button', { name: 'Open Review', exact: true }).waitFor()
    },
    translated: async () => {
      await page.goto(`${config.url}/work/workspaces/prepared/review`)
      await page.getByRole('button', { name: 'Review individually & next', exact: true }).click()
      await page.getByRole('button', { name: 'Approve glossary', exact: true }).click()
      await page.getByRole('dialog', { name: 'Approve glossary?' }).getByRole('button', { name: 'Approve selected forms' }).click()
      await page.getByText('✓ Glossary approved', { exact: true }).waitFor()
      await page.goto(`${config.url}/work/workspaces/prepared`)
      await page.getByRole('button', { name: 'Run', exact: true }).click()
      await wait(async () => (await (await context.request.get(`${config.url}/api/workspaces/prepared/pipeline`)).json()).publication.current,
        'offline translation and publication')
    },
  }

  for (let stage = 0; stage <= lastStage; stage++) {
    if (stage) await advance[STAGES[stage]]()
    for (const shot of shots.filter(s => STAGES.indexOf(s.stage) === stage)) {
      await page.goto(config.url + shot.path)
      await waitFor(page, shot.ready)
      await page.addStyleTag({ content: fontCss })
      await page.evaluate(() => Promise.all([400, 500, 600, 700, 800].map(w => document.fonts.load(`${w} 16px Inter`))))
      await page.evaluate(() => document.fonts.ready)
      const file = `${OUTPUT}/${shot.name}.png`
      await (shot.target ? page.locator(shot.target).first() : page).screenshot({ path: file, animations: 'disabled' })
      console.log('wrote', file)
    }
  }
  if (errors.length) console.warn('Page errors while capturing:\n' + errors.join('\n'))
} finally {
  await browser.close()
  server.stdin.end('quit\n')
  await new Promise(done => { server.once('exit', done); setTimeout(() => { server.kill('SIGTERM'); done() }, 3000).unref() })
}
