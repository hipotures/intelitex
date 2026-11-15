// Standard visual regression suite: the current Work UI against approved, version-controlled baseline images
// in tests/visual-baseline/, plus the 12 P0 preload layout comparisons. Every comparison runs; failures are
// reported together at the end. The historical v33 comparison is separate: npm run test:visual:v33.
//
// Baselines are never written here. With INTELITEX_VISUAL_CANDIDATES=1 the run also saves each screenshot as a
// candidate under the test evidence root (./tmp); adopting candidates is a deliberate, reviewed copy into
// tests/visual-baseline/. A missing baseline is a failure, never a skip.
import { testEvidence } from './paths.mjs'
import { verifyPreloadVisual } from './preload-visual.mjs'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { chromium } from 'playwright'
import { DIFF_BUDGET, THEMES, WORK_VIEWPORTS, compare, embedInter, failureCollector, readyWork, setTheme, startFixtureServer } from './visual-support.mjs'

const baselineDir = resolve(import.meta.dirname, 'visual-baseline')
const output = testEvidence('intelitex-visual-evidence')
const candidates = process.env.INTELITEX_VISUAL_CANDIDATES === '1' ? `${output}/candidates` : null
await mkdir(output, { recursive: true })
if (candidates) await mkdir(candidates, { recursive: true })

const { config, stop } = await startFixtureServer()
const browser = await chromium.launch()
const { failures, check } = failureCollector()
const results = []
let preloadResults = []
try {
  for (const [width, height] of WORK_VIEWPORTS) {
    for (const theme of THEMES) {
      const name = `work-${theme}-${width}`
      await check(name, async () => {
        // A fresh context per comparison: no state, hover or scroll position carries over between screenshots.
        const context = await browser.newContext({ viewport: { width, height }, colorScheme: theme, reducedMotion: 'reduce' })
        const page = await context.newPage()
        const errors = []
        page.on('pageerror', error => errors.push(error.message))
        page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
        try {
          await page.goto(config.url + '/work')
          await embedInter(page)
          await setTheme(page, theme)
          await readyWork(page)
          const actual = await page.screenshot({ path: `${output}/${name}-actual.png`, animations: 'disabled' })
          if (candidates) await writeFile(`${candidates}/${name}.png`, actual)
          const problems = []
          const topbar = await page.locator('.topbar').evaluate(n => n.getBoundingClientRect().height)
          if (topbar !== 64) problems.push(`topbar height ${topbar} ≠ 64`)
          if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) problems.push('horizontal scroll')
          if (errors.length) problems.push(`console errors: ${errors.join(' | ')}`)
          const baselinePath = `${baselineDir}/${name}.png`
          const result = { name, baseline: existsSync(baselinePath) }
          if (!result.baseline) problems.push(`no approved baseline at tests/visual-baseline/${name}.png`)
          else {
            const comparison = compare(actual, await readFile(baselinePath))
            if (comparison.diff) await writeFile(`${output}/${name}-diff.png`, comparison.diff)
            Object.assign(result, { differingPixels: comparison.pixels, percent: comparison.percent })
            if (comparison.sizeMismatch) problems.push(`size ${comparison.sizeMismatch}`)
            else if (comparison.pixels / (width * height) > DIFF_BUDGET) problems.push(`${comparison.percent}% exceeds the 1% budget`)
          }
          results.push(result)
          if (problems.length) throw new Error(problems.join('; '))
        } finally { await context.close() }
      })
    }
  }
  await check('p0 preload', async () => {
    const preload = await verifyPreloadVisual(browser, output + '/preload')
    preloadResults = preload.results
    failures.push(...preload.failures)
  })
} finally { await browser.close(); stop() }

await writeFile(output + '/report.json', JSON.stringify({ browser: browser.version(), unmasked: true, candidates, results, preloadComparisons: preloadResults.length, preloadResults, failures }, null, 2))
for (const r of results) console.log(`${r.name.padEnd(17)} ${r.baseline ? `${r.percent.toFixed(4)}%` : 'no baseline'}`)
console.log(`P0 preload comparisons: ${preloadResults.length}${candidates ? `; candidates written to ${candidates}` : ''}`)
if (failures.length) {
  console.error(`Visual regression: ${failures.length} failure(s):\n- ${failures.join('\n- ')}`)
  process.exit(1)
}
console.log(`Visual regression: all ${results.length} Work comparisons and ${preloadResults.length} P0 comparisons passed.`)
