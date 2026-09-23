import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { extname, resolve } from 'node:path'
import test from 'node:test'
import { chromium } from 'playwright'

const dist = resolve('dist')
const workspace = (id, title, archived) => ({
  workspace_id: id, prepared: true, source_id: `${id}.epub`, active_job: null, last_job: null,
  metadata: { title, creators: [], language: 'en', source_language: 'en', target_language: 'pl',
    word_count: null, lifecycle: { archived, revision: `revision-${id}` } },
})

test('Work loads compact active summaries and defers the archive', { timeout: 30000 }, async () => {
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    const errors = [], queries = [], summaries = [], fullPipelines = []
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
      if (url.pathname === '/api/capabilities') body = { scope_id: 'offline-workspaces', import_enabled: true,
        review: true, reader: true, sse: true, multi_workspace: true, drafts: true, archive: true,
        section_configuration: true, diagnostics: false }
      else if (url.pathname === '/api/jobs') body = { jobs: [], cursor: 0 }
      else if (url.pathname === '/api/workspaces') {
        const archived = url.searchParams.get('archived')
        queries.push(archived)
        assert.ok(archived === 'true' || archived === 'false', 'Work must use filtered workspace queries')
        body = { workspaces: archived === 'true' ? [workspace('old-book', 'Old Book', true)]
          : [workspace('new-book-1', 'New Book 1', false), workspace('new-book-2', 'New Book 2', false), workspace('new-book-3', 'New Book 3', false)] }
      } else if (/^\/api\/workspaces\/[^/]+\/summary$/.test(url.pathname)) {
        summaries.push(url.pathname)
        const id = url.pathname.split('/')[3]
        body = { workspace_id: id, stage: 'analysis', progress: { percent: null, basis: 'Workflow progress — not an ETA',
          analysis: { completed: 0, required: 0, denominator: 'required P1 analysis units' },
          translation: { completed: 0, required: 0, denominator: 'required P5 translation units' } },
        analysis: { complete: false }, approved: false, publication: { current: false, last_failure: null },
        actions: { analyze: { allowed: true, reason: null } },
        metadata: { lifecycle: { archived: id === 'old-book', revision: `revision-${id}` } },
        publishing: false, active_job: null, last_job: null }
      } else if (url.pathname.endsWith('/pipeline')) {
        fullPipelines.push(url.pathname)
        return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
      } else if (url.pathname === '/api/library') body = { configured: true, sources: [], next_cursor: null }
      if (body) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
      const file = url.pathname.startsWith('/assets/') ? resolve(dist, '.' + url.pathname) : resolve(dist, 'index.html')
      assert.ok(file.startsWith(dist + '/') && !file.includes('..'))
      return route.fulfill({ status: 200, contentType: extname(file) === '.js' ? 'application/javascript' : extname(file) === '.css' ? 'text/css' : 'text/html', body: await readFile(file) })
    })
    try {
      await page.goto('http://intelitex.test/work')
      await page.getByText('New Book 3', { exact: true }).waitFor()
      assert.equal(await page.locator('.workspace-row').count(), 3)
      await page.getByRole('button', { name: 'Run', exact: true }).first().waitFor()
      assert.ok(summaries.filter(path => !path.includes('old-book')).length >= 3)
      assert.deepEqual(fullPipelines, [], 'Work cards must not fetch detail pipelines')
      assert.equal(queries.includes('true'), false, 'archive request must wait for drawer opening')
      const initialSummaries = summaries.length
      await page.waitForTimeout(16_000)
      assert.ok(summaries.length > initialSummaries, 'healthy-stream reconciliation must refresh Work summaries')
      assert.deepEqual(fullPipelines, [], 'periodic reconciliation must not refetch full pipelines')
      assert.equal(queries.includes('true'), false, 'periodic reconciliation must not open the archive')
      await page.getByRole('button', { name: 'Archive →' }).click()
      await page.getByText('Old Book', { exact: true }).waitFor()
      assert.ok(queries.includes('true'))
      assert.deepEqual(fullPipelines, [])
      assert.deepEqual(errors, [])
    } finally { await page.close() }
  } finally { await browser.close() }
})
