import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import { extname, resolve } from 'node:path'
import test from 'node:test'
import { chromium } from 'playwright'

const dist = resolve('dist')
const output = '/tmp/intelitex-p1-cost-evidence'
const metadata = { title: 'Offline Book', creators: [], language: 'en', source_language: 'en',
  target_language: 'pl', label: null, format: 'EPUB', word_count: 120,
  lifecycle: { archived: false, revision: 'life' } }
const job = { job_id: 'j-1', workspace_id: 'w-1', operation: 'analyze', state: 'running',
  sequence: 0, started_at: '2026-09-23T00:00:00Z', finished_at: null, last_event: null, error: null }
const passState = { state: 'pending', completed: 0, required: 0, retained: 0, provenance: [] }
const section = { id: 'ch0001', ordinal: 1, title: 'Chapter', fallback_excerpt: 'Opening text',
  content_type: 'narrative', processing: 'full', profiles: {},
  passes: Object.fromEntries([1, 2, 3, 4, 5].map(n => [String(n), passState])) }
const progress = { percent: null, basis: 'Workflow progress — not an ETA',
  analysis: { completed: 0, required: 1, denominator: 'required P1 analysis units' },
  translation: { completed: 0, required: 1, denominator: 'required P5 translation units' } }
const pipeline = { workspace_id: 'w-1', stage: 'analysis', active_job: job, last_job: null,
  publishing: false, busy: true, metadata, artifacts: { terminology: false, book_memory: false },
  preparation: { source_id: 'book.epub', checks: [] }, progress,
  analysis: { complete: false, membership_locked: true, planned: true,
    units: [{ id: 'ch0001_a001', chapter_id: 'ch0001', state: 'running', attempt_result: null, failed_attempt_count: 0 }] },
  review: { prepared: false, current: false, revision: null, summary: null }, approved: false,
  translation_complete: false, sections: [section], config: { revision: 'rev-1', sections: {}, pass_profiles: {} },
  units: [], publication: { state: 'not_ready', current: false, translation_complete: false,
    target_language: 'pl', title: null, creators: [], source_language: 'en', generated_at: null,
    generated_by: null, last_error: null, last_failure: null, filename: null, size_bytes: null, checks: [] },
  actions: { analyze: { allowed: false, reason: 'workspace_busy' }, prepare_review: { allowed: false, reason: 'analysis_required' },
    translate: { allowed: false, reason: 'approval_required' }, publish: { allowed: false, reason: 'translation_required' } } }
const aggregate = value => ({ value, known_attempts: 1, unknown_attempts: 0 })
const usagePass = { pass_no: 1, profile: 'codex-sol-medium', provider: 'codex', requested_model: 'gpt-5.6-sol',
  reported_model: 'gpt-5.6-sol', input_tokens: aggregate(40000), cached_input_tokens: aggregate(10000),
  reasoning_output_tokens: aggregate(1000), output_tokens: aggregate(5000), elapsed_seconds: aggregate(2),
  attempts: [], result_status: 'not_checkpointed', physical_attempt_count: 1, provider_call_count: 1,
  unknown_provider_call_count: 0, failed_attempt_count: 0, retry_count: 0,
  cost: { status: 'complete', amount: .123456, currency: 'USD', estimate_type: 'codex_api_equivalent_standard',
    note: 'Codex API-equivalent estimate, not a subscription invoice.' } }
const usage = { scope: 'offline physical attempts', warning: null,
  units: [{ unit_id: 'ch0001_a001', chapter_id: 'ch0001', passes: [usagePass] }] }

test('P1 prices update with recorded usage during a run and stop pulsing when it ends', { timeout: 60000 }, async () => {
  await mkdir(output, { recursive: true })
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
    page.setDefaultTimeout(8000)
    const errors = [], failed = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    page.on('requestfailed', request => failed.push(`${request.url()}: ${request.failure()?.errorText}`))
    await page.addInitScript(() => { window.EventSource = class {
      static OPEN = 1; readyState = 1; listeners = {}
      constructor() { window.testStream = this }
      addEventListener(type, callback) { this.listeners[type] = callback }
      emitProgress(event) { this.listeners.progress?.({ data: JSON.stringify(event) }) }
      close() {}
    } })
    await page.route('http://localhost/**', async route => {
      const path = new URL(route.request().url()).pathname
      let body
      if (path === '/api/capabilities') body = { scope_id: 'offline-cost', import_enabled: true, review: true,
        reader: true, sse: true, multi_workspace: true, drafts: true, archive: true,
        section_configuration: true, diagnostics: false }
      else if (path === '/api/jobs') body = { jobs: pipeline.active_job ? [pipeline.active_job] : [], cursor: 1 }
      else if (path === '/api/workspaces') body = { workspaces: [{ workspace_id: 'w-1', source_id: 'book.epub',
        prepared: true, metadata, active_job: pipeline.active_job, last_job: null, progress }] }
      else if (path === '/api/workspaces/w-1/pipeline') body = pipeline
      else if (path === '/api/workspaces/w-1/usage') body = usage
      else if (path === '/api/workspaces/w-1/profiles') body = { source: 'project', revision: 'settings',
        assignments: {}, default_profile: 'codex-sol-medium', profiles: [], resolved_passes: {} }
      else if (path === '/api/workspaces/w-1/analysis-reset') body = { revision: 'p1-running',
        has_data: true, can_reset: false, reason: 'workspace_busy', history_available: false }
      if (body) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
      if (path.startsWith('/api/')) return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
      const file = path.startsWith('/assets/') ? resolve(dist, '.' + path) : resolve(dist, 'index.html')
      assert.ok(file.startsWith(dist + '/') && !file.includes('..'))
      return route.fulfill({ status: 200, contentType: extname(file) === '.js' ? 'application/javascript' :
        extname(file) === '.css' ? 'text/css' : 'text/html', body: await readFile(file) })
    })
    try {
      await page.goto('http://localhost/work/workspaces/w-1/analyse')
      assert.equal(await page.title(), 'Intelitex')
      await page.locator('[data-ui-debug-id="PAN"]').waitFor()
      const metrics = page.locator('[data-ui-debug-id="PHM"]')
      const cost = metrics.locator('[data-entity-id="Cost"] .phase-metric-value')
      await metrics.getByText('$0.1235').waitFor()
      assert.equal(await page.locator('[data-ui-debug-id="PAN"] th').last().textContent(), 'Cost')
      assert.equal(await page.locator('[data-ui-debug-id="PAN"] tbody td').last().textContent(), '$0.1235')
      assert.equal(await page.locator('[data-ui-debug-id="PAN"] tbody td').last().evaluate(node => node.scrollWidth <= node.clientWidth), true,
        'the Cost value is not ellipsized on desktop')
      assert.equal(await page.locator('[data-ui-debug-id="PAN"] .diagnostic-scroll').evaluate(node => node.scrollWidth <= node.clientWidth), true,
        'the new Cost column is visible without horizontal scrolling on desktop')
      const layout = await page.evaluate(() => {
        const generated = document.querySelector('[data-ui-debug-id="PGD"]').getBoundingClientRect()
        const analysis = document.querySelector('[data-ui-debug-id="PAN"]').getBoundingClientRect()
        const artifacts = document.querySelector('[data-ui-debug-id="PAR"]').getBoundingClientRect()
        const main = document.querySelector('.analyse-detail-page').getBoundingClientRect()
        const mainStyle = getComputedStyle(document.querySelector('.analyse-detail-page'))
        return { generatedBottom: generated.bottom, analysisTop: analysis.top, analysisBottom: analysis.bottom,
          artifactsTop: artifacts.top, analysisWidth: analysis.width,
          mainWidth: main.width - parseFloat(mainStyle.paddingLeft) - parseFloat(mainStyle.paddingRight) }
      })
      assert.ok(layout.generatedBottom < layout.analysisTop, 'compact generated data precedes the table')
      assert.ok(layout.artifactsTop > layout.analysisBottom, 'artifacts follow the table')
      assert.ok(layout.analysisWidth >= layout.mainWidth - 1, 'analysis table gets the full content width')
      const gap = await page.evaluate(() => document.querySelector('[data-ui-debug-id="PAC"]').getBoundingClientRect().top -
        document.querySelector('.analyse-detail-grid').getBoundingClientRect().bottom)
      assert.ok(gap >= 13, `P1 data has spacing after the analysis grid: ${gap}px`)
      assert.equal(await metrics.getByText('(partial)').count(), 0)
      assert.equal(await cost.evaluate(node => getComputedStyle(node).animationDuration), '3.6s')
      await page.screenshot({ path: `${output}/running-dark-1440.png`, animations: 'disabled' })

      usagePass.input_tokens = { value: 86808, known_attempts: 2, unknown_attempts: 1 }
      usagePass.cost = { ...usagePass.cost, status: 'partial', amount: .234567 }
      await page.evaluate(() => window.testStream.emitProgress({ id: 1, job_id: 'j-1', workspace_id: 'w-1',
        sequence: 1, timestamp: '2026-09-23T00:00:01Z', event: { kind: 'provider_usage_update', values: {} } }))
      await metrics.getByText('86,808').waitFor()
      await metrics.getByText('$0.2346').waitFor()
      assert.equal(await metrics.getByText('(partial)').count(), 0)
      assert.match(await cost.getAttribute('title'), /unknown price or usage/)

      pipeline.active_job = null
      pipeline.busy = false
      await page.evaluate(() => window.testStream.emitProgress({ id: 2, job_id: 'j-1', workspace_id: 'w-1',
        sequence: 2, timestamp: '2026-09-23T00:00:02Z', event: { kind: 'job_state', values: { state: 'succeeded' } } }))
      await page.locator('.phase-status-badge').getByText('Ready').waitFor()
      assert.equal(await cost.evaluate(node => getComputedStyle(node).animationName), 'none')
      await page.setViewportSize({ width: 390, height: 844 })
      await page.getByRole('button', { name: 'Toggle theme' }).click()
      await page.screenshot({ path: `${output}/complete-light-390.png`, fullPage: true, animations: 'disabled' })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
      assert.equal(await page.locator('[data-ui-debug-id="PAN"] .diagnostic-scroll').evaluate(node => node.scrollWidth > node.clientWidth), true,
        'a narrow viewport scrolls inside the table')
      assert.deepEqual(errors, [])
      assert.deepEqual(failed, [])
    } finally { await page.close() }
  } finally { await browser.close() }
})
