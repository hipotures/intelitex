import { testEvidence } from './paths.mjs'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'

test('Review exports a complete draft and imports validated changes through approval', { timeout: 90000 }, async t => {
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
  const { url } = await new Promise((resolve, reject) => {
    lines.on('line', line => { try { const value = JSON.parse(line); if (value.url) resolve(value) } catch {} })
    server.once('exit', () => reject(new Error(serverError)))
  })
  const browser = await chromium.launch()
  t.after(() => browser.close())
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
  page.setDefaultTimeout(15000)
  const errors = [], failures = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('requestfailed', request => {
    if (request.failure()?.errorText !== 'net::ERR_ABORTED') failures.push(`${request.failure()?.errorText} ${request.url()}`)
  })
  page.on('response', response => { if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`) })

  await page.goto(`${url}/work/workspaces/prepared`)
  await page.getByRole('button', { name: 'Run', exact: true }).click()
  await page.getByRole('button', { name: 'Open Review', exact: true }).click()
  await page.getByRole('heading', { name: 'Terminology Review' }).waitFor()
  assert.equal(await page.title(), 'Intelitex')
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  const download = await downloadPromise
  assert.ok(download.suggestedFilename().endsWith('-review.json'))
  const document = JSON.parse(await readFile(await download.path(), 'utf8'))
  assert.equal(document.format, 'intelitex-review')
  assert.equal(document.review.terms.length, 1)
  document.review.terms[0].candidates[0].text = 'Poprawiona nazwa'
  // Exercise requests above the ordinary 16 KiB API limit.
  document.review.terms[0].user_notes = 'AI review. '.repeat(2000)
  await page.getByRole('button', { name: 'Import', exact: true }).click()
  await page.getByLabel('Import Review JSON').setInputFiles({ name: 'updated-review.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) })
  const dialog = page.getByRole('dialog', { name: 'Import Review?' })
  await dialog.getByText('1 of 1 terms changed.', { exact: false }).waitFor()
  assert.equal(await dialog.locator('tbody tr').count(), 2)
  // Preview does not write; cancel also preserves original.
  const original = await (await page.request.get(`${url}/api/workspaces/prepared/review`)).json()
  assert.notEqual(original.terms[0].candidates[0].text, 'Poprawiona nazwa')
  await page.screenshot({ path: testEvidence('intelitex-review-import-desktop.png'), animations: 'disabled' })
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await page.getByLabel('Import Review JSON').setInputFiles({ name: 'updated-review.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) })
  await page.getByRole('button', { name: 'Import and replace Review', exact: true }).click()
  await page.getByRole('button', { name: 'Bulk accept remaining in this view (0)' }).waitFor()
  await page.getByText('Imported 1 changed terms out of 1.', { exact: false }).waitFor()
  const persisted = await (await page.request.get(`${url}/api/workspaces/prepared/review`)).json()
  assert.equal(persisted.terms[0].candidates[0].text, 'Poprawiona nazwa')
  assert.equal(persisted.terms[0].reviewed, true)
  assert.equal(persisted.terms[0].review_method, 'bulk')
  await page.getByRole('button', { name: 'Approve glossary', exact: true }).click()
  await page.getByRole('button', { name: 'Approve selected forms', exact: true }).click()
  await page.getByText('✓ Glossary approved', { exact: true }).waitFor()
  const approved = await (await page.request.get(`${url}/api/workspaces/prepared/review`)).json()
  assert.equal(approved.terms[0].candidates[0].text, 'Poprawiona nazwa')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: testEvidence('intelitex-review-import-mobile.png'), fullPage: true, animations: 'disabled' })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
  assert.equal(await page.locator('vite-error-overlay').count(), 0)
  assert.deepEqual(errors, [])
  assert.deepEqual(failures, [])
})
