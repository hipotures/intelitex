// Shared helpers for the visual tests. Methodology is fixed: pixelmatch threshold .15, anti-aliasing excluded,
// and at most 1% differing pixels per comparison.
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import pixelmatch from 'pixelmatch'
import { PNG } from 'pngjs'

export const PIXEL_THRESHOLD = .15
export const DIFF_BUDGET = .01
// The 12 Work viewport/theme combinations, in the historical order.
export const WORK_VIEWPORTS = [[1440, 1000], [1920, 1080], [1024, 1000], [820, 1000], [720, 1000], [390, 844]]
export const THEMES = ['dark', 'light']

export async function startFixtureServer() {
  const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'], { cwd: '..', stdio: ['pipe', 'pipe', 'inherit'] })
  const lines = createInterface({ input: server.stdout })
  const config = await new Promise((done, fail) => {
    lines.on('line', line => { try { const data = JSON.parse(line); if (data.url) done(data) } catch { /* wait for readiness */ } })
    server.once('exit', code => fail(new Error(`Fixture server exited (${code}) before it was ready`)))
  })
  return { config, stop: () => server.stdin.end('quit\n') }
}

// The app asks for the system font Inter and does not bundle it, so baselines would otherwise depend on the
// machine's fonts. Test pages load the Inter the project already depends on, from bytes through the FontFace API:
// unlike an injected stylesheet this is not blocked by the app's Content Security Policy, which stays enforced.
const interFonts = await Promise.all(['latin', 'latin-ext'].map(async subset =>
  (await readFile(resolve(import.meta.dirname, `../node_modules/@fontsource-variable/inter/files/inter-${subset}-wght-normal.woff2`))).toString('base64')))
export async function embedInter(page) {
  await page.evaluate(async fonts => {
    for (const base64 of fonts) {
      const face = new FontFace('Inter', Uint8Array.from(atob(base64), c => c.charCodeAt(0)), { weight: '100 900', style: 'normal' })
      document.fonts.add(await face.load())
    }
    await document.fonts.ready
  }, interFonts)
}

export async function setTheme(page, theme) {
  // index.html ships a static data-theme="dark"; the stored preference applies only once the connected shell
  // (the one with the theme button) has mounted, so the attribute is read after that, never before.
  await page.getByRole('button', { name: /Theme: .*\. Switch theme/ }).waitFor()
  for (let tries = 0; tries < 3 && await page.locator('html').getAttribute('data-theme') !== theme; tries++)
    await page.getByRole('button', { name: /Theme: .*\. Switch theme/ }).click()
  if (await page.locator('html').getAttribute('data-theme') !== theme) throw new Error(`Could not select the ${theme} theme`)
}

/** Waits for the settled Work page state instead of a fixed delay: live connection, loaded summaries, actions and Library. */
export async function readyWork(page) {
  await page.locator('.connection-status.live').waitFor({ state: 'attached' })
  await page.locator('.workspace-state').first().waitFor({ state: 'attached' })
  await page.locator('.book-card').first().waitFor()
  await page.waitForFunction(() => {
    const main = document.querySelector('main')
    const refresh = document.querySelector('.library-refresh')
    return !!main && !/Loading/.test(main.innerText) && !!refresh && !refresh.disabled &&
      ![...document.querySelectorAll('.ds-count')].some(badge => badge.textContent === '—')
  })
  await page.evaluate(() => document.fonts.ready)
  // Park the pointer away from controls so no hover state is captured, then let two frames paint.
  await page.mouse.move(0, 0)
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))))
}

export function compare(actual, expected) {
  const one = PNG.sync.read(actual), two = PNG.sync.read(expected)
  if (one.width !== two.width || one.height !== two.height)
    return { sizeMismatch: `${one.width}×${one.height} vs ${two.width}×${two.height}`, pixels: one.width * one.height, percent: 100, diff: null }
  const diff = new PNG({ width: one.width, height: one.height })
  const pixels = pixelmatch(one.data, two.data, diff.data, one.width, one.height, { threshold: PIXEL_THRESHOLD, includeAA: false })
  return { pixels, percent: 100 * pixels / (one.width * one.height), diff: PNG.sync.write(diff) }
}

/** Collects every failure and reports them together, so one failing comparison never hides the rest. */
export function failureCollector() {
  const failures = []
  return {
    failures,
    async check(label, fn) {
      try { await fn() } catch (error) { failures.push(`${label}: ${error?.message ?? error}`) }
    },
  }
}
