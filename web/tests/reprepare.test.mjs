import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import { extname, resolve } from 'node:path'
import test from 'node:test'
import { chromium } from 'playwright'

const dist = resolve('dist')
const output = '/tmp/intelitex-reprepare-evidence'
const profile = { name: 'local', stable_palette_index: 0, provider: 'llamacpp', model: null, enabled: true }
const otherProfile = { ...profile, name: 'local-other', stable_palette_index: 1 }
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
const secondSection = { id: 'ch0002', ordinal: 2, title: 'TWO', fallback_excerpt: 'Second chapter.',
  content_type: 'front_matter', processing: 'translate', profiles: {}, passes }
const thirdSection = { id: 'ch0003', ordinal: 3, title: 'THREE', fallback_excerpt: 'Third chapter.',
  content_type: 'narrative', processing: 'full', profiles: {}, passes }
const pipeline = { workspace_id: 'w-1', stage: 'analysis', active_job: null, last_job: null,
  publishing: false, busy: false, metadata, artifacts: { terminology: false, book_memory: false },
  preparation: { source_id: 'book.epub', checks: ['Frozen source/chunk manifest verified'] }, progress,
  analysis: { complete: false, membership_locked: false, planned: false, units: [] },
  review: { prepared: false, current: false, revision: null, summary: null }, approved: false,
  translation_complete: false, sections: [section, secondSection, thirdSection], config: { revision: 'rev-1', sections: {}, pass_profiles: {} },
  units: [], publication: { state: 'not_ready', current: false, translation_complete: false,
    target_language: 'pl', title: null, creators: [], source_language: 'en', generated_at: null,
    generated_by: null, last_error: null, last_failure: null, filename: null, size_bytes: null, checks: [] },
  actions: { analyze: { allowed: true, reason: null }, prepare_review: { allowed: false, reason: 'analysis_required' },
    translate: { allowed: false, reason: 'approval_required' }, publish: { allowed: false, reason: 'translation_required' } },
}
const workspaces = { workspaces: [{ workspace_id: 'w-1', source_id: 'book.epub', prepared: true,
  metadata, active_job: null, last_job: null, progress }] }
const profiles = { source: 'project', revision: 'settings', assignments: {}, default_profile: 'local',
  profiles: [profile, otherProfile], resolved_passes: Object.fromEntries([1, 2, 3, 4, 5].map(n => [String(n), profile])) }

test('Prepare rebuild is explicit and F/T/E responds before slow background reads', { timeout: 60000 }, async () => {
  await mkdir(output, { recursive: true })
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
    page.setDefaultTimeout(8000)
    const errors = [], failed = [], mutations = [], modelMutations = []
    let slowReads = 0, globalReadsAfterPatch = 0, patchReplies = 0, previewReads = 0, expectedConflictConsole = 0
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => {
      if (message.type() !== 'error') return
      if (message.text() === 'Failed to load resource: the server responded with a status of 409 (Conflict)') {
        expectedConflictConsole++
      } else errors.push(message.text())
    })
    page.on('requestfailed', request => {
      const reason = request.failure()?.errorText
      if (reason === 'net::ERR_ABORTED' && request.method() === 'GET' &&
          ['/api/workspaces', '/api/workspaces/w-1/pipeline'].includes(new URL(request.url()).pathname)) return
      failed.push(`${request.url()}: ${reason}`)
    })
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
      else if (path === '/api/workspaces') {
        if (mutations.some(item => item.method === 'PATCH')) globalReadsAfterPatch++
        body = workspaces
      }
      else if (path === '/api/workspaces/w-1/pipeline') body = pipeline
      else if (path === '/api/workspaces/w-1/profiles') body = profiles
      else if (path === '/api/workspaces/w-1/settings' && request.method() === 'PATCH') {
        const payload = request.postDataJSON()
        assert.equal(payload.revision, pipeline.config.revision)
        assert.equal(payload.allow_model_change, true)
        modelMutations.push({ path, payload })
        profiles.assignments = { ...profiles.assignments, ...payload.pass_profiles }
        profiles.resolved_passes['1'] = otherProfile
        pipeline.config.revision = 'rev-model-1'
        pipeline.config.pass_profiles = profiles.assignments
        body = pipeline.config
      }
      else if (path === '/api/workspaces/w-1/preparation') body = { checks: ['Frozen source/chunk manifest verified'],
        unavailable: null, reading_order: 'opf_spine', source_id: 'book.epub' }
      else if (path === '/api/workspaces/w-1/sections/ch0001/0') {
        previewReads++
        body = { id: 'ch0001', blocks: [{ id: 'b1', text: 'A'.repeat(1023) + 'ą' + '<script>window.bad=true</script>' }], next_page: null }
      } else if (path === '/api/workspaces/w-1/sections/ch0002/0') {
        previewReads++
        await new Promise(resolve => setTimeout(resolve, 350))
        body = { id: 'ch0002', blocks: [{ id: 'b2', text: 'The second section begins here.' }], next_page: null }
      }
      else if (path === '/api/workspaces/w-1/sections/ch0001' && request.method() === 'PATCH') {
        const payload = request.postDataJSON()
        assert.equal(payload.revision, pipeline.config.revision)
        if (payload.profiles) {
          assert.equal(payload.allow_model_change, true)
          modelMutations.push({ path, payload })
          section.profiles = { ...section.profiles, ...payload.profiles }
          pipeline.config.revision = 'rev-model-2'
          body = { ...pipeline.config, sections: { ch0001: { profiles: section.profiles } } }
        } else if (payload.processing === 'full') {
          await new Promise(resolve => setTimeout(resolve, 350))
          return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: {
            code: 'revision_conflict', message: 'Configuration changed.', details: {},
          } }) })
        } else {
          assert.equal(payload.processing, 'translate')
          mutations.push({ method: 'PATCH', path, payload })
          await new Promise(resolve => setTimeout(resolve, 1200))
          pipeline.config.revision = 'rev-2'
          pipeline.sections[0].processing = 'translate'
          body = { revision: 'rev-2', sections: { ch0001: { processing: 'translate' } }, pass_profiles: profiles.assignments }
          patchReplies++
          slowReads = 2
        }
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
      await page.getByRole('status').filter({ hasText: 'Live' }).waitFor({ state: 'attached' })
      await page.getByText('Analyse · P1').waitFor()
      await page.getByText('Start P1 with Run').waitFor()
      assert.equal(await page.locator('.processing-switch').count(), 0, 'workspace Processing is read-only')
      assert.equal(await page.locator('[data-ui-debug-id="SCT"] .processing-readonly').first().textContent(), 'F',
        'overview shows one compact Processing letter, not F plus Full')
      await page.getByRole('button', { name: /Pipeline models/ }).click()
      const pipelineModelSave = page.waitForResponse(response => response.url().endsWith('/settings') && response.request().method() === 'PATCH')
      await page.getByRole('combobox', { name: 'Pass 1' }).selectOption('local-other')
      await pipelineModelSave
      await page.waitForFunction(() => document.querySelector('#profile-1')?.value === 'local-other')
      assert.deepEqual(modelMutations[0]?.payload.pass_profiles, { '1': 'local-other' })
      assert.equal(await page.getByRole('dialog', { name: 'Change model assignment?' }).count(), 0)
      await page.locator('.sections-table tbody tr').first().click()
      await page.locator('[data-ui-debug-id="PVD"]').waitFor()
      const sectionModelSave = page.waitForResponse(response => response.url().endsWith('/sections/ch0001') && response.request().method() === 'PATCH')
      await page.getByRole('combobox', { name: 'Pass 2' }).last().selectOption('local-other')
      await sectionModelSave
      await page.waitForFunction(() => document.querySelector('#override-2')?.value === 'local-other')
      assert.deepEqual(modelMutations[1]?.payload.profiles, { '2': 'local-other' })
      assert.equal(await page.locator('[data-ui-debug-id="PVD"]').count(), 1, 'section stays open while its model saves')
      await page.locator('[data-ui-debug-id="PVD"]').getByRole('button', { name: 'Close' }).click()
      previewReads = 0
      await page.getByRole('button', { name: /Prepare.*Source structure frozen/ }).click()
      assert.equal(previewReads, 0, 'Prepare does not fetch source text before selection')
      const metadataTop = () => page.locator('[data-ui-debug-id="PSM"]').evaluate(element =>
        element.getBoundingClientRect().top + window.scrollY)
      const initialMetadataTop = await metadataTop()
      await page.locator('.prepare-structure tbody tr').first().click()
      await page.locator('[data-ui-debug-id="PPR"] .prepare-preview-text').waitFor()
      assert.ok(Math.abs(await metadataTop() - initialMetadataTop) < 2,
        'opening a long source excerpt does not move metadata and checks')
      assert.equal(await page.locator('.prepare-preview-text').textContent(), 'A'.repeat(1023),
        'the opening source text is bounded to one complete UTF-8 KiB')
      assert.equal(await page.evaluate(() => window.bad), undefined)
      const started = Date.now()
      await page.locator('.prepare-processing-switch').first().getByRole('button', { name: 'T' }).click()
      await page.locator('.prepare-processing-switch.saving button.active').first().waitFor({ timeout: 300 })
      assert.ok(Date.now() - started < 350, 'F/T/E selection appears before the delayed PATCH response')
      assert.equal(patchReplies, 0, 'pending selection is not claimed as persisted pipeline state')
      await page.locator('.prepare-processing-switch.saving').waitFor({ state: 'hidden' })
      assert.equal(mutations.length, 1)
      await page.waitForTimeout(3800)
      assert.equal(globalReadsAfterPatch, 0, 'section processing does not refetch the global workspace list')
      await page.locator('.prepare-processing-switch').first().getByRole('button', { name: 'F' }).click()
      await page.locator('.prepare-processing-switch.saving button.active').first().waitFor()
      await page.getByRole('alert').filter({ hasText: 'Configuration changed.' }).waitFor()
      assert.equal(await page.locator('.prepare-processing-switch').first().getByRole('button', { name: 'T' }).getAttribute('aria-pressed'), 'true',
        'a conflict restores the authoritative choice in Prepare')
      const readsAfterDrawer = previewReads
      await page.locator('.prepare-structure tbody tr').nth(1).locator('td').nth(1).click()
      await page.getByRole('status').filter({ hasText: 'Loading source text' }).waitFor()
      assert.ok(Math.abs(await metadataTop() - initialMetadataTop) < 2,
        'right column remains fixed while the next source preview loads')
      await page.getByText('The second section begins here.').waitFor()
      assert.ok(Math.abs(await metadataTop() - initialMetadataTop) < 2,
        'short and long source excerpts keep the same panel geometry')
      assert.equal(previewReads, readsAfterDrawer + 1, 'a newly selected section fetches its own source text')
      assert.equal(await page.locator('.prepare-structure tbody tr').nth(2).locator('td').last().textContent(), 'in',
        'P1 column means membership, not completed analysis')
      const rebuildButton = page.getByRole('button', { name: 'Rebuild', exact: true })
      await rebuildButton.waitFor()
      assert.equal(await rebuildButton.evaluate(button => button.classList.contains('primary-btn')), true,
        'Prepare rebuild has a prominent button style')
      assert.equal(await rebuildButton.evaluate(button => {
        const actions = button.parentElement.getBoundingClientRect()
        const control = button.getBoundingClientRect()
        const description = button.parentElement.previousElementSibling.getBoundingClientRect()
        return Math.abs(actions.right - control.right) < 2 && control.top - description.bottom >= 30
      }), true, 'Prepare rebuild is right-aligned and separated from its description')
      await page.screenshot({ path: `${output}/prepare-dark-1440.png`, animations: 'disabled' })
      await page.setViewportSize({ width: 390, height: 844 })
      await page.getByRole('button', { name: 'Toggle theme' }).click()
      await page.evaluate(() => {
        const row = document.querySelector('.prepare-structure tbody tr')
        window.scrollTo(0, window.scrollY + row.getBoundingClientRect().top - 140)
      })
      const mobileScroll = await page.evaluate(() => window.scrollY)
      const tableScroll = await page.locator('.prepare-structure-scroll').evaluate(element => element.scrollTop)
      const mobileMetadataTop = await metadataTop()
      await page.locator('.prepare-structure tbody tr').first().click()
      assert.ok(Math.abs(await page.evaluate(() => window.scrollY) - mobileScroll) < 100,
        'selecting a Prepare section does not jump to the source preview')
      assert.equal(await page.locator('.prepare-structure-scroll').evaluate(element => element.scrollTop), tableScroll,
        'selecting a section keeps the table position')
      assert.ok(Math.abs(await metadataTop() - mobileMetadataTop) < 2,
        'mobile metadata stays in place when switching from a short to a long excerpt')
      await page.evaluate(() => window.scrollTo(0, 0))
      await page.screenshot({ path: `${output}/prepare-light-390-full.png`, fullPage: true, animations: 'disabled' })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
      await page.getByRole('button', { name: 'Toggle theme' }).click()
      await page.setViewportSize({ width: 1440, height: 1000 })
      await page.getByRole('button', { name: 'Rebuild', exact: true }).click()
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
        table.parentElement.scrollWidth > table.parentElement.clientWidth), false,
      'all four Prepare columns fit at a 390px viewport')
      assert.equal(expectedConflictConsole, 1, 'only the intentionally tested 409 appears in console')
      pipeline.active_job = null; pipeline.busy = false
      workspaces.workspaces[0].active_job = null
      delete workspaces.workspaces[0].source_id
      await page.goto('http://localhost/work/workspaces/w-1/prepare')
      await page.locator('[data-ui-debug-id="PCK"]').waitFor()
      const disabledRebuild = page.getByRole('button', { name: 'Rebuild', exact: true })
      const reason = page.locator('#prepare-rebuild-reason')
      assert.equal(await disabledRebuild.isDisabled(), true)
      await reason.getByText('This workspace is not linked to its original Library source, so Prepare cannot rebuild it.').waitFor()
      assert.equal(await disabledRebuild.getAttribute('title'), null, 'the reason is not trapped in an uncopyable native tooltip')
      assert.equal(await disabledRebuild.getAttribute('aria-describedby'), 'prepare-rebuild-reason')
      assert.equal(await reason.evaluate(element => {
        const selection = window.getSelection(), range = document.createRange()
        range.selectNodeContents(element); selection.removeAllRanges(); selection.addRange(range)
        const selected = selection.toString(); selection.removeAllRanges()
        return selected.includes('This workspace is not linked to its original Library source')
      }), true, 'the disabled reason is visible page text that can be selected and copied')
      await page.setViewportSize({ width: 1440, height: 1000 })
      await page.getByRole('button', { name: 'Toggle theme' }).click()
      await reason.scrollIntoViewIfNeeded()
      await page.screenshot({ path: `${output}/rebuild-disabled-dark-1440.png`, animations: 'disabled' })
      await page.setViewportSize({ width: 390, height: 844 })
      await page.getByRole('button', { name: 'Toggle theme' }).click()
      await reason.scrollIntoViewIfNeeded()
      assert.equal(await reason.isVisible(), true)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
      await page.screenshot({ path: `${output}/rebuild-disabled-light-390.png`, animations: 'disabled' })
      workspaces.workspaces[0].source_id = 'book.epub'
      await page.reload()
      await page.locator('#prepare-rebuild-reason').getByText('The server is reconnecting. Wait for a live connection before rebuilding Prepare.').waitFor()
      await page.waitForFunction(() => !!window.testStream?.listeners.snapshot)
      await page.evaluate(() => window.testStream.emitSnapshot())
      await page.locator('.connection-status.live').waitFor({ state: 'attached' })
      await page.locator('#prepare-rebuild-reason').waitFor({ state: 'detached' })
      assert.equal(await page.getByRole('button', { name: 'Rebuild', exact: true }).isEnabled(), true)
      assert.deepEqual(errors, [])
      assert.deepEqual(failed, [])
    } finally { await page.close() }
  } finally { await browser.close() }
})
