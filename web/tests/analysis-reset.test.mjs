import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import { extname, resolve } from 'node:path'
import test from 'node:test'
import { chromium } from 'playwright'

const dist = resolve('dist')
const metadata = { title: 'Offline Book', creators: [], language: null, source_language: 'en',
  target_language: 'pl', label: null, format: 'EPUB', word_count: 120,
  lifecycle: { archived: false, revision: 'life' } }
const profile = { name: 'local', stable_palette_index: 0, provider: 'llamacpp', model: null, enabled: true }
const pass = { state: 'pending', completed: 0, required: 0, retained: 0, provenance: [] }
const section = { id: 'ch0001', ordinal: 1, title: 'Chapter', fallback_excerpt: 'Opening text',
  content_type: 'narrative', processing: 'full', profiles: {},
  passes: Object.fromEntries([1, 2, 3, 4, 5].map(n => [String(n), pass])) }
const progress = { percent: null, basis: 'Workflow progress — not an ETA',
  analysis: { completed: 0, required: 0, denominator: 'required P1 analysis units' },
  translation: { completed: 0, required: 1, denominator: 'required P5 translation units' } }
const pipeline = { workspace_id: 'w-1', stage: 'analysis', active_job: null, last_job: null,
  publishing: false, busy: false, metadata, artifacts: { terminology: false, book_memory: false },
  preparation: { source_id: 'book.epub', checks: ['Frozen source/chunk manifest verified'] }, progress,
  analysis: { complete: false, membership_locked: false, planned: false, units: [] },
  review: { prepared: false, current: false, revision: null, summary: null }, approved: false,
  translation_complete: false, sections: [section], config: { revision: 'rev-1', sections: {}, pass_profiles: {} },
  units: [], publication: { state: 'not_ready', current: false, translation_complete: false,
    target_language: 'pl', title: null, creators: [], source_language: 'en', generated_at: null,
    generated_by: null, last_error: null, last_failure: null, filename: null, size_bytes: null, checks: [] },
  actions: { analyze: { allowed: true, reason: null }, prepare_review: { allowed: false, reason: 'analysis_required' },
    translate: { allowed: false, reason: 'approval_required' }, publish: { allowed: false, reason: 'translation_required' } } }
const workspaceList = { workspaces: [{ workspace_id: 'w-1', source_id: 'book.epub', prepared: true,
  metadata, active_job: null, last_job: null, progress }] }
const profiles = { source: 'project', revision: 'settings', assignments: {}, default_profile: 'local',
  profiles: [profile], resolved_passes: Object.fromEntries([1, 2, 3, 4, 5].map(n => [String(n), profile])) }
const reset = { revision: 'p1-empty', has_data: false, can_reset: true, reason: null, history_available: false }

test('Analyse shows an honest empty state and clears saved P1 only after confirmation', { timeout: 60000 }, async () => {
  await mkdir('/tmp/intelitex-analysis-reset-evidence', { recursive: true })
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
    page.setDefaultTimeout(8000)
    const errors = [], failed = []
    let capabilityDelay = true, resetPosts = 0
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    page.on('requestfailed', request => failed.push(`${request.url()}: ${request.failure()?.errorText}`))
    await page.addInitScript(() => { window.EventSource = class {
      static OPEN = 1; readyState = 1; listeners = {}
      constructor() { window.testStream = this }
      addEventListener(type, callback) { this.listeners[type] = callback }
      emitSnapshot() { this.listeners.snapshot?.({ data: '{"jobs":[],"cursor":0}' }) }
      close() {}
    } })
    await page.route('http://localhost/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname
      let body
      if (path === '/api/capabilities') {
        body = { scope_id: 'offline-reset', import_enabled: true, review: true, reader: true,
          sse: true, multi_workspace: true, drafts: true, archive: true, section_configuration: true, diagnostics: false }
        if (capabilityDelay) { capabilityDelay = false; await new Promise(resolve => setTimeout(resolve, 900)) }
      } else if (path === '/api/jobs') body = { jobs: [], cursor: 0 }
      else if (path === '/api/workspaces') body = workspaceList
      else if (path === '/api/workspaces/w-1/pipeline') body = pipeline
      else if (path === '/api/workspaces/w-1/profiles') body = profiles
      else if (path === '/api/workspaces/w-1/usage') body = { scope: 'offline', warning: null, units: [] }
      else if (path === '/api/workspaces/w-1/analysis-reset') {
        if (request.method() === 'POST') {
          assert.deepEqual(request.postDataJSON(), { revision: 'p1-planned' })
          resetPosts++
          reset.revision = 'p1-cleared'; reset.has_data = false; reset.history_available = true
          pipeline.analysis = { complete: false, membership_locked: false, planned: false, units: [] }
          pipeline.progress.analysis.required = 0
          pipeline.artifacts = { terminology: false, book_memory: false }
        }
        body = reset
      }
      if (body) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
      if (path.startsWith('/api/')) return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
      const file = path.startsWith('/assets/') ? resolve(dist, '.' + path) : resolve(dist, 'index.html')
      assert.ok(file.startsWith(dist + '/') && !file.includes('..'))
      return route.fulfill({ status: 200, contentType: extname(file) === '.js' ? 'application/javascript' :
        extname(file) === '.css' ? 'text/css' : 'text/html', body: await readFile(file) })
    })
    try {
      await page.goto('http://localhost/work/workspaces/w-1/analyse')
      await page.locator('[data-ui-debug-id="BLS"]').waitFor()
      assert.equal(await page.locator('.topbar').count(), 1, 'refresh has visible application chrome while connecting')
      await page.locator('[data-ui-debug-id="PAE"]').waitFor()
      await page.evaluate(() => window.testStream.emitSnapshot())
      await page.getByRole('status').filter({ hasText: 'Live' }).waitFor()
      await page.getByText('P1 has not been started').waitFor()
      assert.equal(await page.locator('[data-ui-debug-id="PHM"]').count(), 0, 'unknown usage is not shown as four empty metrics')
      assert.equal(await page.getByRole('button', { name: 'Clear P1' }).count(), 0, 'no fake reset for an empty P1')
      await page.screenshot({ path: '/tmp/intelitex-analysis-reset-evidence/empty-dark-1440.png', animations: 'disabled' })
      await page.getByRole('button', { name: 'Return to workspace' }).click()
      await page.locator('[data-ui-debug-id="WSP"]').waitFor()

      pipeline.analysis = { complete: false, membership_locked: true, planned: true,
        units: [{ id: 'ch0001_a001', chapter_id: 'ch0001', state: 'pending', attempt_result: 'failed', failed_attempt_count: 1 }] }
      pipeline.progress.analysis.required = 1
      reset.revision = 'p1-planned'; reset.has_data = true
      await page.goto('http://localhost/work/workspaces/w-1/analyse')
      await page.locator('[data-ui-debug-id="PAC"]').getByRole('button', { name: 'Clear P1' }).waitFor()
      await page.evaluate(() => window.testStream.emitSnapshot())
      await page.getByRole('status').filter({ hasText: 'Live' }).waitFor()
      await page.getByRole('button', { name: 'Clear P1' }).click()
      await page.locator('[data-ui-debug-id="PRM"]').waitFor()
      assert.equal(resetPosts, 0, 'opening confirmation does not mutate P1')
      await page.getByRole('button', { name: 'Cancel' }).click()
      assert.equal(resetPosts, 0)
      await page.getByRole('button', { name: 'Clear P1' }).click()
      await page.getByRole('button', { name: 'Clear P1 and return' }).click()
      await page.locator('[data-ui-debug-id="WSP"]').waitFor()
      assert.equal(resetPosts, 1)
      await page.setViewportSize({ width: 390, height: 844 })
      await page.getByRole('button', { name: 'Toggle theme' }).click()
      await page.screenshot({ path: '/tmp/intelitex-analysis-reset-evidence/workspace-light-390.png', fullPage: true, animations: 'disabled' })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
      assert.deepEqual(errors, [])
      assert.deepEqual(failed, [])
    } finally { await page.close() }
  } finally { await browser.close() }
})
