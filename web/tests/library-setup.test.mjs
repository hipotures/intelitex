import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import { extname, resolve } from 'node:path'
import test from 'node:test'
import { chromium } from 'playwright'

const dist = resolve('dist')
const output = '/tmp/intelitex-library-setup-evidence'
const profile = { name: 'codex-luna-low', stable_palette_index: 0, provider: 'codex', model: 'gpt-5.6-luna', enabled: true,
  source_languages: null, target_languages: null }
const profiles = { source: 'defaults', revision: 'defaults', default_profile: 'codex-luna-low', assignments: {},
  profiles: [profile], resolved_passes: Object.fromEntries([1, 2, 3, 4, 5].map(number => [String(number), profile])) }
const source = { source_id: 'book.epub', title: 'Relay Book', creators: ['Test Author'], language: 'en', word_count: null, workspace_id: null }
const preflight = { source_id: source.source_id, title: source.title, creators: source.creators, declared_language: 'en',
  detected_language: 'en', detection_confidence: .8, source_language: 'en', source_fingerprint: 'a'.repeat(64), language_warning: null }

test('Library card is read-only, setup Save creates distinct drafts, and Prepare remains explicit', { timeout: 60000 }, async () => {
  await mkdir(output, { recursive: true })
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
    page.setDefaultTimeout(7000)
    const errors = [], failed = [], saves = [], prepares = [], workspaces = [], savedRequests = new Map()
    let failNextSave = false
    let libraryRequests = 0
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error' && !(message.text().includes('503') && message.location().url.includes('/api/workspaces/setup'))) errors.push(message.text()) })
    page.on('requestfailed', request => failed.push(request.url()))
    await page.addInitScript(() => {
      Object.defineProperty(Crypto.prototype, 'randomUUID', { value: undefined, configurable: true })
      window.EventSource = class { static OPEN = 1; readyState = 1; listeners = {}; constructor() { window.testStream = this }
        addEventListener(type, callback) { this.listeners[type] = callback }
        emitSnapshot() { this.listeners.snapshot?.({ data: '{"jobs":[],"cursor":0}' }) }
        close() {} }
    })
    await page.route('http://localhost/**', async route => {
      const request = route.request(), url = new URL(request.url())
      let body
      if (url.pathname === '/api/capabilities') body = { scope_id: 'offline-setup', import_enabled: true, review: true,
        reader: true, sse: true, multi_workspace: true, drafts: true, archive: true, section_configuration: true, diagnostics: false }
      else if (url.pathname === '/api/jobs') body = { jobs: [], cursor: 0 }
      else if (url.pathname === '/api/workspaces' && request.method() === 'GET') body = { workspaces }
      else if (url.pathname === '/api/library') { libraryRequests++; body = { configured: true, sources: [source], next_cursor: null } }
      else if (url.pathname === '/api/profiles') body = profiles
      else if (url.pathname.endsWith('/preflight')) body = preflight
      else if (url.pathname.endsWith('/inspect')) body = { ...preflight, sample_word_count: 126, sampled_documents: 2,
        document_count: 8, sample_previews: [{ position: 1, heading: 'Opening', excerpt: 'The relay moved through the valley.' },
          { position: 8, heading: null, excerpt: 'The reader found the next signal.' }] }
      else if (url.pathname === '/api/library/compatibility') body = { compatible: true,
        warnings: ['Source languages are not declared for codex-luna-low.', 'Target languages are not declared for codex-luna-low.'], target_choices: ['pl'] }
      else if (url.pathname === '/api/workspaces/setup') {
        const payload = request.postDataJSON()
        saves.push(payload)
        body = savedRequests.get(payload.request_key)
        if (!body) {
          const workspace_id = `w-${workspaces.length + 1}`
          workspaces.push({ workspace_id, source_id: source.source_id, prepared: false,
            metadata: { title: source.title, creators: source.creators, language: 'en', source_language: payload.source_language,
              target_language: payload.target_language, label: payload.label, word_count: null,
              lifecycle: { archived: false, revision: 'rev' } }, active_job: null, last_job: null })
          body = { workspace_id, source_id: source.source_id }
          savedRequests.set(payload.request_key, body)
          if (failNextSave) { failNextSave = false; return route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":{"code":"unavailable","message":"Retry","details":{}}}' }) }
        }
      }
      else if (/^\/api\/workspaces\/w-\d+\/prepare$/.test(url.pathname)) {
        prepares.push(request.postDataJSON())
        body = { job_id: 'j-prepare', workspace_id: url.pathname.split('/')[3], operation: 'import', state: 'starting',
          sequence: 0, started_at: null, finished_at: null, last_event: null, error: null }
      }
      if (body) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
      if (url.pathname.startsWith('/api/')) return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
      const file = url.pathname.startsWith('/assets/') ? resolve(dist, '.' + url.pathname) : resolve(dist, 'index.html')
      assert.ok(file.startsWith(dist + '/') && !file.includes('..'))
      return route.fulfill({ status: 200, contentType: extname(file) === '.js' ? 'application/javascript' : extname(file) === '.css' ? 'text/css' : 'text/html', body: await readFile(file) })
    })
    try {
      await page.goto('http://localhost/work')
      assert.equal(await page.evaluate(() => typeof crypto.randomUUID), 'undefined', 'plain-HTTP LAN behavior must be reproduced')
      assert.equal(await page.evaluate(() => typeof crypto.getRandomValues), 'function')
      await page.locator('[data-source-id="book.epub"]').waitFor()
      assert.equal(await page.title(), 'Intelitex')
      await page.screenshot({ path: `${output}/work-dark-1440.png`, animations: 'disabled' })
      await page.locator('[data-source-id="book.epub"]').click()
      await page.locator('[data-ui-debug-id="LSD"]').waitFor()
      assert.equal(saves.length, 0, 'opening a card must not create a workspace')
      assert.equal(libraryRequests, 1, 'opening a card must not refresh Library')
      await page.getByRole('button', { name: 'Inspect source' }).click()
      await page.getByText('reading-order files, not necessarily chapters').waitFor()
      await page.getByText('The relay moved through the valley.').waitFor()
      assert.equal(await page.getByText('chapter1.xhtml').count(), 0)
      assert.equal(await page.getByText('100% separation').count(), 0)
      await page.screenshot({ path: `${output}/inspect-dark-1440.png`, animations: 'disabled' })
      await page.setViewportSize({ width: 390, height: 844 })
      await page.screenshot({ path: `${output}/inspect-dark-390.png`, animations: 'disabled' })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
      await page.setViewportSize({ width: 1440, height: 1000 })
      await page.getByRole('button', { name: 'Add to workspace' }).click()
      await page.locator('[data-ui-debug-id="WCM"]').waitFor()
      await page.getByRole('combobox', { name: 'Target language' }).waitFor()
      await page.getByText('Source languages are not declared for codex-luna-low.').waitFor()
      await page.getByText('Missing language declarations are profile metadata').waitFor()
      assert.equal(await page.getByRole('button', { name: 'Waiting for sync…' }).isDisabled(), true)
      await page.getByText('Save is waiting for live synchronization').waitFor()
      await page.screenshot({ path: `${output}/setup-waiting-dark-1440.png`, animations: 'disabled' })
      await page.evaluate(() => window.testStream.emitSnapshot())
      await page.getByRole('button', { name: 'Save', exact: true }).waitFor({ state: 'visible' })
      assert.equal(await page.getByRole('button', { name: 'Save', exact: true }).isEnabled(), true)
      await page.screenshot({ path: `${output}/setup-dark-1440.png`, animations: 'disabled' })
      await page.getByRole('button', { name: 'Cancel' }).click()
      assert.equal(saves.length, 0, 'Cancel must leave no draft')
      await page.locator('[data-source-id="book.epub"]').click()
      await page.getByRole('button', { name: 'Add to workspace' }).click()
      await page.getByRole('textbox', { name: 'Workspace label · optional' }).fill('First experiment')
      await page.getByRole('button', { name: 'Save', exact: true }).click()
      await page.getByRole('heading', { name: 'Prepare this workspace' }).waitFor()
      assert.equal(new URL(page.url()).pathname, '/work/workspaces/w-1')
      assert.equal(await page.locator('.sections-table').count(), 0)
      assert.equal(await page.getByRole('button', { name: 'Prepare', exact: true }).count(), 2)
      assert.equal(await page.locator('.phase').first().isDisabled(), true)
      assert.equal(saves.length, 1)
      const prepareResponse = page.waitForResponse(response => response.url().endsWith('/prepare'))
      await page.getByRole('button', { name: 'Prepare', exact: true }).first().click()
      await prepareResponse
      assert.match(prepares[0].request_key, /^[a-f0-9]{32}$/)
      await page.getByRole('link', { name: 'Library', exact: true }).click()
      await page.locator('[data-source-id="book.epub"]').waitFor()
      await page.getByText('1 workspace', { exact: true }).waitFor()
      await page.locator('[data-source-id="book.epub"]').click()
      await page.getByRole('button', { name: 'Add to workspace' }).click()
      await page.getByRole('button', { name: 'Save', exact: true }).click()
      await page.getByRole('heading', { name: 'Prepare this workspace' }).waitFor()
      assert.equal(new URL(page.url()).pathname, '/work/workspaces/w-2')
      assert.equal(saves.length, 2)
      await page.getByRole('link', { name: 'Library', exact: true }).click()
      await page.getByText('2 workspaces', { exact: true }).waitFor()
      await page.setViewportSize({ width: 390, height: 844 })
      await page.locator('[data-source-id="book.epub"]').click()
      await page.getByRole('button', { name: 'Add to workspace' }).click()
      await page.screenshot({ path: `${output}/setup-dark-390.png`, animations: 'disabled' })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
      await page.keyboard.press('Escape')
      await page.getByRole('button', { name: 'Toggle theme' }).click()
      await page.locator('[data-source-id="book.epub"]').click()
      await page.getByRole('button', { name: 'Add to workspace' }).click()
      await page.screenshot({ path: `${output}/setup-light-390.png`, animations: 'disabled' })
      assert.deepEqual(failed, [])
      assert.deepEqual(errors, [])
      assert.equal(libraryRequests, 1, 'setup and navigation must not rescan Library')
      failNextSave = true
      await page.getByRole('button', { name: 'Save', exact: true }).click()
      await page.getByText('Save may have succeeded.').waitFor()
      const pendingKey = saves.at(-1).request_key
      await page.reload()
      await page.locator('[data-source-id="book.epub"]').click()
      await page.getByRole('button', { name: 'Add to workspace' }).click()
      await page.evaluate(() => window.testStream.emitSnapshot())
      await page.getByRole('button', { name: 'Retry Save' }).click()
      await page.getByRole('heading', { name: 'Prepare this workspace' }).waitFor()
      assert.equal(new URL(page.url()).pathname, '/work/workspaces/w-3')
      assert.equal(saves.at(-1).request_key, pendingKey)
      assert.equal(workspaces.length, 3, 'lost Save acknowledgement must not create another draft')
      assert.deepEqual(failed, [])
      assert.deepEqual(errors, [])
      await page.getByRole('link', { name: 'Library', exact: true }).click()
      await page.locator('[data-source-id="book.epub"]').click()
      await page.getByRole('button', { name: 'Inspect source' }).click()
      await page.getByText('The relay moved through the valley.').waitFor()
      await page.screenshot({ path: `${output}/inspect-light-390.png`, animations: 'disabled' })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
      assert.deepEqual(errors, [])
    } finally { await page.close() }
  } finally { await browser.close() }
})
