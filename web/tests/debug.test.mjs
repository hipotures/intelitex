import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { mkdir } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'

const evidence = '/tmp/intelitex-debug-evidence'
const regions = ['HDR', 'WRK', 'WHT', 'ACT', 'WLS', 'WRC', 'LIB', 'BKC']
const rects = page => page.evaluate(ids => Object.fromEntries(ids.map(id => {
  const element = document.querySelector(`[data-ui-debug-id="${id}"]`)
  const { x, y, width, height } = element.getBoundingClientRect()
  return [id, { x, y, width, height }]
})), regions)

test('Debug badges toggle and persist without moving Work regions or changing API behavior', { timeout: 60000 }, async t => {
  await mkdir(evidence, { recursive: true })
  const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'], { cwd: '..', stdio: ['pipe', 'pipe', 'pipe'] })
  let serverError = ''
  server.stderr.on('data', value => serverError += value)
  t.after(async () => { server.stdin.end('quit\n'); await new Promise(resolve => { server.once('exit', resolve); setTimeout(() => { server.kill('SIGTERM'); resolve() }, 3000).unref() }) })
  const lines = createInterface({ input: server.stdout })
  const config = await new Promise((resolve, reject) => { lines.on('line', line => { try { const value = JSON.parse(line); if (value.url) resolve(value) } catch {} }); server.once('exit', () => reject(new Error(serverError))) })
  const browser = await chromium.launch()
  t.after(() => browser.close())
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
  const page = await context.newPage()
  const consoleErrors = [], pageErrors = [], failedResponses = [], failedRequests = [], mutations = []
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('response', response => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`) })
  page.on('requestfailed', request => failedRequests.push(request.url()))
  page.on('request', request => { if (!['GET', 'HEAD'].includes(request.method())) mutations.push(`${request.method()} ${request.url()}`) })

  await page.goto(config.url + '/work')
  await page.locator('[data-ui-debug-id="BKC"]').first().waitFor()
  await page.getByRole('status').filter({ hasText: 'Live' }).waitFor()
  assert.equal(await page.locator('html').getAttribute('data-ui-debug'), 'off')
  assert.equal(await page.locator('[data-ui-debug-id="BKC"]').first().getAttribute('data-entity-id'),
    await page.locator('[data-ui-debug-id="BKC"]').first().getAttribute('data-source-id'))
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    await page.waitForFunction(() => !document.querySelector('.workspace-state')?.textContent?.includes('Loading…'))
    await page.evaluate(() => document.fonts.ready)
    // v33 animates the workspace row gap for 140 ms after a viewport change.
    await page.waitForTimeout(200)
    const before = await rects(page)
    await page.getByRole('button', { name: 'Settings', exact: true }).click()
    await page.getByRole('tab', { name: 'Debug' }).click()
    const toggle = page.getByRole('checkbox', { name: 'Show panel identifiers' })
    assert.equal(await toggle.isChecked(), false)
    await toggle.check()
    assert.equal(await page.locator('html').getAttribute('data-ui-debug'), 'on')
    assert.equal(await page.locator('[data-ui-debug-id="SET"]').count(), 1)
    assert.equal(await page.locator('[data-ui-debug-id="SPD"]').count(), 1)
    assert.equal(await page.locator('[data-ui-debug-id="SET"]').evaluate(element => getComputedStyle(element, '::after').content), '"SET"')
    assert.equal(await page.locator('[data-ui-debug-id="ACT"]').evaluate(element => getComputedStyle(element, '::after').content), '"ACT"')
    await page.screenshot({ path: `${evidence}/settings-debug-dark-${width}.png`, fullPage: true, animations: 'disabled' })
    await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).last().click()
    const on = await rects(page)
    assert.deepEqual(on, before, `${width}px layout moved when Debug was enabled`)
    await page.screenshot({ path: `${evidence}/work-debug-dark-${width}.png`, fullPage: true, animations: 'disabled' })
    await page.reload()
    await page.locator('[data-ui-debug-id="BKC"]').first().waitFor()
    assert.equal(await page.locator('html').getAttribute('data-ui-debug'), 'on', 'Debug preference survives reload')
    await page.getByRole('button', { name: 'Settings', exact: true }).click()
    await page.getByRole('tab', { name: 'Debug' }).click()
    await page.getByRole('checkbox', { name: 'Show panel identifiers' }).uncheck()
    await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).last().click()
    assert.deepEqual(await rects(page), before, `${width}px layout moved when Debug was disabled`)
    assert.notEqual(await page.locator('[data-ui-debug-id="ACT"]').evaluate(element => getComputedStyle(element, '::after').content), '"ACT"')
  }
  await page.getByRole('button', { name: 'Toggle theme' }).click()
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await page.getByRole('tab', { name: 'Debug' }).click()
  await page.getByRole('checkbox', { name: 'Show panel identifiers' }).check()
  await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).last().click()
  await page.screenshot({ path: `${evidence}/work-debug-light-390.png`, fullPage: true, animations: 'disabled' })
  assert.equal(await page.locator('[data-ui-debug-id="LIB"]').evaluate(element => getComputedStyle(element, '::after').content), '"LIB"')
  await page.goto(config.url + '/work/workspaces/prepared')
  await page.locator('[data-ui-debug-id="SCT"]').waitFor()
  for (const id of ['WSP', 'WHH', 'PHR', 'STF', 'SCT', 'PMA', 'LVA']) assert.equal(await page.locator(`[data-ui-debug-id="${id}"]`).count(), 1)
  const workspaceIdentity = page.locator('.workspace-debug-identity')
  assert.equal(await page.locator('[data-ui-debug-id="WSP"]').getAttribute('data-entity-id'), 'prepared')
  assert.equal(await workspaceIdentity.isVisible(), true)
  assert.equal((await workspaceIdentity.textContent()).trim(), 'Workspace ID: prepared')
  assert.equal(await workspaceIdentity.evaluate(element => {
    const selection = window.getSelection(), range = document.createRange()
    range.selectNodeContents(element); selection.removeAllRanges(); selection.addRange(range)
    const value = selection.toString(); selection.removeAllRanges()
    return value.includes('prepared')
  }), true, 'workspace identity is selectable text, not a tooltip')
  await page.screenshot({ path: `${evidence}/workspace-debug-light-390.png`, fullPage: true, animations: 'disabled' })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.getByRole('button', { name: 'Toggle theme' }).click()
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
  await page.screenshot({ path: `${evidence}/workspace-debug-dark-1440.png`, fullPage: true, animations: 'disabled' })
  await page.locator('[data-ui-debug-id="PMA"] > button').click()
  for (const id of ['MPA', 'MPB', 'MPC', 'MPD', 'MPE']) assert.equal(await page.locator(`[data-ui-debug-id="${id}"]`).count(), 1)
  await page.locator('.sections-table .section-open').first().click()
  await page.locator('[data-ui-debug-id="PVD"]').waitFor()
  for (const id of ['SMG', 'SMA', 'SMB', 'SMC', 'SMD', 'SME']) assert.equal(await page.locator(`[data-ui-debug-id="${id}"]`).count(), 1)
    await page.getByRole('button', { name: 'Close', exact: true }).click()
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await page.getByRole('tab', { name: 'Debug' }).click()
  await page.getByRole('checkbox', { name: 'Show panel identifiers' }).uncheck()
  await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).last().click()
  assert.equal(await workspaceIdentity.isHidden(), true)
  assert.deepEqual({ consoleErrors, pageErrors, failedResponses, failedRequests, mutations },
    { consoleErrors: [], pageErrors: [], failedResponses: [], failedRequests: [], mutations: [] })
})
