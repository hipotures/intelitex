import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { createInterface } from 'node:readline'
import { cp, mkdir, writeFile } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'

test('Library refresh discovers new sources locally and retains its last good contents on failure', { timeout: 90000 }, async t => {
  const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'], { cwd: '..', stdio: ['pipe', 'pipe', 'pipe'] })
  let serverError = ''
  server.stderr.on('data', value => serverError += value)
  t.after(async () => { server.stdin.end('quit\n'); await new Promise(resolve => { server.once('exit', resolve); setTimeout(() => { server.kill('SIGTERM'); resolve() }, 3000).unref() }) })
  const config = await new Promise((resolve, reject) => {
    createInterface({ input: server.stdout }).on('line', line => { try { const value = JSON.parse(line); if (value.url) resolve(value) } catch {} })
    server.once('exit', () => reject(new Error(serverError)))
  })
  const browser = await chromium.launch()
  t.after(() => browser.close())
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' })
  const consoleErrors = [], pageErrors = [], failedResponses = []
  let libraryRequests = 0
  page.on('request', request => { if (new URL(request.url()).pathname === '/api/library') libraryRequests++ })
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('response', response => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${new URL(response.url()).pathname}`) })

  await page.goto(config.url + '/work')
  assert.equal(new URL(page.url()).pathname, '/work')
  assert.equal(await page.title(), 'Intelitex')
  await page.locator('[data-source-id="second-book"]').waitFor()
  await page.locator('.workspace-row').waitFor()
  assert.equal(await page.locator('.workspace-row').count(), 1)
  assert.equal(await page.locator('[data-source-id="new-book"]').count(), 0)
  const initialRequests = libraryRequests
  await page.locator('[data-source-id="second-book"]').hover()
  await page.waitForTimeout(16000)
  assert.equal(libraryRequests, initialRequests, 'hover and realtime polling must not rescan Library')
  await cp(`${config.root}/sources/epub-source`, `${config.root}/sources/new-book`, { recursive: true })
  const packed = spawnSync('uv', ['run', 'python', '-c', `from pathlib import Path
from zipfile import ZipFile
from sys import argv
root=Path(argv[1])/'sources'
source=root/'epub-source'
with ZipFile(root/'Packed Book.epub','w') as archive:
    for path in source.rglob('*'):
        if path.is_file(): archive.write(path,path.relative_to(source).as_posix())`, config.root], { cwd: '..', encoding: 'utf8' })
  assert.equal(packed.status, 0, packed.stderr)
  await page.getByRole('button', { name: 'Refresh Library' }).scrollIntoViewIfNeeded()
  const before = await page.evaluate(() => scrollY)
  const cardTop = await page.locator('[data-source-id="second-book"]').evaluate(node => node.getBoundingClientRect().top)
  await page.route('**/api/library?*', async route => { await new Promise(resolve => setTimeout(resolve, 1000)); await route.continue() })
  await page.getByRole('button', { name: 'Refresh Library' }).click()
  await page.locator('.toast[role="status"]').filter({ hasText: 'Refreshing Library…' }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Refresh Library' }).isDisabled(), true)
  assert.ok(Math.abs(await page.locator('[data-source-id="second-book"]').evaluate(node => node.getBoundingClientRect().top) - cardTop) < 1, 'refresh status must not shift the card grid')
  assert.equal(await page.locator('[data-source-id="second-book"]').count(), 1, 'existing source stays visible while loading')
  assert.equal(await page.locator('.workspace-row').count(), 1, 'active workspaces stay visible')
  await page.locator('[data-source-id="new-book"]').waitFor()
  await page.locator('[data-source-id="Packed Book.epub"]').waitFor()
  assert.equal(libraryRequests, initialRequests + 1, 'Refresh makes one Library request')
  await page.unroute('**/api/library?*')
  assert.equal(new URL(page.url()).pathname, '/work')
  assert.equal(await page.evaluate(() => scrollY), before)
  assert.equal(await page.getByRole('button', { name: 'Refresh Library' }).isEnabled(), true)
  assert.equal(await page.getByText('Source directory: Configured').count(), 0)
  assert.equal(await page.getByText('— words').count(), 0)
  const beforeOpen = libraryRequests
  await page.locator('[data-source-id="new-book"]').click()
  await page.waitForURL(/\/work\/workspaces\//)
  await page.goBack()
  await page.locator('[data-source-id="new-book"]').waitFor()
  assert.equal(libraryRequests, beforeOpen, 'opening a source and returning must not rediscover Library')

  await page.route('**/api/library?*', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'temporary_failure', message: 'Library temporarily unavailable.' } }) }))
  await page.getByRole('button', { name: 'Refresh Library' }).click()
  await page.getByRole('alert').filter({ hasText: 'Library temporarily unavailable.' }).waitFor()
  assert.equal(await page.locator('[data-source-id="new-book"]').count(), 1, 'last successful source list survives failure')
  assert.equal(await page.locator('.workspace-row').count(), 2, 'opening the new source leaves its draft workspace visible')
  await page.unroute('**/api/library?*')
  await page.getByRole('button', { name: 'Retry Library refresh' }).click()
  await page.getByRole('alert').filter({ hasText: 'Library temporarily unavailable.' }).waitFor({ state: 'detached' })
  assert.equal(await page.locator('[data-source-id="new-book"]').count(), 1)
  assert.equal(new URL(page.url()).pathname, '/work')

  for (let index = 0; index < 25; index++) {
    const folder = `${config.root}/sources/z-${String(index).padStart(2, '0')}`
    await mkdir(folder)
    await writeFile(`${folder}/chapter.html`, '<p>Offline catalog fixture.</p>')
  }
  await page.getByRole('button', { name: 'Refresh Library' }).click()
  await page.locator('[data-source-id="z-07"]').waitFor()
  assert.equal(await page.locator('[data-source-id]').count(), 12, 'first page is bounded')
  assert.equal(await page.locator('[data-source-id="z-24"]').count(), 0, 'later books are not fetched early')
  for (let index = 0; index < 3 && await page.locator('[data-source-id="z-24"]').count() === 0; index++) {
    const beforePage = await page.locator('[data-source-id]').count()
    await page.locator('.library-load-more').scrollIntoViewIfNeeded()
    await page.waitForFunction(previous => document.querySelectorAll('[data-source-id]').length > previous, beforePage)
  }
  await page.locator('[data-source-id="z-24"]').waitFor()
  assert.equal(await page.locator('[data-source-id]').count(), 29)

  const beforePackedOpen = libraryRequests
  await page.locator('[data-source-id="Packed Book.epub"]').click()
  await page.getByRole('heading', { name: 'Prepare this workspace' }).waitFor()
  await page.getByRole('button', { name: 'Prepare', exact: true }).first().click()
  await page.locator('.sections-table').waitFor({ state: 'visible' })
  await page.goBack()
  await page.locator('[data-source-id="Packed Book.epub"]').waitFor()
  assert.equal(libraryRequests, beforePackedOpen, 'preparing a packed EPUB must not rescan Library')

  await mkdir('/tmp/intelitex-browser-evidence', { recursive: true })
  await page.screenshot({ path: '/tmp/intelitex-browser-evidence/library-refresh-dark-390.png', fullPage: false, animations: 'disabled' })
  await page.getByRole('button', { name: 'Toggle theme' }).click()
  await page.screenshot({ path: '/tmp/intelitex-browser-evidence/library-refresh-light-390.png', fullPage: false, animations: 'disabled' })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
  assert.deepEqual(pageErrors, [])
  assert.deepEqual(failedResponses, ['503 /api/library'])
  assert.ok(consoleErrors.every(message => message.includes('503')), `unexpected console errors: ${consoleErrors.join('; ')}`)
})
