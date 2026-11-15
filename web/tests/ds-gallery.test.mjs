// Design-system gallery: opens web/src/styles/ds/gallery.html straight from disk (no build, no server) and checks that
// it loads cleanly, that its controls work from the keyboard, that the accent hook and themes apply, and that every
// ds- primitive computes exactly the same default-state styles as the Intelitex component it was extracted from.
import { testEvidence } from './paths.mjs'
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import { chromium } from 'playwright'

const styles = resolve('src/styles')
const gallery = pathToFileURL(resolve(styles, 'ds/gallery.html')).href

// [Intelitex markup, ds markup]; each pair is rendered into the same parent and compared element by element.
const pairs = [
  ['<button class="primary-btn">Run</button>', '<button class="ds-btn ds-btn-primary">Run</button>'],
  ['<button class="secondary-btn">Open</button>', '<button class="ds-btn">Open</button>'],
  ['<button class="ghost-btn">Cancel</button>', '<button class="ds-btn ds-btn-ghost">Cancel</button>'],
  ['<button class="ghost-btn compact">Less</button>', '<button class="ds-btn ds-btn-ghost ds-btn-compact">Less</button>'],
  ['<button class="danger-btn">Archive</button>', '<button class="ds-btn ds-btn-danger">Archive</button>'],
  ['<button class="icon-btn" aria-label="x">×</button>', '<button class="ds-icon-btn" aria-label="x">×</button>'],
  ['<button class="primary-btn" disabled>Run</button>', '<button class="ds-btn ds-btn-primary" disabled>Run</button>'],
  ['<button class="secondary-btn" disabled>Open</button>', '<button class="ds-btn" disabled>Open</button>'],
  ['<button class="ghost-btn" disabled>Cancel</button>', '<button class="ds-btn ds-btn-ghost" disabled>Cancel</button>'],
  ['<div class="phase-detail-card"><div class="phase-detail-card-head"><h2>T</h2></div><div class="phase-detail-card-body"><p>B</p></div></div>',
    '<div class="ds-panel"><div class="ds-panel-head"><h2 class="ds-panel-title">T</h2></div><div class="ds-panel-body"><p>B</p></div></div>'],
  ['<div class="phase-detail-card-body"><table><tbody><tr><td>1</td></tr></tbody></table></div>',
    '<div class="ds-panel-body"><table><tbody><tr><td>1</td></tr></tbody></table></div>'],
  ['<div class="table-card"><table><thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table></div>',
    '<div class="ds-table-card"><table><thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table></div>'],
  ['<span class="phase-status-badge">Idle</span>', '<span class="ds-badge">Idle</span>'],
  ['<span class="phase-status-badge done">Done</span>', '<span class="ds-badge" data-tone="success">Done</span>'],
  ['<span class="phase-status-badge active">Run</span>', '<span class="ds-badge" data-tone="accent">Run</span>'],
  ['<span class="phase-status-badge error">Err</span>', '<span class="ds-badge" data-tone="danger">Err</span>'],
  ['<span class="count-badge">3</span>', '<span class="ds-count">3</span>'],
  ...['', 'running', 'waiting', 'error', 'done'].map(s => [`<span class="status-dot ${s}"></span>`, `<span class="ds-status-dot" data-status="${s}"></span>`]),
  ['<div class="notice"><div><strong>S</strong><p>P</p></div><button class="primary-btn">Go</button></div>',
    '<div class="ds-notice"><div><strong>S</strong><p>P</p></div><button class="ds-btn ds-btn-primary">Go</button></div>'],
  ['<div class="notice error"><div><strong>S</strong><p>P</p></div></div>', '<div class="ds-notice" data-tone="danger"><div><strong>S</strong><p>P</p></div></div>'],
  ['<div class="setup-body"><div class="field-row"><div class="field"><label>L</label><input value="v"><small>H</small></div><div class="field"><label>S</label><select><option>a</option></select></div></div></div>',
    '<div class="setup-body"><div class="ds-field-row"><div class="ds-field"><label>L</label><input value="v"><small class="ds-field-hint">H</small></div><div class="ds-field"><label>S</label><select><option>a</option></select></div></div></div>'],
  ['<div class="filter-segment"><button class="active">A</button><button>B</button></div>',
    '<div class="ds-segmented"><button aria-pressed="true">A</button><button aria-pressed="false">B</button></div>'],
  ['<div class="empty-note">None</div>', '<div class="ds-empty">None</div>'],
]
// Files that hold the original components; loaded only for the parity check, after the gallery's own styles.
const intelitex = ['intelitex/shell.css', 'intelitex/components.css', 'intelitex/library.css']
// Buttons animate color changes (transition), so wait for the settled value.
const primaryBackground = (page, expected) => page.waitForFunction(value =>
  getComputedStyle(document.querySelector('.ds-btn-primary')).backgroundColor === value, expected, { timeout: 2000 })

test('design-system gallery works standalone and matches Intelitex components', async () => {
  const evidence = testEvidence('ds-gallery'); await mkdir(evidence, { recursive: true })
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
    const problems = []
    page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') problems.push(m.text()) })
    page.on('pageerror', e => problems.push(e.message))
    page.on('requestfailed', r => problems.push(`failed ${r.url()}`))
    await page.goto(gallery)
    await page.waitForLoadState('load')
    assert.deepEqual(problems, [], 'gallery loads without errors or failed requests')
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ds-space-6').trim()), '12px', 'tokens.css loaded')
    assert.equal(await page.locator('.ds-btn-primary').first().evaluate(e => getComputedStyle(e).backgroundColor), 'rgb(79, 157, 234)', 'dark accent')
    await page.screenshot({ path: resolve(evidence, 'gallery-dark.png'), fullPage: true })

    // Accessible names and labels.
    const unnamed = await page.evaluate(() => [...document.querySelectorAll('button, input, select')]
      .filter(e => !(e.getAttribute('aria-label') || e.labels?.length || e.textContent.trim())).map(e => e.outerHTML))
    assert.deepEqual(unnamed, [], 'every control has an accessible name')

    // Keyboard: the first Tab stop is the theme toggle, it shows the focus ring and Space switches the theme.
    await page.keyboard.press('Tab')
    const focused = await page.evaluate(() => { const e = document.activeElement, s = getComputedStyle(e); return { text: e.textContent, outline: s.outlineStyle, width: s.outlineWidth } })
    assert.deepEqual(focused, { text: 'Dark', outline: 'solid', width: '2px' })
    await page.keyboard.press('Tab'); await page.keyboard.press('Space')
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light')
    assert.equal(await page.locator('#theme button[data-theme-value="light"]').getAttribute('aria-pressed'), 'true')
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), 'rgb(245, 246, 248)', 'light background')
    await primaryBackground(page, 'rgb(47, 126, 209)')   // light accent
    await page.mouse.move(0, 0); await page.waitForTimeout(300)   // let hover and color transitions settle before the screenshot
    await page.screenshot({ path: resolve(evidence, 'gallery-light.png'), fullPage: true })

    // Accent hook: one custom property recolors primary buttons in either theme.
    await page.locator('#accent').evaluate(e => { e.value = '#9a4fd6'; e.dispatchEvent(new Event('input', { bubbles: true })) })
    await primaryBackground(page, 'rgb(154, 79, 214)')
    await page.locator('#accent-reset').click()
    await primaryBackground(page, 'rgb(47, 126, 209)')

    // Parity with the Intelitex components, in both themes.
    // Transitions are disabled for the comparison so that computed colors are final values, not interpolations.
    await page.addStyleTag({ content: '*, *::before, *::after { transition: none !important; }' })
    for (const file of intelitex) await page.addStyleTag({ url: pathToFileURL(resolve(styles, file)).href })
    for (const theme of ['dark', 'light']) {
      const diffs = await page.evaluate(({ pairs, theme }) => {
        document.documentElement.dataset.theme = theme
        const host = document.createElement('div'); host.style.width = '600px'; document.body.append(host)
        const out = []
        for (const [a, b] of pairs) {
          const x = document.createElement('div'), y = document.createElement('div'); x.innerHTML = a; y.innerHTML = b; host.append(x, y)
          const ex = [...x.querySelectorAll('*')], ey = [...y.querySelectorAll('*')]
          ex.forEach((e, i) => {
            const sx = getComputedStyle(e), sy = getComputedStyle(ey[i])
            for (const prop of sx) if (!prop.startsWith('--') && sx.getPropertyValue(prop) !== sy.getPropertyValue(prop))
              out.push(`${a.slice(0, 40)} <${e.tagName.toLowerCase()}> ${prop}: ${sx.getPropertyValue(prop)} ≠ ${sy.getPropertyValue(prop)}`)
          })
        }
        host.remove()
        return out
      }, { pairs, theme })
      assert.deepEqual(diffs, [], `ds primitives equal Intelitex components (${theme})`)
    }
    assert.deepEqual(problems, [], 'no console errors during the run')
  } finally { await browser.close() }
})
