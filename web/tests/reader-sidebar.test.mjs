import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import test from 'node:test'
import { chromium } from 'playwright'

test('Reader sidebar keeps the first chapter clear of the Chapters/Library switch, also while scrolling', { timeout: 60000 }, async t => {
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
  const page = await browser.newPage({ viewport: { width: 1440, height: 700 }, colorScheme: 'dark' })
  await page.route('**/favicon.ico', route => route.fulfill({ status: 204 }))
  await page.goto(`${config.url}/reader/prepared`)
  await page.locator('.reader-chapter-list button').first().waitFor()
  const boxes = () => page.evaluate(() => {
    const top = selector => document.querySelector(selector).getBoundingClientRect()
    const books = document.querySelector('.reader-books')
    return { switchBottom: top('.reader-sidebar-switch').bottom, label: top('.reader-chapter-book'), firstTop: top('.reader-chapter-list button').top, scrollable: books.scrollHeight > books.clientHeight }
  })
  const rest = await boxes()
  assert.ok(rest.label.top >= rest.switchBottom - 1, 'the book title is below the switch, not under it')
  assert.ok(rest.firstTop >= rest.switchBottom - 1, 'the first chapter is not covered by the switch')
})
