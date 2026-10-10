import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import test from 'node:test'
import { chromium } from 'playwright'

test('Prepare lets the user enter a translated title that the Reader shows instead of the source title', { timeout: 90000 }, async t => {
  const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'], { cwd: '..', stdio: ['pipe', 'pipe', 'pipe'] })
  let serverError = ''
  server.stderr.on('data', value => { serverError += value })
  t.after(async () => { server.stdin.end('quit\n'); await new Promise(resolve => { server.once('exit', resolve); setTimeout(() => { server.kill('SIGTERM'); resolve() }, 3000).unref() }) })
  const config = await new Promise((resolve, reject) => {
    createInterface({ input: server.stdout }).on('line', line => { try { const value = JSON.parse(line); if (value.url) resolve(value) } catch {} })
    server.once('exit', () => reject(new Error(serverError)))
  })
  const browser = await chromium.launch(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {})
  t.after(() => browser.close())
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`) })

  await page.goto(`${config.url}/work/workspaces/prepared/prepare`)
  const card = page.locator('[data-ui-debug-id="PTT"]')
  await card.waitFor()
  const order = await page.evaluate(() => [...document.querySelectorAll('[data-ui-debug-id]')].map(node => node.getAttribute('data-ui-debug-id')))
  assert.ok(order.indexOf('PTT') >= 0 && order.indexOf('PTT') < order.indexOf('PSM'), 'the card comes before Source metadata')
  const input = card.getByLabel('Title')
  const save = card.getByRole('button', { name: 'Save title' })
  assert.equal(await save.isDisabled(), true, 'nothing to save yet')
  await input.fill('  Księga   Przekaźnika ')
  await save.click()
  await page.waitForFunction(() => document.querySelector('#translated-title')?.value === 'Księga Przekaźnika')
  assert.equal(await card.getByRole('button', { name: 'Clear' }).isVisible(), true)

  await page.goto(`${config.url}/reader/prepared`)
  await page.locator('.reader-chapter-book').waitFor()
  assert.equal((await page.locator('.reader-chapter-book').textContent()).trim(), 'Księga Przekaźnika')
  assert.ok((await page.locator('.reader-current strong').textContent()).startsWith('Księga Przekaźnika'))

  await page.goto(`${config.url}/work/workspaces/prepared/prepare`)
  await card.getByRole('button', { name: 'Clear' }).click()
  await card.getByRole('button', { name: 'Clear' }).waitFor({ state: 'detached' })
  await page.goto(`${config.url}/reader/prepared`)
  await page.locator('.reader-chapter-book').waitFor()
  assert.equal((await page.locator('.reader-chapter-book').textContent()).trim(), 'Relay Book')
  assert.deepEqual(errors.filter(e => !e.includes('favicon')), [])
})
