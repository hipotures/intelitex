import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { mkdir } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'

test('bulk acceptance stays distinct from individual review through glossary approval', { timeout: 90000 }, async t => {
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
  await page.getByRole('button', { name: 'Bulk accept remaining in this view (1)' }).click()
  const bulkDialog = page.getByRole('dialog', { name: 'Bulk accept remaining terms?' })
  await bulkDialog.getByText('without individual review?', { exact: false }).waitFor()
  await bulkDialog.getByRole('button', { name: 'Bulk accept 1 term' }).click()
  await page.locator('.review-page-meta').getByText('0/1 individually reviewed', { exact: false }).waitFor()
  await page.locator('.review-term-row').getByText('✓ bulk accepted').waitFor()

  await page.getByRole('button', { name: 'Approve glossary', exact: true }).click()
  const approvalDialog = page.getByRole('dialog', { name: 'Approve glossary?' })
  await approvalDialog.getByText('0/1 individually reviewed · 1 bulk accepted', { exact: false }).waitFor()
  await mkdir('/tmp/intelitex-review-methods-evidence', { recursive: true })
  await page.screenshot({ path: '/tmp/intelitex-review-methods-evidence/bulk-approval-dark-1440.png', animations: 'disabled' })
  await approvalDialog.getByRole('button', { name: 'Approve selected forms' }).click()
  await page.getByText('✓ Glossary approved', { exact: true }).waitFor()
  await page.locator('.review-term-row').getByText('✓ bulk accepted').waitFor()
  const persisted = await (await page.request.get(`${url}/api/workspaces/prepared/review`)).json()
  assert.equal(persisted.terms[0].review_method, 'bulk')
  assert.equal(persisted.terms[0].reviewed, true)

  await page.getByRole('button', { name: 'Bulk accepted', exact: true }).click()
  assert.equal(await page.locator('.review-term-row').count(), 1)
  await page.getByRole('button', { name: 'Individually reviewed', exact: true }).click()
  assert.equal(await page.locator('.review-term-row').count(), 0)
  await page.getByRole('button', { name: 'All', exact: true }).first().click()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: 'Toggle theme' }).click()
  await page.screenshot({ path: '/tmp/intelitex-review-methods-evidence/bulk-light-390.png', fullPage: true, animations: 'disabled' })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
  await page.getByRole('button', { name: 'Review individually & next', exact: true }).click()
  await page.locator('.review-page-meta').getByText('1/1 individually reviewed · 0 bulk accepted', { exact: false }).waitFor()
  await page.locator('.review-term-row').getByText('✓ individually reviewed').waitFor()
  const manuallyReviewed = await (await page.request.get(`${url}/api/workspaces/prepared/review`)).json()
  assert.equal(manuallyReviewed.terms[0].review_method, 'individual')
  assert.deepEqual(errors, [])
  assert.deepEqual(failures, [])
})
