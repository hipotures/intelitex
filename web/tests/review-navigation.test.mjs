import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { mkdir } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'

test('Review Next saves once, advances without waiting for workspace refresh, and protects failed drafts', { timeout: 90000 }, async t => {
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
  page.setDefaultTimeout(10000)
  const errors = [], failures = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error' && !message.text().includes('status of 409 (Conflict)')) errors.push(message.text()) })
  page.on('response', response => {
    if (response.status() >= 400 && response.status() !== 409) failures.push(`${response.status()} ${response.url()}`)
  })
  await page.goto(`${config.url}/work/workspaces/prepared`)
  await page.getByRole('button', { name: 'Run', exact: true }).click()
  await page.getByRole('button', { name: 'Open Review', exact: true }).waitFor()

  let firstId = '', firstPatches = 0, secondPatches = 0, heldPipelineReads = 0, fakeSecond
  const bulkBodies = []
  let holdPipeline = false
  let releasePipeline
  const pipelineGate = new Promise(resolve => { releasePipeline = resolve })
  await page.route('**/api/workspaces/prepared/pipeline', async route => {
    if (holdPipeline) { heldPipelineReads++; await pipelineGate }
    await route.continue()
  })
  await page.route('**/api/workspaces/prepared/review', async route => {
    const response = await route.fetch()
    const review = await response.json()
    firstId = review.terms[0].id
    fakeSecond = { ...review.terms[0], id: 'T_TEST_SECOND', source: 'Second term', custom: '', reviewed: false }
    await route.fulfill({ response, json: { ...review, terms: [...review.terms,
      fakeSecond, { ...review.terms[0], id: 'T_TEST_THIRD', source: 'Person term', category: 'People', custom: '', reviewed: false }] } })
  })
  await page.route('**/api/workspaces/prepared/review/terms/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/T_TEST_SECOND/evidence')) {
      await route.fulfill({ json: { term_id: 'T_TEST_SECOND', entries: [], warnings: [], choice_pending_approval: false } })
      return
    }
    if (path.endsWith('/T_TEST_SECOND') && route.request().method() === 'PATCH') {
      secondPatches++
      await route.fulfill({ status: 409, json: { error: { code: 'revision_conflict', message: 'Revision changed.' } } })
      return
    }
    if (route.request().method() === 'PATCH') firstPatches++
    await route.continue()
  })
  await page.route('**/api/workspaces/prepared/review/bulk-review', async route => {
    bulkBodies.push(JSON.parse(route.request().postData()))
    await route.fulfill({ json: { revision: 'bulk-test-revision', terms: [{ ...fakeSecond, reviewed: true }],
      changed_count: 1, summary: { total: 3, reviewed: 2, unreviewed: 1, uncertain: 0,
        confirmed: false, categories: { technology: 2, People: 1 } } } })
  })
  await page.getByRole('button', { name: 'Open Review', exact: true }).click()
  await page.getByRole('heading', { name: 'Terminology Review' }).waitFor()
  assert.equal(await page.title(), 'Intelitex')
  assert.equal(await page.locator('.review-term-row').count(), 3)
  const custom = page.getByRole('textbox', { name: 'Custom Polish form' })
  await custom.fill('Relay custom')
  holdPipeline = true
  try {
    await page.locator('#reviewOnlyNextBtn').click()
    await page.locator('.review-detail-heading h2').getByText('Second term').waitFor({ timeout: 2500 })
    assert.equal(await page.getByRole('dialog', { name: 'Keep your unsaved form?' }).count(), 0)
    assert.equal(firstPatches, 1, 'blur and Next issue only one PATCH')
    assert.ok(heldPipelineReads > 0, 'workspace refresh started in the background')
  } finally { releasePipeline() }
  const saved = await page.request.get(`${config.url}/api/workspaces/prepared/review`)
  const savedReview = await saved.json()
  assert.equal(savedReview.terms.find(term => term.id === firstId).custom, 'Relay custom')
  await mkdir('/tmp/intelitex-review-navigation-evidence', { recursive: true })
  await page.screenshot({ path: '/tmp/intelitex-review-navigation-evidence/next-dark-1440.png', animations: 'disabled' })

  await page.locator('#reviewPrevBtn').click()
  await page.locator('.review-detail-heading h2').getByText('Relay', { exact: true }).waitFor()
  await page.locator('[data-ui-debug-id="RVC"] button').filter({ hasText: 'technology' }).click()
  await page.getByRole('button', { name: 'Unreviewed', exact: true }).click()
  await custom.fill('Relay custom revised')
  await page.locator('#reviewNextBtn').getByText('Review & next').click()
  await page.locator('.review-detail-heading h2').getByText('Second term').waitFor()
  assert.equal(firstPatches, 3, 'Review & next saves the changed form and then marks the term reviewed')
  assert.equal(await page.locator('.review-term-row').count(), 1, 'reviewed term leaves the Unreviewed filter')
  const afterReview = await page.request.get(`${config.url}/api/workspaces/prepared/review`)
  assert.equal((await afterReview.json()).terms.find(term => term.id === firstId).reviewed, true)
  assert.equal((await afterReview.json()).terms.find(term => term.id === firstId).custom, 'Relay custom revised')

  const latestReview = await afterReview.json()
  const remote = await page.request.patch(`${config.url}/api/workspaces/prepared/review/terms/${firstId}`,
    { data: { revision: latestReview._revision, user_notes: 'Concurrent editor' } })
  assert.equal(remote.status(), 200)
  await custom.fill('Unsent choice')
  await page.locator('#reviewNextBtn').click()
  await page.getByText('Revision changed.', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Reapply my form' }).waitFor()
  assert.equal(secondPatches, 1)
  assert.equal(await custom.inputValue(), 'Unsent choice')
  assert.equal(await page.locator('.review-detail-heading h2').textContent(), 'Second term')
  assert.equal(await page.getByRole('dialog', { name: 'Keep your unsaved form?' }).count(), 0)
  await page.getByRole('link', { name: 'Workspace', exact: true }).click()
  await page.getByRole('dialog', { name: 'Keep your unsaved form?' }).waitFor()
  await page.getByRole('button', { name: 'Stay', exact: true }).click()
  assert.equal(await custom.inputValue(), 'Unsent choice')
  await page.getByRole('button', { name: 'Discard my form' }).click()
  await page.getByRole('button', { name: 'Review remaining in this view (1)' }).click()
  await page.getByRole('dialog', { name: 'Review remaining terms?' }).waitFor()
  await page.getByRole('dialog', { name: 'Review remaining terms?' }).getByText('Unreviewed / technology').waitFor()
  await page.screenshot({ path: '/tmp/intelitex-review-navigation-evidence/bulk-confirm-dark-1440.png', animations: 'disabled' })
  await page.getByRole('dialog', { name: 'Review remaining terms?' }).getByRole('button', { name: 'Review 1 term' }).click()
  await page.getByText('1 term reviewed. Current choices and notes kept.').waitFor()
  assert.deepEqual(bulkBodies.map(body => body.term_ids), [['T_TEST_SECOND']])
  assert.equal(await page.getByRole('button', { name: 'Review remaining in this view (0)' }).isDisabled(), true)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: 'Toggle theme' }).click()
  await page.screenshot({ path: '/tmp/intelitex-review-navigation-evidence/draft-light-390.png', fullPage: true, animations: 'disabled' })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
  assert.equal(await page.locator('.review-detail-footer').evaluate(footer =>
    [...footer.querySelectorAll('button')].every(button => button.getBoundingClientRect().right <= footer.getBoundingClientRect().right - 10)), true,
  'all Review navigation buttons fit in the mobile footer')
  assert.deepEqual(errors, [])
  assert.deepEqual(failures, [])
})
