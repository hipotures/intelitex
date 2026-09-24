import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { mkdir } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'

test('Translate runs one pass, previews output and explains rerun before confirmation', { timeout: 90000 }, async t => {
  const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'],
    { cwd: '..', stdio: ['pipe', 'pipe', 'pipe'] })
  let serverError = ''
  server.stderr.on('data', chunk => { serverError += chunk })
  t.after(async () => {
    server.stdin.end('quit\n')
    await new Promise(resolve => {
      server.once('exit', resolve)
      setTimeout(() => { server.kill('SIGTERM'); resolve() }, 3000).unref()
    })
  })
  const lines = createInterface({ input: server.stdout })
  const config = await new Promise((resolve, reject) => {
    lines.on('line', line => { try { const value = JSON.parse(line); if (value.url) resolve(value) } catch {} })
    server.once('exit', () => reject(new Error(serverError)))
  })
  const browser = await chromium.launch()
  t.after(() => browser.close())
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
  page.setDefaultTimeout(12000)
  const errors = [], failures = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('response', response => { if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`) })

  await page.goto(`${config.url}/work/workspaces/prepared`)
  await page.getByRole('button', { name: 'Run', exact: true }).click()
  await page.getByRole('button', { name: 'Open Review', exact: true }).waitFor()
  const review = await (await page.request.get(`${config.url}/api/workspaces/prepared/review`)).json()
  const bulk = await (await page.request.post(`${config.url}/api/workspaces/prepared/review/bulk-review`,
    { data: { revision: review._revision, term_ids: review.terms.map(term => term.id) } })).json()
  const approved = await page.request.post(`${config.url}/api/workspaces/prepared/review/confirm-and-approve`,
    { data: { revision: bulk.revision } })
  assert.equal(approved.status(), 200)
  const pipeline = await (await page.request.get(`${config.url}/api/workspaces/prepared/pipeline`)).json()
  const chunkId = pipeline.units[0].id
  await page.setViewportSize({ width: 1920, height: 1080 })
  const widths = []
  for (const [path, selector] of [['', 'main.workspace-page'], ['/prepare', 'main.prepare-detail-page'],
    ['/analyse', 'main.analyse-detail-page'], ['/review', 'main.review-page'], ['/translate', 'main.phase-detail-page']]) {
    await page.goto(`${config.url}/work/workspaces/prepared${path}`)
    widths.push(await page.locator(selector).evaluate(element => Math.round(element.getBoundingClientRect().width)))
  }
  assert.deepEqual([...new Set(widths)], [1520], `Workspace and phase widths differ: ${widths}`)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`${config.url}/work/workspaces/prepared/translate`)
  await page.getByRole('heading', { name: 'Translate', exact: true }).waitFor()
  await page.getByRole('button', { name: `Run ${chunkId} P2` }).click()
  const dialog = page.getByRole('dialog', { name: 'Run this pass?' })
  await dialog.getByText('spends tokens', { exact: false }).waitFor()
  const started = page.waitForResponse(response => response.url().endsWith('/api/workspaces/prepared/jobs') && response.request().method() === 'POST')
  await dialog.getByRole('button', { name: 'Run pass' }).click()
  const job = await (await started).json()
  for (let tries = 0; tries < 100; tries++) {
    const state = await (await page.request.get(`${config.url}/api/jobs/${job.job_id}`)).json()
    if (['succeeded', 'failed', 'cancelled'].includes(state.state)) {
      assert.equal(state.state, 'succeeded', JSON.stringify(state.error))
      break
    }
    if (tries === 99) assert.fail('Targeted pass did not finish')
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  await page.reload()
  await page.getByRole('button', { name: `Run again ${chunkId} P2` }).waitFor()
  await page.getByRole('button', { name: `${chunkId} P2: saved successful result; preview` }).click()
  await page.getByText('Semantic checks', { exact: true }).waitFor()
  await mkdir('/tmp/intelitex-translate-evidence', { recursive: true })
  await page.screenshot({ path: '/tmp/intelitex-translate-evidence/translate-dark-1440.png', animations: 'disabled' })
  const scrollStyle = await page.locator('.translate-preview-scroll').evaluate(element => getComputedStyle(element).scrollbarWidth)
  assert.equal(scrollStyle, 'thin')
  await page.getByRole('button', { name: 'M+H' }).click()
  await page.getByText('No sentences with this risk in this chunk.').waitFor()
  await page.getByRole('button', { name: 'All', exact: true }).click()
  await page.locator('.translate-preview-segment').first().waitFor()
  const untouchedChunk = pipeline.units[1].id
  await page.locator('.translate-chunk-choice').filter({ hasText: untouchedChunk }).click()
  await page.getByText('Source text · no saved passes yet.').waitFor()
  assert.equal(await page.locator('.translate-preview-head.source-only').evaluate(element => getComputedStyle(element).borderBottomWidth), '0px')
  assert.equal(await page.locator('.translate-preview-segment').count(), 0)
  await page.screenshot({ path: '/tmp/intelitex-translate-evidence/translate-source-only-dark-1440.png', animations: 'disabled' })
  await page.locator('.translate-chunk-choice').filter({ hasText: chunkId }).click()
  await page.getByRole('button', { name: `Run again ${chunkId} P2` }).click()
  const rerun = page.getByRole('dialog', { name: 'Run this pass again?' })
  await rerun.getByText('Earlier artifacts remain saved.', { exact: false }).waitFor()
  await rerun.getByRole('button', { name: 'Cancel' }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: 'Toggle theme' }).click()
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  await page.screenshot({ path: '/tmp/intelitex-translate-evidence/translate-light-390.png', fullPage: true, animations: 'disabled' })
  assert.deepEqual(errors, [])
  assert.deepEqual(failures, [])
})
