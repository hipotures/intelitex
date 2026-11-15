// Interaction states of the ds primitives, forced through the DevTools protocol (hover, focus-visible, active) and
// under both motion preferences. Pins two behaviors:
//  - ds alone (what an independent consumer gets): hover skips disabled controls, every variant dims when disabled,
//    no hover lift under reduced motion;
//  - Intelitex (ds + intelitex/ds-compat.css): the legacy production behavior is kept until a change is approved.
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import test from 'node:test'
import { chromium } from 'playwright'
import * as legacyCases from './ds-legacy/cases.mjs'

const styles = resolve('src/styles')
const read = files => Promise.all(files.map(f => readFile(resolve(styles, f), 'utf8'))).then(parts => parts.join('\n'))
const dsFiles = ['ds/tokens.css', 'ds/themes.css', 'ds/base.css', 'ds/components.css', 'ds/state.css']
const appFiles = ['ds/tokens.css', 'ds/themes.css', 'ds/base.css', 'ds/components.css', 'intelitex/ds-compat.css', 'ds/state.css']

const MARKUP = `
  <button id="primary" class="ds-btn ds-btn-primary">Run</button>
  <button id="primary-off" class="ds-btn ds-btn-primary" disabled>Run</button>
  <button id="secondary" class="ds-btn">Open</button>
  <button id="secondary-off" class="ds-btn" disabled>Open</button>
  <button id="ghost-off" class="ds-btn ds-btn-ghost" disabled>Cancel</button>
  <button id="danger-off" class="ds-btn ds-btn-danger" disabled>Archive</button>
  <button id="icon" class="ds-icon-btn" aria-label="Close">×</button>
  <button id="icon-off" class="ds-icon-btn" aria-label="Close" disabled>×</button>
  <div class="ds-segmented" role="group" aria-label="Filter"><button id="seg-on" aria-pressed="true">All</button><button id="seg-off" aria-pressed="false">Full</button></div>
  <div class="ds-field"><label for="bad">Label</label><input id="bad" aria-invalid="true"></div>
  <div class="ds-field"><label for="ok">Label</label><input id="ok"></div>
  <div class="ds-field"><label for="locked">Label</label><input id="locked" disabled></div>`

async function harness(browser, files, reducedMotion) {
  const context = await browser.newContext({ reducedMotion })
  const page = await context.newPage()
  await page.setContent(`<!doctype html><html><head><style>${await read(files)}</style>
    <style>*,*::before,*::after{transition:none!important}</style></head><body>${MARKUP}</body></html>`)
  const cdp = await context.newCDPSession(page)
  await cdp.send('DOM.enable'); await cdp.send('CSS.enable')
  const { root } = await cdp.send('DOM.getDocument', { depth: -1 })
  const style = async (id, states = [], props = ['background-color', 'border-color', 'opacity', 'filter', 'transform', 'cursor', 'outline-style', 'outline-color']) => {
    const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector: `#${id}` })
    await cdp.send('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: states })
    const values = await page.$eval(`#${id}`, (e, props) => Object.fromEntries(props.map(p => [p, getComputedStyle(e).getPropertyValue(p)])), props)
    await cdp.send('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: [] })
    return values
  }
  return { style, close: () => context.close() }
}
const lift = 'matrix(1, 0, 0, 1, 0, -1)'
const dark = { panel: 'rgb(20, 24, 30)', panel2: 'rgb(23, 28, 35)', panel3: 'rgb(29, 35, 44)', lineStrong: 'rgb(53, 64, 77)', accent: 'rgb(79, 157, 234)' }

test('ds primitives: interaction states without application compatibility', async () => {
  const browser = await chromium.launch()
  try {
    const h = await harness(browser, dsFiles, 'no-preference')
    // Hover.
    assert.equal((await h.style('secondary', ['hover']))['background-color'], dark.panel2)
    assert.equal((await h.style('secondary', ['hover']))['border-color'], dark.lineStrong)
    assert.equal((await h.style('icon', ['hover']))['background-color'], dark.panel2)
    assert.deepEqual(await h.style('primary', ['hover']), { ...(await h.style('primary')), filter: 'brightness(1.07)', transform: lift })
    // Hover never restyles a disabled control.
    assert.deepEqual(await h.style('secondary-off', ['hover']), await h.style('secondary-off'))
    assert.deepEqual(await h.style('primary-off', ['hover']), await h.style('primary-off'))
    // Disabled: every variant dims and shows the not-allowed cursor.
    for (const id of ['primary-off', 'secondary-off', 'ghost-off', 'danger-off', 'icon-off'])
      assert.deepEqual([(await h.style(id)).opacity, (await h.style(id)).cursor], ['0.45', 'not-allowed'], id)
    // Keyboard focus ring.
    for (const id of ['primary', 'secondary', 'icon', 'seg-on', 'ok']) {
      const s = await h.style(id, ['focus', 'focus-visible'])
      assert.deepEqual([s['outline-style'], s['outline-color']], ['solid', dark.accent], id)
    }
    assert.equal((await h.style('secondary', ['focus']))['outline-style'], 'none', 'mouse focus without :focus-visible has no ring')
    // Active adds nothing of its own.
    assert.deepEqual(await h.style('secondary', ['active']), await h.style('secondary'))
    // Selected segmented option and invalid / disabled fields.
    assert.equal((await h.style('seg-on'))['background-color'], dark.panel3)
    assert.equal((await h.style('seg-off'))['background-color'], 'rgba(0, 0, 0, 0)')
    assert.notEqual((await h.style('bad'))['border-color'], (await h.style('ok'))['border-color'])
    assert.equal((await h.style('bad'))['border-color'], 'color(srgb 0.664706 0.310588 0.308627)', '70% red mixed into the line color')
    assert.deepEqual([(await h.style('locked')).opacity, (await h.style('locked')).cursor], ['0.6', 'not-allowed'])
    await h.close()
    // Reduced motion: no hover lift, brightness stays.
    const r = await harness(browser, dsFiles, 'reduce')
    const hovered = await r.style('primary', ['hover'])
    assert.deepEqual([hovered.transform, hovered.filter], ['none', 'brightness(1.07)'])
    await r.close()
  } finally { await browser.close() }
})

test('Intelitex compatibility layer keeps the legacy interaction states', async () => {
  const browser = await chromium.launch()
  try {
    const h = await harness(browser, appFiles, 'no-preference')
    assert.equal((await h.style('secondary-off', ['hover']))['background-color'], dark.panel2, 'hover still styles disabled buttons')
    assert.equal((await h.style('icon-off', ['hover']))['border-color'], dark.lineStrong)
    const primaryOff = await h.style('primary-off', ['hover'])
    assert.deepEqual([primaryOff.filter, primaryOff.transform, primaryOff.opacity], ['brightness(1.07)', 'none', '0.45'])
    for (const id of ['danger-off', 'icon-off']) assert.deepEqual([(await h.style(id)).opacity, (await h.style(id)).cursor], ['1', 'not-allowed'], id)
    for (const id of ['primary-off', 'secondary-off', 'ghost-off']) assert.equal((await h.style(id)).opacity, '0.45', id)
    assert.equal((await h.style('primary', ['hover'])).transform, lift)
    assert.deepEqual([(await h.style('locked')).opacity, (await h.style('locked')).cursor], ['1', 'default'], 'disabled field controls are not dimmed')
    await h.close()
    const r = await harness(browser, appFiles, 'reduce')
    assert.equal((await r.style('primary', ['hover'])).transform, lift, 'legacy keeps the lift under reduced motion')
    await r.close()
  } finally { await browser.close() }
})

// Migrated Intelitex components against their frozen legacy CSS: the full app stylesheet stack with the legacy
// rules at their former cascade position versus the stack the app ships, every theme, motion preference and state.
const appStack = ['ds/tokens.css', 'ds/themes.css', 'intelitex/themes.css', 'ds/base.css', 'ds/components.css', 'intelitex/ds-compat.css',
  'intelitex/shell.css', 'intelitex/components.css', 'intelitex/library.css', 'intelitex/pipeline.css', 'intelitex/review.css',
  'intelitex/reader.css', 'ds/state.css', 'intelitex/debug.css']
const legacyFixture = resolve('tests/ds-legacy/components.css')
const STATES = [[], ['hover'], ['focus', 'focus-visible'], ['active'], ['hover', 'active']]

test('migrated components keep the legacy styles in every interaction state', async () => {
  const appCss = await read(appStack)
  const legacyCss = (await read(appStack.slice(0, 7))) + '\n' + await readFile(legacyFixture, 'utf8') + '\n' + await read(appStack.slice(7))
  const cases = Object.values(legacyCases).flat()
  const browser = await chromium.launch()
  const diffs = []
  try {
    // Desktop and phone widths: some primitives change at the 720px breakpoint.
    for (const [reducedMotion, width] of [['no-preference', 1280], ['reduce', 1280], ['no-preference', 390]]) {
      const context = await browser.newContext({ reducedMotion, viewport: { width, height: 800 } })
      const pages = {}
      for (const [side, css] of [['legacy', legacyCss], ['migrated', appCss]]) {
        const page = pages[side] = await context.newPage()
        await page.setContent(`<!doctype html><html><head><style>${css}</style><style id="freeze"></style></head><body><div id="host"></div></body></html>`)
        page.cdp = await context.newCDPSession(page)
        await page.cdp.send('DOM.enable'); await page.cdp.send('CSS.enable')
      }
      for (const c of cases) for (const theme of ['dark', 'light', 'sepia']) for (const states of STATES) {
        const result = {}
        for (const side of ['legacy', 'migrated']) {
          const page = pages[side]
          await page.evaluate(({ markup, theme, freeze }) => {
            document.documentElement.dataset.theme = theme
            document.getElementById('host').innerHTML = markup
            // The default state keeps transitions so their values are compared; forced states read final values.
            document.getElementById('freeze').textContent = freeze ? '*,*::before,*::after{transition:none!important}' : ''
          }, { markup: (c.context ?? '{}').replace('{}', c[side]), theme, freeze: states.length > 0 })
          if (states.length) {
            const { root } = await page.cdp.send('DOM.getDocument', { depth: -1 })
            const { nodeIds } = await page.cdp.send('DOM.querySelectorAll', { nodeId: root.nodeId, selector: '#host [data-target]' })
            assert.ok(nodeIds.length, `${c.name}: target`)
            for (const nodeId of nodeIds) await page.cdp.send('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: states })
          }
          result[side] = await page.evaluate(() => [...document.querySelectorAll('#host *')].flatMap(e => [null, '::before', '::after'].map(pseudo => {
            const s = getComputedStyle(e, pseudo)
            if (pseudo && (s.content === 'none' || s.content === 'normal')) return null
            return Object.fromEntries([...s].filter(p => !p.startsWith('--')).map(p => [p, s.getPropertyValue(p)]))
          }).filter(Boolean)))
        }
        const label = `${c.name} [${theme}, ${reducedMotion}, ${width}px, ${states.join('+') || 'default'}]`
        if (result.legacy.length !== result.migrated.length) { diffs.push(`${label}: element count`); continue }
        result.legacy.forEach((values, i) => { for (const p in values) if (values[p] !== result.migrated[i][p]) diffs.push(`${label} #${i} ${p}: ${values[p]} ≠ ${result.migrated[i][p]}`) })
      }
      await context.close()
    }
  } finally { await browser.close() }
  assert.deepEqual(diffs.slice(0, 20), [])
})
