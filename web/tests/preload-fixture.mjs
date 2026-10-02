import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { readFile, mkdtemp, rm } from 'node:fs/promises'
import { extname, resolve, join } from 'node:path'
import { tmpdir } from 'node:os'

export async function preloadFixture() {
  if (process.env.INTELITEX_TEST_P0_FIXTURE_PATH) return JSON.parse(await readFile(process.env.INTELITEX_TEST_P0_FIXTURE_PATH, 'utf8'))
  const directory = await mkdtemp(join(tmpdir(), 'intelitex-p0-dto-'))
  const file = join(directory, 'fixture.json')
  try {
    const child = spawn('../.venv/bin/python', ['../tests/preload_web_fixture.py', '--output', file], { stdio: ['ignore', 'ignore', 'pipe'] })
    let error = ''; child.stderr.on('data', data => error += data)
    await new Promise((yes, no) => { child.once('error', no); child.once('exit', code => code === 0 ? yes() : no(new Error(error))) })
    return JSON.parse(await readFile(file, 'utf8'))
  } finally { await rm(directory, { recursive: true, force: true }) }
}

export async function preloadPage(browser, fixture, scenario = 'pending', viewport = { width: 1440, height: 1000 }) {
  assert.equal(fixture.native_loopback_calls, 2)
  assert.equal(fixture.cloud_calls, 0)
  const page = await browser.newPage({ viewport, colorScheme: 'dark' })
  page.setDefaultTimeout(8000)
  const state = structuredClone(fixture[scenario]), requests = [], mutations = [], errors = []
  let sequence = 0, id = 0
  const job = { job_id: 'p0-browser-job', workspace_id: 'book', operation: 'preload', state: 'running',
    sequence: 0, started_at: '2026-10-02T00:00:00Z', finished_at: null, last_event: null, error: null }
  const receipts = new Map()
  const workspace = () => state.workspaces.workspaces.find(w => w.workspace_id === 'book')
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error' && !/Failed to load resource/.test(message.text())) errors.push(message.text()) })
  await page.addInitScript(() => {
    window.EventSource = class {
      static OPEN = 1; readyState = 1; listeners = {}
      constructor() { window.testStream = this; setTimeout(() => this.listeners.snapshot?.({ data: JSON.stringify({ jobs: [], cursor: 0 }) }), 20) }
      addEventListener(type, callback) { this.listeners[type] = callback }
      emit(event) { this.listeners.progress?.({ data: JSON.stringify(event) }) }
      close() {}
    }
  })
  await page.route('http://localhost/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname, method = route.request().method()
    requests.push({ path, method })
    let body
    if (method !== 'GET') {
      const value = route.request().postDataJSON()
      mutations.push({ path, value })
      if (path === '/api/workspaces/book/jobs') {
        const started = { ...job, operation: value.operation }; receipts.set(value.request_key, started)
        if (state.loseResponse) { state.loseResponse = false; return route.abort('failed') }
        body = started
      } else if (path === `/api/jobs/${job.job_id}/stop`) body = { ...job, state: 'stopping' }
    } else if (path === '/api/capabilities') body = { scope_id: 'offline-preload', import_enabled: true,
      review: true, reader: true, sse: true, multi_workspace: true, drafts: true, archive: true,
      section_configuration: true, diagnostics: false }
    else if (path === '/api/jobs') body = { jobs: state.pipeline.active_job ? [state.pipeline.active_job] : [], cursor: id }
    else if (path === '/api/workspaces') body = state.workspaces
    else if (path === '/api/library') body = { configured: true, sources: [] }
    else if (path === '/api/profiles') body = state.profiles
    else if (path === '/api/workspaces/book/pipeline') body = state.pipeline
    else if (path === '/api/workspaces/book/usage') body = state.usage
    else if (path === '/api/workspaces/book/profiles') body = state.profiles
    else if (path === '/api/workspaces/book/source-preload') body = state.inventory
    else if (path === '/api/workspaces/book/activity') body = { events: [] }
    else if (path === '/api/workspaces/book/analysis-reset') body = { revision: 'no-p1', has_data: state.pipeline.analysis.planned, can_reset: state.pipeline.analysis.planned, reason: null, history_available: false }
    else if (path.startsWith('/api/requests/')) body = receipts.get(decodeURIComponent(path.split('/').at(-1)))
    else {
      const match = path.match(/^\/api\/workspaces\/book\/source-preload\/targets\/(pt_[a-f0-9]{64})\/preview$/)
      if (match) {
        const target = state.inventory.chapters.flatMap(c => c.targets).find(t => t.target_id === match[1])
        const actual = fixture.previews[match[1]]?.[Number(url.searchParams.get('page') ?? 0)]
        if (target && actual) body = { ...actual, source_kind: target.baseline_state === 'accepted' ? 'recorded' : 'planned',
          available: target.baseline_state === 'accepted', session: target }
      }
      const p1 = path.match(/^\/api\/workspaces\/book\/analysis\/units\/([^/]+)\/preview$/)
      if (p1) body = fixture.p1_previews[p1[1]]
    }
    if (body) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    if (path.startsWith('/api/')) return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: { message: 'Fixture route missing' } }) })
    const dist = resolve('dist'), file = path.startsWith('/assets/') ? resolve(dist, '.' + path) : resolve(dist, 'index.html')
    assert.ok(file.startsWith(dist + '/') && !path.includes('..'))
    return route.fulfill({ status: 200, contentType: extname(file) === '.js' ? 'application/javascript' : extname(file) === '.css' ? 'text/css' : 'text/html', body: await readFile(file) })
  })
  async function event(kind, values = {}, owner = job) {
    const envelope = { id: ++id, sequence: ++sequence, job_id: owner.job_id, workspace_id: owner.workspace_id,
      timestamp: '2026-10-02T00:00:01Z', event: { kind, values } }
    if (owner.job_id === state.pipeline.active_job?.job_id) state.pipeline.active_job.sequence = sequence
    await page.evaluate(envelope => window.testStream.emit(envelope), envelope)
    return envelope
  }
  async function refresh() { await event('provider_response_received'); await page.waitForTimeout(1100) }
  return { page, state, workspace, requests, mutations, errors, job, receipts, event, refresh }
}

export async function readyPreload(page, suffix = '') {
  await page.goto(`http://localhost/work/workspaces/book/preload${suffix}`)
  await page.locator('[data-ui-debug-id="PNU"] tbody tr').first().waitFor()
  await page.locator('[data-ui-debug-id="PNV"] .translate-preview-text').first().waitFor()
  await page.getByRole('status', { includeHidden: true }).filter({ hasText: /^Live$/ }).waitFor({ state: 'attached' })
}
