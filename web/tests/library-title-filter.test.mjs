import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { cp, readFile, writeFile } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'
import { testEvidence } from './paths.mjs'

test('Library searches all titles from two characters and clearing restores the list', { timeout: 90000 }, async t => {
  const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'], { cwd: '..', stdio: ['pipe', 'pipe', 'pipe'] })
  let stderr = ''
  server.stderr.on('data', chunk => stderr += chunk)
  t.after(async () => {
    server.stdin.end('quit\n')
    await new Promise(resolve => { server.once('exit', resolve); setTimeout(() => { server.kill('SIGTERM'); resolve() }, 3000).unref() })
  })
  const config = await new Promise((resolve, reject) => {
    createInterface({ input: server.stdout }).on('line', line => { try { const value = JSON.parse(line); if (value.url) resolve(value) } catch {} })
    server.once('exit', () => reject(new Error(stderr)))
  })
  for (let i = 0; i < 30; i++) {
    const path = `${config.root}/sources/book-${String(i).padStart(2, '0')}`
    await cp(`${config.root}/sources/epub-source`, path, { recursive: true })
    const opf = `${path}/EPUB/package.opf`
    await writeFile(opf, (await readFile(opf, 'utf8')).replace('Relay Book', i === 29 ? 'Nether Vortex' : `Novel ${i}`))
  }
  const browser = await chromium.launch()
  t.after(() => browser.close())
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
  const errors = [], requests = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('request', request => { if (new URL(request.url()).pathname === '/api/library') requests.push(request.url()) })
  await page.goto(config.url + '/work')
  assert.equal(await page.title(), 'Intelitex')
  const input = page.getByRole('searchbox', { name: 'Filter Library by title' })
  await input.scrollIntoViewIfNeeded()
  await page.locator('.book-card').first().waitFor()
  assert.equal(await page.locator('[data-source-id="book-29"]').count(), 0, 'matching book starts beyond the loaded page')
  await input.fill('v')
  await page.waitForTimeout(400)
  assert.ok(requests.every(url => !new URL(url).searchParams.has('title')), 'one character never activates filtering')
  await input.fill('vO')
  await page.locator('[data-source-id="book-29"]').waitFor()
  assert.equal(await page.locator('.book-card').count(), 1)
  await page.getByRole('button', { name: 'Refresh Library' }).click()
  await page.getByRole('status').filter({ hasText: 'Library refreshed.' }).waitFor()
  assert.equal(await page.locator('.book-card').count(), 1)
  await page.screenshot({ path: testEvidence('library-title-filter-desktop.png') })
  await page.setViewportSize({ width: 390, height: 844 })
  await input.scrollIntoViewIfNeeded()
  assert.ok(await input.isVisible())
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  await page.screenshot({ path: testEvidence('library-title-filter-mobile.png') })
  await input.fill('no-matching-title')
  await page.getByText('No titles match this search', { exact: true }).waitFor()
  await input.fill('')
  await page.locator('[data-source-id="second-book"]').waitFor()
  assert.ok(await page.locator('.book-card').count() > 1)
  assert.deepEqual(errors, [])
})
