import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'

test('All shows nested groups, optional types, global sorting and nested source details', { timeout: 60000 }, async t => {
  const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'], { cwd: '..', stdio: ['pipe', 'pipe', 'pipe'] })
  let serverError = ''
  server.stderr.on('data', value => { serverError += value })
  t.after(async () => { server.stdin.end('quit\n'); await new Promise(resolve => { server.once('exit', resolve); setTimeout(() => { server.kill('SIGTERM'); resolve() }, 3000).unref() }) })
  const config = await new Promise((resolve, reject) => {
    createInterface({ input: server.stdout }).on('line', line => { try { const value = JSON.parse(line); if (value.url) resolve(value) } catch {} })
    server.once('exit', () => reject(new Error(serverError)))
  })
  for (const [name, title] of [['Hamilton/Salvation/01', 'First volume'], ['Hamilton/Salvation/02', 'Second volume'], ['Unknown group/Novel', 'Another novel'], ['Standalone', 'A standalone novel']]) {
    const destination = `${config.root}/sources/${name}`
    await cp(`${config.root}/sources/epub-source`, destination, { recursive: true })
    const opf = `${destination}/EPUB/package.opf`
    await writeFile(opf, (await readFile(opf, 'utf8')).replace('Relay Book', title))
  }
  await writeFile(`${config.root}/sources/Hamilton/library.yaml`, 'version: 1\nkind: author\nname: Peter F. Hamilton\n')
  await writeFile(`${config.root}/sources/Hamilton/Salvation/library.yaml`, 'version: 1\nkind: series\n')
  const browser = await chromium.launch()
  t.after(() => browser.close())
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`) })
  await page.goto(config.url + '/work')
  assert.equal(await page.title(), 'Intelitex')
  await page.getByRole('heading', { name: 'Library', exact: true }).scrollIntoViewIfNeeded()
  const author = page.locator('.book-card[data-library-group="Hamilton"]')
  await author.first().waitFor()
  assert.equal(await author.count(), 2)
  assert.ok((await author.first().locator('.library-group-path').textContent()).includes('Peter F. Hamilton'))
  assert.ok((await author.first().locator('.library-group-path').textContent()).includes('Salvation'))
  assert.deepEqual(await author.first().locator('.library-kind').allTextContents(), ['author', 'series'])
  assert.equal(await page.locator('[data-library-group="Unknown group"] .library-kind').count(), 0)
  assert.equal(await page.locator('[data-source-id="Hamilton"]').count(), 0)
  assert.equal(await page.locator('.book-card').count(), 6)
  await page.getByLabel('Sort Library').selectOption('title')
  await page.waitForFunction(() => document.querySelector('.book-card')?.getAttribute('data-source-id') === 'Standalone')
  await page.reload()
  await page.getByRole('heading', { name: 'Library', exact: true }).scrollIntoViewIfNeeded()
  assert.equal(await page.getByLabel('Sort Library').inputValue(), 'title')
  await page.locator('[data-source-id="Hamilton/Salvation/02"]').click()
  const dialog = page.getByRole('dialog')
  await dialog.getByText('Hamilton/Salvation/02', { exact: true }).waitFor()
  await dialog.getByRole('button', { name: 'Inspect source' }).click()
  await dialog.getByText('Source inspection', { exact: true }).waitFor()
  await dialog.locator('.source-inspect-facts').waitFor()
  await dialog.getByRole('button', { name: 'Close', exact: true }).click()
  await Promise.all([
    page.waitForResponse(response => response.url().includes('sort=language') && response.status() === 200),
    page.getByLabel('Sort Library').selectOption('language'),
  ])
  const output = '/tmp/intelitex-library-groups-evidence'
  await mkdir(output, { recursive: true })
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    await author.first().scrollIntoViewIfNeeded()
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'No horizontal overflow')
    const cards = await page.locator('.library-grid > .book-card').evaluateAll(nodes => nodes.map(node => {
      const rect = node.getBoundingClientRect()
      return { x: rect.x, y: rect.y, width: rect.width }
    }))
    assert.equal(cards.length, 6, 'All cards are direct grid children, without group wrappers')
    const columns = width === 1440 ? 7 : 2
    for (let i = 0; i < cards.length; i++) {
      assert.ok(Math.abs(cards[i].width - cards[0].width) < 1, 'Group cards retain the normal card width')
      assert.ok(Math.abs(cards[i].y - cards[Math.floor(i / columns) * columns].y) < 2, 'No forced row breaks between groups')
    }
    await page.screenshot({ path: `${output}/groups-${width}.png` })
  }
  assert.equal(await page.locator('vite-error-overlay').count(), 0)
  assert.deepEqual(errors, [])
})
