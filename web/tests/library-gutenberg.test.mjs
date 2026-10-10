import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { cp, readFile, writeFile } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'

test('Settings controls a Library checkbox that lists only Project Gutenberg books', { timeout: 90000 }, async t => {
  const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'], { cwd: '..', stdio: ['pipe', 'pipe', 'pipe'] })
  let serverError = ''
  server.stderr.on('data', value => { serverError += value })
  t.after(async () => { server.stdin.end('quit\n'); await new Promise(resolve => { server.once('exit', resolve); setTimeout(() => { server.kill('SIGTERM'); resolve() }, 3000).unref() }) })
  const config = await new Promise((resolve, reject) => {
    createInterface({ input: server.stdout }).on('line', line => { try { const value = JSON.parse(line); if (value.url) resolve(value) } catch {} })
    server.once('exit', () => reject(new Error(serverError)))
  })
  // The marker is read from the OPF, so the file names are deliberately unrelated to Project Gutenberg.
  // Enough books of each kind that the Library is a long page, as in real use.
  const marker = '<dc:source>https://www.gutenberg.org/ebooks/35</dc:source>'
  for (let i = 1; i <= 14; i++) for (const [name, title, source] of [[`Book ${String(i).padStart(2, '0')} PG`, `Novel ${String(i).padStart(2, '0')} A`, marker], [`Book ${String(i).padStart(2, '0')} xx`, `Novel ${String(i).padStart(2, '0')} B`, '']]) {
    const destination = `${config.root}/sources/${name}`
    await cp(`${config.root}/sources/epub-source`, destination, { recursive: true })
    const opf = `${destination}/EPUB/package.opf`
    await writeFile(opf, (await readFile(opf, 'utf8')).replace('Relay Book', title).replace('</metadata>', `${source}</metadata>`))
  }
  const browser = await chromium.launch(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {})
  t.after(() => browser.close())
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`) })
  const filter = page.getByLabel('Show only Project Gutenberg books')
  await page.goto(config.url + '/work')
  await page.getByRole('heading', { name: 'Library', exact: true }).scrollIntoViewIfNeeded()
  await page.locator('.book-card').first().waitFor()
  const onlyGutenberg = () => page.waitForFunction(() => {
    const cards = [...document.querySelectorAll('.book-card')]
    return cards.length >= 12 && cards.every(card => card.getAttribute('data-source-id').endsWith(' PG'))
  })
  const hasOtherBooks = () => page.waitForSelector('.book-card[data-source-id$=" xx"]')
  await hasOtherBooks()
  assert.equal(await filter.count(), 0, 'The checkbox is hidden until Settings enables it')

  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await page.getByRole('tab', { name: 'Library' }).click()
  await page.getByRole('checkbox', { name: /Show the .Project Gutenberg. filter/ }).check()
  await page.locator('.modal-foot').getByRole('button', { name: 'Close', exact: true }).click()
  await filter.waitFor()
  const labels = await page.locator('.library-header-actions label').allTextContents()
  assert.deepEqual(labels.slice(0, 2), ['Project Gutenberg', 'Sort byAuthorTitleLanguage'], 'The checkbox precedes Sort by')

  // Put the checkbox mid-screen (clear of the sticky header), as when a reader has scrolled to the Library, then check it.
  // The list must be replaced in place; emptying it first shrinks the page and the browser clamps the scroll position.
  await page.setViewportSize({ width: 1440, height: 300 })
  await filter.evaluate(element => element.scrollIntoView({ block: 'center' }))
  const before = await page.evaluate(() => window.scrollY)
  assert.ok(before > 100, 'the Library must be scrolled into view for this check')
  await page.evaluate(() => { window.__lowest = Infinity; const tick = () => { window.__lowest = Math.min(window.__lowest, window.scrollY); requestAnimationFrame(tick) }; tick() })
  await Promise.all([page.waitForResponse(r => r.url().includes('gutenberg=true') && r.status() === 200), filter.check()])
  await onlyGutenberg()
  assert.ok(await page.evaluate(() => window.__lowest) >= before - 2, 'checking the filter must not send the reader back toward the top')
  await page.setViewportSize({ width: 1440, height: 1000 })
  assert.equal(await page.locator('.library-view-label').textContent(), 'Project Gutenberg')
  await page.reload()
  await page.getByRole('heading', { name: 'Library', exact: true }).scrollIntoViewIfNeeded()
  await filter.waitFor()
  assert.equal(await filter.isChecked(), true, 'The choice survives a reload')
  await onlyGutenberg()

  await filter.uncheck()
  await hasOtherBooks()
  await filter.check()
  await onlyGutenberg()
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await page.getByRole('tab', { name: 'Library' }).click()
  await page.getByRole('checkbox', { name: /Show the .Project Gutenberg. filter/ }).uncheck()
  await page.locator('.modal-foot').getByRole('button', { name: 'Close', exact: true }).click()
  await hasOtherBooks()
  assert.equal(await filter.count(), 0, 'Disabling the setting also disables the filter')
  assert.equal(await page.locator('vite-error-overlay').count(), 0)
  assert.deepEqual(errors, [])
})
