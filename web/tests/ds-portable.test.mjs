// Portability of web/design-system/: it must work on a page that loads nothing else (no Intelitex CSS, no Tailwind,
// no reset, no framework, no bundler). The interaction states themselves are pinned in ds-states.test.mjs (which runs
// the package without any reset); this file covers the standalone consumer and the package contract.
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { resolve, sep } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import { chromium } from 'playwright'

const pkg = resolve('design-system')
const example = pathToFileURL(resolve(pkg, 'examples/standalone.html')).href
const cssFiles = (await readdir(pkg)).filter(f => f.endsWith('.css'))
const sources = Object.fromEntries(await Promise.all(cssFiles.map(async f => [f, await readFile(resolve(pkg, f), 'utf8')])))
const stripComments = css => css.replace(/\/\*[\s\S]*?\*\//g, '')
// Hooks a consumer may set; every use has a fallback.
const OPTIONAL = new Set(['--ds-accent-dark', '--ds-accent-soft-dark', '--ds-accent-light', '--ds-accent-soft-light', '--ds-control-font-size'])

async function withBrowser(run) {
  const browser = await chromium.launch()
  try { return await run(browser) } finally { await browser.close() }
}
async function open(browser, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 }, ...options })
  const page = await context.newPage()
  const problems = [], requests = []
  page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') problems.push(m.text()) })
  page.on('pageerror', e => problems.push(e.message))
  page.on('requestfailed', r => problems.push(`failed ${r.url()}`))
  page.on('request', r => requests.push(r.url()))
  await page.goto(example)
  return { page, context, problems, requests }
}
const css = (page, selector, prop) => page.$eval(selector, (e, p) => getComputedStyle(e).getPropertyValue(p), prop)
const settled = (page, selector, prop, value) => page.waitForFunction(([s, p, v]) =>
  getComputedStyle(document.querySelector(s)).getPropertyValue(p) === v, [selector, prop, value], { timeout: 2000 })

test('the package is self-contained plain CSS with ds- classes only', () => {
  const all = Object.values(sources).map(stripComments).join('\n')
  // 1. No framework or build-tool syntax, no imports from outside the package.
  assert.doesNotMatch(all, /@tailwind|@apply|@theme|@layer|@plugin|@config|theme\(/, 'no Tailwind or build-time CSS')
  for (const [, url] of all.matchAll(/@import\s+(?:url\()?["']?([^"')\s;]+)/g)) assert.ok(cssFiles.includes(url), `import stays in the package: ${url}`)
  // 3. No Intelitex names: every class selector is a ds- class.
  const classes = new Set([...all.replace(/url\([^)]*\)|"[^"]*"|'[^']*'/g, '').matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)].map(m => m[1]).filter(c => !/^\d/.test(c)))
  assert.deepEqual([...classes].filter(c => !c.startsWith('ds-')), [], 'only ds- classes')
  assert.doesNotMatch(Object.values(sources).join('\n'), /intelitex/i, 'no reference to the application')
  // 2 (static). Every var() without a fallback names a property the package defines.
  const defined = new Set([...all.matchAll(/(--[\w-]+)\s*:/g)].map(m => m[1]))
  const used = [...all.matchAll(/var\(\s*(--[\w-]+)\s*(,)?/g)]
  assert.deepEqual(used.filter(([, name, fallback]) => !defined.has(name) && !(fallback && OPTIONAL.has(name))).map(m => m[1]), [], 'undefined custom properties')
  // 7 (static). Outside base.css, which is optional and documented, no rule targets plain elements: every selector
  // either names a ds- class or is :root / html[data-theme].
  for (const f of cssFiles.filter(f => f !== 'base.css')) {
    for (const [, selectors] of stripComments(sources[f]).matchAll(/(?:^|})\s*([^{}@]+?)\s*\{/g)) for (const selector of selectors.split(/,(?![^(]*\))/)) {
      const s = selector.trim()
      if (!s || /^(from|to|\d+%)$/.test(s)) continue
      assert.ok(/\.ds-/.test(s) || /^(:root|html\[data-theme="light"\])$/.test(s), `${f}: global selector ${s}`)
    }
  }
})

test('standalone consumer loads only the design system and every token resolves', () => withBrowser(async browser => {
  const { page, problems, requests } = await open(browser)
  assert.deepEqual(problems, [], 'no console errors or failed requests')
  // 1. Every request stays inside the package folder; the page has no application or framework CSS.
  assert.deepEqual(requests.filter(url => !url.startsWith(pathToFileURL(pkg + sep).href)), [])
  const sheets = await page.evaluate(() => [...document.styleSheets].map(s => s.href ? s.href.split('/design-system/')[1] : 'inline'))
  assert.deepEqual(sheets, ['ds.css', 'base.css', 'inline'])
  // 2. Every custom property the package defines on :root resolves to a value, in both themes.
  const names = [...new Set(Object.values(sources).flatMap(s => [...stripComments(s).matchAll(/(--[\w-]+)\s*:/g)].map(m => m[1])))]
  for (const theme of ['dark', 'light']) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t }, theme)
    const empty = await page.evaluate(list => list.filter(n => !getComputedStyle(document.documentElement).getPropertyValue(n).trim()), names)
    assert.deepEqual(empty, [], `unresolved properties (${theme})`)
  }
  // Primitives are actually styled (not browser defaults).
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark' })
  await settled(page, '#publish', 'background-color', 'rgb(79, 157, 234)')
  assert.equal(await css(page, '.ds-table th', 'text-transform'), 'uppercase')
  assert.equal(await css(page, '.ds-panel', 'border-top-left-radius'), '12px')
}))

test('themes, accent hook, keyboard focus, disabled and reduced motion in the standalone consumer', () => withBrowser(async browser => {
  const { page } = await open(browser)
  // 4. Dark by default; the light theme switches surfaces, text and the accent.
  assert.equal(await css(page, 'html', 'background-color'), 'rgb(13, 15, 18)')
  assert.equal(await css(page, 'html', 'color-scheme'), 'dark')
  await page.getByRole('button', { name: 'Light' }).click()
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light')
  await settled(page, 'html', 'background-color', 'rgb(245, 246, 248)')
  assert.equal(await css(page, 'html', 'color-scheme'), 'light')
  await settled(page, '#publish', 'background-color', 'rgb(47, 126, 209)')
  await settled(page, '.ds-panel', 'background-color', 'rgb(255, 255, 255)')
  // 5. Accent hooks recolor primary buttons and accent badges in either theme.
  await page.getByRole('button', { name: 'Brand accent' }).click()
  await settled(page, '#publish', 'background-color', 'rgb(109, 63, 216)')
  assert.equal(await css(page, '.ds-badge[data-tone="accent"]', 'color'), 'rgb(109, 63, 216)')
  await page.getByRole('button', { name: 'Dark' }).click()
  await settled(page, '#publish', 'background-color', 'rgb(154, 107, 255)')
  // 6. Documented states: disabled controls, the invalid field and the pressed segmented option.
  assert.deepEqual([await css(page, 'button[disabled]', 'opacity'), await css(page, 'button[disabled]', 'cursor')], ['0.45', 'not-allowed'])
  assert.deepEqual([await css(page, '#owner', 'opacity'), await css(page, '#owner', 'cursor')], ['0.6', 'not-allowed'])
  assert.notEqual(await css(page, '#slug', 'border-top-color'), await css(page, '#name', 'border-top-color'))
  await page.getByRole('button', { name: 'Failed' }).click()
  assert.equal(await page.getByRole('button', { name: 'Failed' }).getAttribute('aria-pressed'), 'true')
  assert.equal(await css(page, '#filter button[aria-pressed="true"]', 'background-color'), 'rgb(29, 35, 44)')
  // 8. Every keyboard stop shows the focus ring (disabled controls are skipped by the browser).
  await page.mouse.click(5, 5)
  const stops = []
  for (let i = 0; i < 20; i++) {
    await page.keyboard.press('Tab')
    const s = await page.evaluate(() => { const e = document.activeElement, c = getComputedStyle(e)
      return e === document.body ? null : { name: e.id || e.textContent.trim() || e.getAttribute('aria-label'), style: c.outlineStyle, width: parseFloat(c.outlineWidth) } })
    if (!s || stops.some(x => x.name === s.name)) break
    stops.push(s)
  }
  assert.ok(stops.length >= 14, `tab stops: ${stops.length}`)
  assert.deepEqual(stops.filter(s => s.style !== 'solid' || s.width < 2), [], 'every focused control shows a 2px ring')
  // 9. Reduced motion: the hover lift is dropped; with no preference it is kept.
  await page.mouse.move(0, 0)
  await page.hover('#publish'); await settled(page, '#publish', 'transform', 'matrix(1, 0, 0, 1, 0, -1)')
  const reduced = await open(browser, { reducedMotion: 'reduce' })
  await reduced.page.hover('#publish'); await settled(reduced.page, '#publish', 'filter', 'brightness(1.07)')
  assert.equal(await css(reduced.page, '#publish', 'transform'), 'none')
}))

test('control font size, icons, panel overflow, badge tones, standalone controls and the dialog', () => withBrowser(async browser => {
  const { page, problems } = await open(browser)
  // 10. Controls use --ds-control-font-size (14px here) although the body text is 16px; compact keeps its own size.
  assert.equal(await css(page, 'body', 'font-size'), '16px')
  for (const sel of ['#publish', '#name', '#lang', '#notes', '#sort', '#tag', '#summary'])
    assert.equal(await css(page, sel, 'font-size'), '14px', sel)
  assert.equal(await css(page, '#brand', 'font-size'), '13px')
  // Unset, the hook falls back to the inherited size (1em).
  await page.evaluate(() => document.documentElement.style.setProperty('--ds-control-font-size', 'initial'))
  await settled(page, '#publish', 'font-size', '16px')
  await page.evaluate(() => document.documentElement.style.removeProperty('--ds-control-font-size'))
  // 11. An icon child is laid out next to the label.
  // (#publish is a flex item of the notice, so inline-flex computes as flex.)
  assert.deepEqual([await css(page, '#publish', 'display'), await css(page, '#publish', 'align-items'), await css(page, '#publish', 'column-gap')],
    ['flex', 'center', '6px'])
  assert.equal(await css(page, '#form button[type="submit"]', 'display'), 'block', 'text-only buttons keep their display')
  // 12. Panel overflow modifiers.
  await page.evaluate(() => { const p = document.querySelector('.ds-panel'); p.classList.add('ds-panel-overflow-visible') })
  assert.equal(await css(page, '.ds-panel', 'overflow-x'), 'visible')
  await page.evaluate(() => { const p = document.querySelector('.ds-panel'); p.classList.replace('ds-panel-overflow-visible', 'ds-panel-overflow-auto') })
  assert.equal(await css(page, '.ds-panel', 'overflow-x'), 'auto')
  // 13. Badge tones: warning uses the amber token; neutral equals the default badge.
  assert.equal(await css(page, '.ds-badge[data-tone="warning"]', 'color'), 'rgb(213, 166, 62)')
  const neutral = await page.evaluate(() => {
    const a = document.createElement('span'), b = document.createElement('span')
    a.className = b.className = 'ds-badge'; b.dataset.tone = 'neutral'; a.textContent = b.textContent = 'x'
    document.body.append(a, b)
    const pick = e => ['color', 'background-color', 'border-top-color'].map(p => getComputedStyle(e).getPropertyValue(p))
    return [pick(a), pick(b)]
  })
  assert.deepEqual(neutral[1], neutral[0])
  // 14. Standalone controls match the field look and keep their own width; the textarea fills its container.
  for (const p of ['background-color', 'border-top-color', 'border-top-left-radius', 'padding-left'])
    assert.equal(await css(page, '#sort', p), await css(page, '#lang', p), p)
  assert.ok(parseFloat(await css(page, '#sort', 'width')) < parseFloat(await css(page, '#lang', 'width')))
  assert.equal(await css(page, '#summary', 'width'), await css(page, '#form', 'width'))
  await page.evaluate(() => { const t = document.getElementById('tag'); t.disabled = true; t.setAttribute('aria-invalid', 'true') })
  assert.deepEqual([await css(page, '#tag', 'opacity'), await css(page, '#tag', 'cursor')], ['0.6', 'not-allowed'])
  assert.equal(await css(page, '#tag', 'border-top-color'), await css(page, '#slug', 'border-top-color'))
  // 15. Dialog: modal from showModal, accessible name, focus inside, Esc closes, focus returns, result from the form.
  await page.focus('#delete'); await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: 'Delete this project?' })
  assert.ok(await dialog.isVisible())
  assert.equal(await page.evaluate(() => document.getElementById('confirm').matches(':modal')), true)
  assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Cancel', 'first control focused')
  assert.equal(await css(page, '#confirm', 'overscroll-behavior-y'), 'contain')
  assert.equal(await page.evaluate(() => getComputedStyle(document.getElementById('confirm'), '::backdrop').backgroundColor), 'rgba(0, 0, 0, 0.55)')
  await page.keyboard.press('Escape')
  assert.equal(await dialog.isVisible(), false)
  assert.equal(await page.evaluate(() => document.activeElement.id), 'delete', 'focus returns to the opener')
  // Esc fires close asynchronously; let it run (result '') before reopening.
  await page.waitForFunction(() => document.getElementById('confirm').dataset.result === '', null, { timeout: 2000 })
  await page.keyboard.press('Enter')
  await page.keyboard.press('Tab'); await page.keyboard.press('Enter')
  // The close event is queued, so wait for the listener to record the result.
  await page.waitForFunction(() => document.getElementById('confirm').dataset.result === 'delete', null, { timeout: 2000 })
  assert.deepEqual(problems, [])
}))

// 7. Unrelated markup on a page that loads ds.css computes exactly as on a page without it. The only intended global
// effect is the theme's color-scheme on the root, so the comparison page declares the same color-scheme.
const UNRELATED = `<h1>Title</h1><h2>Sub</h2><p>Text <a href="#">link</a> <strong>bold</strong> <small>small</small> <code>code</code></p>
  <button type="button">Button</button><input value="x"><select><option>o</option></select><textarea>t</textarea>
  <label><input type="checkbox"> check</label><table><thead><tr><th>H</th></tr></thead><tbody><tr><td>D</td></tr></tbody></table>
  <progress value="3" max="10"></progress><ul><li>item</li></ul><details><summary>More</summary>x</details><hr>`
const entry = [...stripComments(sources['ds.css']).matchAll(/@import\s+url\("([^"]+)"\)/g)].map(m => sources[m[1]]).join('\n')
test('ds.css does not restyle unrelated HTML', () => withBrowser(async browser => {
  const page = await browser.newPage()
  const snapshot = () => page.evaluate(() => [...document.querySelectorAll('html, body, body *')].map(e => {
    const s = getComputedStyle(e)
    return [e.tagName, Object.fromEntries([...s].filter(p => !p.startsWith('--')).map(p => [p, s.getPropertyValue(p)]))]
  }))
  for (const theme of ['dark', 'light']) {
    // ds.css inlined (the files it imports, in its order): a page from setContent cannot load file:// stylesheets.
    await page.setContent(`<!doctype html><html${theme === 'light' ? ' data-theme="light"' : ''}><head><style>${entry}</style></head><body>${UNRELATED}</body></html>`)
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ds-space-1')), '2px')
    const withDs = await snapshot()
    await page.setContent(`<!doctype html><html><head><style>:root { color-scheme: ${theme} }</style></head><body>${UNRELATED}</body></html>`)
    const without = await snapshot()
    const diffs = []
    withDs.forEach(([tag, values], i) => { for (const p in values) if (values[p] !== without[i][1][p]) diffs.push(`${tag} ${p}: ${without[i][1][p]} → ${values[p]}`) })
    assert.deepEqual(diffs, [], `leaks (${theme})`)
  }
}))

// Primitives must not rely on a CSS reset: with and without Tailwind preflight they compute the same styles.
test('primitives compute the same with or without a CSS reset', () => withBrowser(async browser => {
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } })
  const markup = (await readFile(resolve(pkg, 'examples/standalone.html'), 'utf8')).match(/<body>([\s\S]*)<script>/)[1]
  const preflight = await readFile(resolve('node_modules/tailwindcss/preflight.css'), 'utf8')
  // The page context is the same on both sides (preflight would otherwise also change the inherited line height and
  // the body margin); what differs is only the reset's effect on the primitives themselves.
  const page_ = 'html { line-height: normal } body { margin: 0 }'
  const capture = async reset => {
    await page.setContent(`<!doctype html><html><head><style>${reset ? `@layer base {${preflight}}` : ''}${entry}${page_}</style></head><body>${markup}</body></html>`)
    return page.evaluate(() => [...document.querySelectorAll('[class*="ds-"], .ds-segmented button')].map(e => {
      const s = getComputedStyle(e)
      return [e.className || e.tagName, Object.fromEntries([...s].filter(p => !p.startsWith('--')).map(p => [p, s.getPropertyValue(p)]))]
    }))
  }
  const bare = await capture(false), reset = await capture(true)
  // Only the primitive elements and the properties they are responsible for are compared; content inside them
  // (paragraphs, labels' text) follows the page's own reset.
  const owned = ['box-sizing', 'margin-top', 'margin-right', 'margin-bottom', 'margin-left', 'padding-top', 'padding-right', 'padding-bottom',
    'padding-left', 'font-family', 'font-size', 'font-weight', 'line-height', 'color', 'background-color', 'border-top-width', 'border-top-style',
    'border-top-color', 'border-top-left-radius', 'width', 'height', 'display', 'text-transform', 'letter-spacing', 'text-decoration-line', 'outline-style']
  const diffs = []
  // A border style without width draws nothing (preflight sets `border: 0 solid` everywhere).
  const invisible = (p, a, b) => p === 'border-top-style' && a['border-top-width'] === '0px' && b['border-top-width'] === '0px'
  bare.forEach(([name, values], i) => { for (const p of owned) if (values[p] !== reset[i][1][p] && !invisible(p, values, reset[i][1])) diffs.push(`${name} ${p}: ${values[p]} (bare) ≠ ${reset[i][1][p]} (preflight)`) })
  assert.deepEqual(diffs, [])
}))
