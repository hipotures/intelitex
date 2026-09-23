import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import { extname, resolve } from 'node:path'
import test from 'node:test'
import { chromium } from 'playwright'

const dist = resolve('dist')
const output = '/tmp/intelitex-reprepare-evidence'
const profile = { name: 'local', stable_palette_index: 0, provider: 'llamacpp', model: null, enabled: true }
const passes = Object.fromEntries([1, 2, 3, 4, 5].map(n => [String(n), {
  state: 'pending', completed: 0, required: n === 1 ? 0 : 1, retained: 0, provenance: [],
}]))
const metadata = { title: 'Offline Chapters', creators: ['Test Author'], language: 'en', source_language: 'en',
  target_language: 'pl', label: null, format: 'EPUB', word_count: 120, lifecycle: { archived: false, revision: 'life' } }
const progress = { percent: null, basis: 'Workflow progress — not an ETA',
  analysis: { completed: 0, required: 0, denominator: 'required P1 analysis units' },
  translation: { completed: 0, required: 1, denominator: 'required P5 translation units' } }
const section = { id: 'ch0001', ordinal: 1, title: 'ONE', fallback_excerpt: 'First chapter.',
  content_type: 'narrative', processing: 'full', profiles: {}, passes }
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
    translate: { allowed: false, reason: 'approval_required' }, publish: { allowed: false, reason: 'translation_required' } },
}
const workspaces = { workspaces: [{ workspace_id: 'w-1', source_id: 'book.epub', prepared: true,
  metadata, active_job: null, last_job: null, progress }] }
const profiles = { source: 'project', revision: 'settings', assignments: {}, default_profile: 'local',
  profiles: [profile], resolved_passes: Object.fromEntries([1, 2, 3, 4, 5].map(n => [String(n), profile])) }

test('Prepare rebuild is explicit and F/T/E responds before slow background reads', { timeout: 60000 }, async () => {
  await mkdir(output, { recursive: true })
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
    page.setDefaultTimeout(8000)
    const errors = [], failed = [], mutations = []
    let slowReads = 0
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    page.on('requestfailed', request => failed.push(request.url()))
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
      if (path === '/api/capabilities') body = { scope_id: 'offline-reprepare', import_enabled: true,
        review: true, reader: true, sse: true, multi_workspace: true, drafts: true, archive: true,
        section_configuration: true, diagnostics: false }
      else if (path === '/api/jobs') body = { jobs: [], cursor: 0 }
      else if (path === '/api/workspaces') body = workspaces
      else if (path === '/api/workspaces/w-1/pipeline') body = pipeline
      else if (path === '/api/workspaces/w-1/profiles') body = profiles
      else if (path === '/api/workspaces/w-1/preparation') body = { checks: ['Frozen source/chunk manifest verified'],
        unavailable: null, reading_order: 'opf_spine', source_id: 'book.epub' }
      else if (path === '/api/workspaces/w-1/sections/ch0001' && request.method() === 'PATCH') {
        const payload = request.postDataJSON()
        assert.equal(payload.revision, pipeline.config.revision)
        assert.equal(payload.processing, 'translate')
        mutations.push({ method: 'PATCH', path, payload })
        pipeline.config.revision = 'rev-2'
        pipeline.sections[0].processing = 'translate'
        body = { revision: 'rev-2', sections: { ch0001: { processing: 'translate' } }, pass_profiles: {} }
        slowReads = 2
      } else if (path === '/api/workspaces/w-1/reprepare' && request.method() === 'POST') {
        const payload = request.postDataJSON()
        assert.equal(payload.revision, 'rev-2')
        mutations.push({ method: 'POST', path, payload })
        body = { job_id: 'rebuild-job', workspace_id: 'w-1', operation: 'import', state: 'starting',
          sequence: 0, started_at: null, finished_at: null, last_event: null, error: null }
        pipeline.active_job = body; pipeline.busy = true
        workspaces.workspaces[0].active_job = body
      }
      if (body) {
        if (slowReads && request.method() === 'GET' && ['/api/workspaces', '/api/workspaces/w-1/pipeline'].includes(path)) {
          slowReads--
          await new Promise(resolve => setTimeout(resolve, 3500))
        }
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
      }
      if (path.startsWith('/api/')) return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
      const file = path.startsWith('/assets/') ? resolve(dist, '.' + path) : resolve(dist, 'index.html')
      assert.ok(file.startsWith(dist + '/') && !file.includes('..'))
      return route.fulfill({ status: 200, contentType: extname(file) === '.js' ? 'application/javascript' :
        extname(file) === '.css' ? 'text/css' : 'text/html', body: await readFile(file) })
    })
    try {
      await page.goto('http://localhost/work/workspaces/w-1')
      await page.locator('[data-ui-debug-id="SCT"]').waitFor()
      await page.evaluate(() => window.testStream.emitSnapshot())
      await page.getByRole('status').filter({ hasText: 'Live' }).waitFor()
      await page.getByText('Analyse · P1').waitFor()
      const started = Date.now()
      await page.locator('.processing-switch button[title="T — Translate only"]').click()
      await page.locator('.processing-switch button[title="T — Translate only"].active').waitFor({ timeout: 1000 })
      assert.ok(Date.now() - started < 1200, 'F/T/E remains responsive while global refreshes are slow')
      assert.equal(mutations.length, 1)
      await page.waitForTimeout(3800)
      await page.getByRole('button', { name: /Prepare.*Source structure frozen/ }).click()
      await page.getByRole('button', { name: 'Run Prepare again' }).waitFor()
      await page.screenshot({ path: `${output}/prepare-dark-1440.png`, animations: 'disabled' })
      await page.getByRole('button', { name: 'Run Prepare again' }).click()
      await page.locator('[data-ui-debug-id="RPM"]').waitFor()
      assert.equal(mutations.length, 1, 'opening confirmation does not start a job')
      await page.screenshot({ path: `${output}/rebuild-confirm-dark-1440.png`, animations: 'disabled' })
      await page.getByRole('button', { name: 'Rebuild Prepare' }).click()
      await page.locator('[data-ui-debug-id="RPM"]').waitFor({ state: 'hidden' })
      assert.equal(mutations.length, 2)
      await page.setViewportSize({ width: 390, height: 844 })
      await page.getByRole('button', { name: 'Toggle theme' }).click()
      await page.screenshot({ path: `${output}/prepare-light-390.png`, animations: 'disabled' })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
      assert.equal(await page.locator('.prepare-structure').evaluate(table =>
        table.parentElement.scrollWidth > table.parentElement.clientWidth), true)
      assert.deepEqual(errors, [])
      assert.deepEqual(failed, [])
    } finally { await page.close() }
  } finally { await browser.close() }
})
