import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { preloadFixture, preloadPage, readyPreload } from './preload-fixture.mjs'

export async function verifyPreloadVisual(browser, output) {
  await mkdir(output, { recursive: true })
  const fixture = await preloadFixture(), results = []
  for (const width of [1440, 1024, 390]) {
    const { page, errors } = await preloadPage(browser, fixture, 'p1', { width, height: width === 390 ? 844 : 1000 })
    try {
      for (const theme of ['dark', 'light']) {
        await readyPreload(page)
        for (let tries = 0; tries < 3 && await page.locator('html').getAttribute('data-theme') !== theme; tries++)
          await page.getByRole('button', { name: /Theme: .*\. Switch theme/ }).click()
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
        await page.evaluate(() => document.fonts.ready)
        await page.emulateMedia({ reducedMotion: 'reduce' })
        let previous
        for (const debug of [false, true]) {
          await page.getByRole('button', { name: 'Settings', exact: true }).click()
          await page.getByRole('tab', { name: 'Debug', exact: true }).click()
          await page.getByRole('checkbox', { name: 'Show panel identifiers' }).setChecked(debug)
          await page.getByRole('button', { name: 'Close', exact: true }).last().click()
          const measure = () => page.evaluate(() => {
            const rect = selector => document.querySelector(selector).getBoundingClientRect().toJSON()
            return { metrics: [...document.querySelectorAll('.phase-metric')].map(n => n.getBoundingClientRect().toJSON()),
              units: rect('[data-ui-debug-id="PNU"]'), preview: rect('[data-ui-debug-id="PNV"]'),
              costs: rect('[data-ui-debug-id="PNC"]'), grid: rect('.analyse-work-grid'),
              recent: rect('[data-ui-debug-id="RAL"]'), overflow: document.documentElement.scrollWidth > innerWidth + 1 }
          })
          const p0 = await measure()
          assert.equal(p0.overflow, false)
          assert.equal(p0.metrics.length, 5)
          assert.ok(Math.abs(p0.costs.width - p0.grid.width) < 1)
          assert.ok(p0.recent.top >= p0.costs.bottom)
          if (width > 900) {
            assert.ok(Math.abs(p0.units.top - p0.preview.top) < 1)
            assert.ok(Math.abs(p0.units.height - p0.preview.height) < 1)
            assert.ok(p0.preview.bottom <= 988, 'The asynchronously loaded P0 panel fits the viewport')
            assert.ok(p0.costs.top >= p0.units.bottom)
          } else assert.ok(p0.preview.top >= p0.units.bottom)
          if (previous) assert.deepEqual(p0, previous, 'Debug labels do not move P0 panels')
          previous = p0
          const name = `p0-${theme}-${width}-debug-${debug ? 'on' : 'off'}`
          await page.screenshot({ path: `${output}/${name}.png`, fullPage: true, animations: 'disabled' })
          await page.goto('http://localhost/work/workspaces/book/analyse')
          await page.locator('[data-ui-debug-id="PAN"]').waitFor()
          await page.locator('[data-ui-debug-id="PAV"] .translate-preview-text').first().waitFor()
          const p1 = await page.evaluate(() => ({
            metrics: [...document.querySelectorAll('.phase-metric')].map(n => n.getBoundingClientRect().toJSON()),
            units: document.querySelector('[data-ui-debug-id="PAN"]').getBoundingClientRect().toJSON(),
            preview: document.querySelector('[data-ui-debug-id="PAV"]').getBoundingClientRect().toJSON() }))
          assert.deepEqual(p0.metrics.map(m => [m.x, m.width, m.height]), p1.metrics.map(m => [m.x, m.width, m.height]), 'P0 and P1 use the same five metric sizes')
          assert.ok(Math.abs(p0.units.width - p1.units.width) < 1)
          assert.ok(Math.abs(p0.preview.width - p1.preview.width) < 1)
          await page.screenshot({ path: `${output}/${name.replace('p0-', 'p1-')}.png`, fullPage: true, animations: 'disabled' })
          await page.goto('http://localhost/work/workspaces/book')
          await page.locator('.phase-rail .phase').first().waitFor()
          const rail = await page.locator('.phase-rail .phase').evaluateAll(nodes => nodes.map(n => n.getBoundingClientRect().toJSON()))
          assert.equal(rail.length, 6)
          assert.equal(await page.locator('.sections-table thead th').count(), 8)
          if (width === 1440) assert.equal(new Set(rail.map(r => r.y)).size, 1)
          for (let i = 0; i < rail.length; i++) for (let j = i + 1; j < rail.length; j++)
            assert.ok(rail[i].right <= rail[j].left || rail[j].right <= rail[i].left || rail[i].bottom <= rail[j].top || rail[j].bottom <= rail[i].top, 'Rail tiles do not overlap')
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
          await page.screenshot({ path: `${output}/workspace-${theme}-${width}-debug-${debug ? 'on' : 'off'}.png`, fullPage: true, animations: 'disabled' })
          results.push({ name, p0, p1, rail })
          await readyPreload(page)
        }
      }
      assert.deepEqual(errors, [])
    } finally { await page.close() }
  }
  await writeFile(`${output}/report.json`, JSON.stringify({ nativeLoopbackCalls: 2, cloudCalls: 0, results }, null, 2))
  return results
}
