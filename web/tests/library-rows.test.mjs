import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { extname, resolve } from 'node:path'
import test from 'node:test'
import { chromium } from 'playwright'

const dist = resolve('dist')
const sources = Array.from({ length: 60 }, (_, index) => ({
  source_id: `book-${String(index).padStart(2, '0')}`,
  title: `Book ${index + 1}`,
  creators: ['Test Author'], language: 'en', word_count: null, workspace_id: null,
}))

test('Library pages complete visible grid rows without fetching the whole catalog', { timeout: 30000 }, async () => {
  const browser = await chromium.launch()
  try {
    for (const [width, columns, firstLimit] of [[1440, 7, 14], [1150, 5, 15], [390, 2, 12]]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 }, colorScheme: 'dark' })
      const requests = [], errors = []
      page.on('pageerror', error => errors.push(error.message))
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
      page.on('requestfailed', request => errors.push(`Failed request: ${request.url()}`))
      await page.addInitScript(() => {
        window.EventSource = class {
          static OPEN = 1
          readyState = 1
          addEventListener(type, callback) {
            if (type === 'snapshot') queueMicrotask(() => callback({ data: '{"jobs":[],"cursor":0}' }))
          }
          close() {}
        }
      })
      await page.route('http://intelitex.test/**', async route => {
        const url = new URL(route.request().url())
        let body
        if (url.pathname === '/api/capabilities') body = { scope_id: 'offline-library', import_enabled: true, review: true, reader: true, sse: true, multi_workspace: true, drafts: true, archive: true, section_configuration: true, diagnostics: true }
        else if (url.pathname === '/api/workspaces') body = { workspaces: [] }
        else if (url.pathname === '/api/jobs') body = { jobs: [], cursor: 0 }
        else if (url.pathname === '/api/library') {
          const limit = Number(url.searchParams.get('limit'))
          const after = url.searchParams.get('after')
          const start = after ? sources.findIndex(source => source.source_id === after) + 1 : 0
          requests.push(limit)
          const pageSources = sources.slice(start, start + limit)
          body = { configured: true, sources: pageSources, next_cursor: start + limit < sources.length ? pageSources.at(-1)?.source_id : null }
        }
        if (body) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
        const file = url.pathname.startsWith('/assets/') ? resolve(dist, '.' + url.pathname) : resolve(dist, 'index.html')
        assert.ok(file.startsWith(dist + '/') && !file.includes('..'))
        return route.fulfill({ status: 200, contentType: extname(file) === '.js' ? 'application/javascript' : extname(file) === '.css' ? 'text/css' : 'text/html', body: await readFile(file) })
      })
      try {
        await page.goto('http://intelitex.test/work')
        await page.waitForFunction(expected => document.querySelectorAll('.book-card').length === expected, firstLimit)
        assert.equal(await page.locator('.library-grid').evaluate(element => getComputedStyle(element).gridTemplateColumns.match(/\d+(?:\.\d+)?px/g)?.length), columns)
        assert.deepEqual(requests, [firstLimit], `${width}px initial Library requests`)
        assert.equal(await page.locator('.book-card').count() % columns, 0)
        if (width === 1440) {
          await page.setViewportSize({ width: 1150, height: 1000 })
          await page.locator('.library-load-more').scrollIntoViewIfNeeded()
          await page.waitForFunction(() => document.querySelectorAll('.book-card').length === 30)
          assert.deepEqual(requests, [14, 16], 'the next page fills the new five-column row')
        }
        if (width === 1150) {
          await page.getByRole('button', { name: 'Refresh Library' }).click()
          await page.getByRole('status').filter({ hasText: 'Library refreshed.' }).waitFor()
          assert.deepEqual(requests, [15, 15], 'Refresh restarts with complete rows')
        }
        assert.deepEqual(errors, [])
      } finally { await page.close() }
    }
  } finally { await browser.close() }
})
