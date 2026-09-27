import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import test from 'node:test'
import { chromium } from 'playwright'

test('Prepare edits shared metadata, preserves original and restores it', { timeout: 60000 }, async t => {
  const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'], { cwd: '..', stdio: ['pipe', 'pipe', 'pipe'] })
  let stderr = ''
  server.stderr.on('data', value => { stderr += value })
  t.after(async () => { server.stdin.end('quit\n'); await new Promise(resolve => { server.once('exit', resolve); setTimeout(() => { server.kill('SIGTERM'); resolve() }, 3000).unref() }) })
  const config = await new Promise((resolve, reject) => {
    createInterface({ input: server.stdout }).on('line', line => { try { const value = JSON.parse(line); if (value.url) resolve(value) } catch {} })
    server.once('exit', () => reject(new Error(stderr)))
  })
  const browser = await chromium.launch()
  t.after(() => browser.close())
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const original = await (await fetch(config.url + '/api/workspaces/prepared/metadata')).json()
  await page.goto(config.url + '/work/workspaces/prepared/prepare')
  const edit = page.getByRole('button', { name: 'Edit metadata / original' })
  await edit.click()
  const dialog = page.getByRole('dialog', { name: 'Book metadata' })
  await dialog.getByLabel('Title', { exact: true }).fill('My corrected book title')
  await dialog.getByLabel('Authors (one per line)').fill('Author One\nAuthor Two')
  await dialog.getByLabel('Source language', { exact: true }).fill('eng-US')
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    assert.ok(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1))
    await page.screenshot({ path: `/tmp/intelitex-metadata-${width}.png` })
  }
  await dialog.getByRole('button', { name: 'Save metadata' }).click()
  await dialog.waitFor({ state: 'detached' })
  const panel = page.locator('.phase-detail-card').filter({ has: page.getByRole('heading', { name: 'Source metadata' }) })
  await panel.getByText('My corrected book title', { exact: true }).waitFor()
  await panel.getByText('Author One, Author Two', { exact: true }).waitFor()
  assert.equal(await panel.getByText('Import folder', { exact: true }).count(), 0)
  await page.reload()
  await edit.click()
  await dialog.getByText('Original: ' + original.original.title, { exact: true }).waitFor()
  assert.equal(await dialog.getByLabel('Title', { exact: true }).inputValue(), 'My corrected book title')
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.goto(config.url + '/work')
  await page.getByRole('heading', { name: 'Library', exact: true }).scrollIntoViewIfNeeded()
  await page.locator('[data-source-id="epub-source"]').getByText('My corrected book title', { exact: true }).waitFor()
  await page.goto(config.url + '/work/workspaces/prepared/prepare')
  await edit.click()
  await dialog.getByRole('button', { name: 'Restore original', exact: true }).first().waitFor()
  assert.equal(await dialog.getByRole('button', { name: 'Restore original', exact: true }).count(), 3)
  for (const button of await dialog.getByRole('button', { name: 'Restore original', exact: true }).all()) await button.click()
  await dialog.getByRole('button', { name: 'Save metadata' }).click()
  await dialog.waitFor({ state: 'detached' })
  await panel.getByText(original.original.title, { exact: true }).waitFor()
  const restored = await (await fetch(config.url + '/api/workspaces/prepared/metadata')).json()
  assert.deepEqual(restored.original, original.original)
  assert.deepEqual(restored.corrections, {})
  assert.equal(restored.book_id, original.book_id)
  await edit.click()
  await dialog.getByLabel('Title', { exact: true }).fill('Unsaved local edit')
  const concurrent = await fetch(config.url + '/api/workspaces/prepared/metadata', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ revision: restored.revision, corrections: { title: 'Saved in another editor' } }),
  })
  assert.equal(concurrent.status, 200)
  await dialog.getByRole('button', { name: 'Save metadata' }).click()
  await dialog.getByRole('alert').waitFor()
  assert.equal(await dialog.getByLabel('Title', { exact: true }).inputValue(), 'Unsaved local edit')
  await dialog.getByRole('button', { name: 'Reload saved values' }).click()
  await page.waitForFunction(() => document.querySelector('#book-metadata-title')?.value === 'Saved in another editor')
  await dialog.getByRole('button', { name: 'Restore original', exact: true }).first().click()
  await dialog.getByRole('button', { name: 'Save metadata' }).click()
  await dialog.waitFor({ state: 'detached' })
  assert.deepEqual(errors, [])
})
