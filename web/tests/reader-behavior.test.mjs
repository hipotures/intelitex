import { testEvidence } from './paths.mjs'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { mkdir } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'

const evidence = testEvidence('intelitex-reader-evidence')
const wait = async (fn, description) => {
  const end = Date.now() + 30000
  while (Date.now() < end) {
    if (await fn()) return
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  throw new Error(`Timed out: ${description}`)
}

test('Reader restores position, ignores vertical scroll gestures, and anchors marker details', { timeout: 90000 }, async t => {
  await mkdir(evidence, { recursive: true })
  const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'], { cwd: '..', stdio: ['pipe', 'pipe', 'pipe'] })
  let serverError = ''
  server.stderr.on('data', value => { serverError += value })
  t.after(async () => {
    server.stdin.end('quit\n')
    await new Promise(resolve => { server.once('exit', resolve); setTimeout(() => { server.kill('SIGTERM'); resolve() }, 3000).unref() })
  })
  const lines = createInterface({ input: server.stdout })
  const config = await new Promise((resolve, reject) => {
    lines.on('line', line => { try { const value = JSON.parse(line); if (value.url) resolve(value) } catch { /* Wait for fixture readiness. */ } })
    server.once('exit', () => reject(new Error(serverError)))
  })
  const browser = await chromium.launch(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {})
  t.after(() => browser.close())
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
  const page = await context.newPage()
  page.setDefaultTimeout(15000)
  const pageErrors = [], consoleErrors = [], failed = [], markerPosts = []
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  page.on('response', response => { if (response.status() >= 400) failed.push(`${response.status()} ${response.url()}`) })
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/reader/markers')) markerPosts.push(request.url()) })
  const setTheme = async theme => {
    for (let i = 0; i < 3; i++) {
      if (await page.locator('html').getAttribute('data-theme') === theme) return
      await page.getByRole('button', { name: /^Theme: .*\. Switch theme$/ }).click()
    }
    throw new Error(`Could not select ${theme} theme`)
  }

  await page.goto(config.url + '/work')
  assert.ok((await page.title()).length > 0)
  await page.getByRole('heading', { name: 'Books in progress' }).waitFor()
  await page.locator('.workspace-title').first().click()
  await page.getByRole('button', { name: 'Run', exact: true }).click()
  await wait(async () => page.getByRole('button', { name: 'Open Review', exact: true }).isVisible(), 'Review gate')
  await page.getByRole('button', { name: 'Open Review', exact: true }).click()
  await page.getByRole('button', { name: 'Review individually & next', exact: true }).click()
  await page.getByRole('button', { name: 'Approve glossary', exact: true }).click()
  await page.getByRole('dialog', { name: 'Approve glossary?' }).getByRole('button', { name: 'Approve selected forms' }).click()
  await page.getByText('✓ Glossary approved', { exact: true }).waitFor()
  await page.getByRole('link', { name: 'Workspace', exact: true }).click()
  await page.getByRole('button', { name: 'Run', exact: true }).click()
  await page.getByRole('button', { name: 'Reader', exact: true }).click()
  await page.reload()
  await wait(async () => {
    const response = await context.request.get(config.url + '/api/workspaces/prepared/pipeline')
    return (await response.json()).publication.current
  }, 'offline publication')
  await page.locator('.reader-reading-area').evaluate(area => area.scrollTo({ top: 0 }))
  await page.getByRole('button', { name: 'Refresh availability', exact: true }).click()
  const paragraph = page.locator('.reader-page p').filter({ hasText: 'Translated: Relay-A moved steadily in section 1.' })
  await paragraph.waitFor()
  assert.equal(new URL(page.url()).pathname, '/reader/prepared')
  assert.equal(await page.locator('.reader-page').count(), 1)
  assert.equal(await page.locator('vite-error-overlay').count(), 0)
  await page.screenshot({ path: evidence + '/reader-dark-1440.png', fullPage: false, animations: 'disabled' })

  await paragraph.evaluate(node => {
    const range = document.createRange()
    range.setStart(node.firstChild, 12)
    range.setEnd(node.firstChild, 19)
    const selection = window.getSelection()
    selection.removeAllRanges()
    selection.addRange(range)
    node.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
  })
  await page.getByRole('button', { name: 'Mark selection', exact: true }).click()
  await page.getByRole('complementary', { name: 'Marker details' }).waitFor()
  const markerGap = await paragraph.evaluate(node => {
    const range = document.createRange()
    range.setStart(node.firstChild, 12)
    range.setEnd(node.firstChild, 19)
    const tag = Array.from(range.getClientRects()).at(-1)
    const panel = document.querySelector('.reader-marker-popover').getBoundingClientRect()
    return { gap: panel.top - tag.bottom, line: parseFloat(getComputedStyle(node).lineHeight) }
  })
  assert.ok(Math.abs(markerGap.gap - 2 * markerGap.line) < 3, `marker panel gap ${markerGap.gap} should equal two lines`)
  await page.screenshot({ path: evidence + '/marker-dark-1440.png', fullPage: false, animations: 'disabled' })
  await setTheme('light')
  await page.screenshot({ path: evidence + '/marker-light-1440.png', fullPage: false, animations: 'disabled' })
  await setTheme('dark')

  const scrollingPreview = await paragraph.evaluate(node => {
    const box = node.getBoundingClientRect(), x = box.left + 30, y = box.top + 15
    const send = (type, cx, cy) => node.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 51, pointerType: 'touch', button: 0, clientX: cx, clientY: cy }))
    send('pointerdown', x, y)
    const onDown = !!document.querySelector('.reader-range-rect.preview')
    send('pointermove', x + 4, y + 42)
    document.querySelector('.reader-reading-area').dispatchEvent(new Event('scroll'))
    const onMove = !!document.querySelector('.reader-range-rect.preview')
    send('pointerup', x + 4, y + 42)
    return [onDown, onMove, !!document.querySelector('.reader-range-rect.preview')]
  })
  assert.deepEqual(scrollingPreview, [false, false, false])
  assert.equal(markerPosts.length, 1, 'vertical scroll did not add another marker')

  await page.setViewportSize({ width: 390, height: 400 })
  await page.screenshot({ path: evidence + '/marker-dark-390.png', fullPage: false, animations: 'disabled' })
  await setTheme('light')
  const mobileMarker = await paragraph.evaluate(node => {
    const range = document.createRange()
    range.setStart(node.firstChild, 12)
    range.setEnd(node.firstChild, 19)
    const tag = Array.from(range.getClientRects()).at(-1)
    const panel = document.querySelector('.reader-marker-popover').getBoundingClientRect()
    return { tagTop: tag.top, tagBottom: tag.bottom, panelTop: panel.top, panelBottom: panel.bottom,
      controlsBottom: document.querySelector('.reader-progress-line').getBoundingClientRect().bottom,
      line: parseFloat(getComputedStyle(node).lineHeight) }
  })
  assert.ok(mobileMarker.tagTop >= mobileMarker.controlsBottom - 2, 'tag stays visible below mobile controls')
  assert.ok(mobileMarker.panelBottom <= 401, 'marker details fit in the mobile viewport')
  assert.ok(Math.abs(mobileMarker.panelTop - mobileMarker.tagBottom - 2 * mobileMarker.line) < 3, 'mobile marker stays two lines below the tag')
  await page.screenshot({ path: evidence + '/marker-light-390.png', fullPage: false, animations: 'disabled' })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
  const scrollPosition = await page.locator('.reader-reading-area').evaluate(area => {
    area.scrollTop = area.scrollHeight - area.clientHeight
    area.dispatchEvent(new Event('scroll'))
    return { top: area.scrollTop, max: area.scrollHeight - area.clientHeight }
  })
  assert.ok(scrollPosition.top > 0 && scrollPosition.max > 0, 'mobile Reader is scrollable')
  await page.goto(config.url + '/work')
  const storedAnchor = await page.evaluate(() => Object.entries(localStorage).find(([key]) => key.includes('.prepared.') && key.endsWith('.anchor'))?.[1])
  assert.ok(storedAnchor && JSON.parse(storedAnchor).block_id, 'position was saved during navigation')
  await page.goto(config.url + '/reader/prepared')
  await paragraph.waitFor()
  const restoredTop = await page.locator('.reader-reading-area').evaluate(area => area.scrollTop)
  assert.ok(Math.abs(restoredTop - scrollPosition.top) <= 48, `position restored near ${scrollPosition.top}px (actual ${restoredTop}px)`)
  await page.screenshot({ path: evidence + '/restored-light-390.png', fullPage: false, animations: 'disabled' })

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
  t.after(() => mobileContext.close())
  const mobilePage = await mobileContext.newPage()
  mobilePage.on('pageerror', error => pageErrors.push(error.message))
  mobilePage.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  mobilePage.on('response', response => { if (response.status() >= 400) failed.push(`${response.status()} ${response.url()}`) })
  await mobilePage.goto(config.url + '/reader/prepared')
  await mobilePage.locator('.reader-page p').first().waitFor()
  assert.equal(await mobilePage.evaluate(() => matchMedia('(max-width: 720px) and (pointer: coarse)').matches), true)
  await mobilePage.getByRole('button', { name: 'Reader settings' }).click()
  await mobilePage.getByLabel('Header auto-hide').selectOption('5')
  await mobilePage.waitForFunction(() => document.fullscreenElement === document.documentElement)
  await mobilePage.screenshot({ path: evidence + '/mobile-fullscreen-settings.png', fullPage: false, animations: 'disabled' })
  await mobilePage.mouse.click(20, 650)
  await mobilePage.waitForFunction(() => document.documentElement.classList.contains('reader-chrome-hidden'), null, { timeout: 7000 })
  assert.equal(await mobilePage.evaluate(() => document.fullscreenElement === document.documentElement), true, 'browser remains fullscreen when Reader controls hide')
  await mobilePage.screenshot({ path: evidence + '/mobile-fullscreen-hidden.png', fullPage: false, animations: 'disabled' })
  await mobilePage.locator('.reader-progress-line').evaluate(button => { button.click(); button.click() })
  await mobilePage.getByRole('button', { name: 'Reader settings' }).click()
  await mobilePage.getByLabel('Header auto-hide').selectOption('0')
  await mobilePage.waitForFunction(() => document.fullscreenElement === null)
  await mobilePage.getByLabel('Header auto-hide').selectOption('5')
  await mobilePage.waitForFunction(() => document.fullscreenElement === document.documentElement)
  await mobilePage.getByRole('button', { name: 'Work', exact: true }).click()
  await mobilePage.waitForFunction(() => document.fullscreenElement === null)
  await mobilePage.getByRole('button', { name: 'Reader', exact: true }).click()
  await mobilePage.locator('.reader-page p').first().waitFor()
  assert.equal(await mobilePage.evaluate(() => document.fullscreenElement), null, 'restored preference waits for a Reader tap')
  await mobilePage.locator('.reader-page p').first().click()
  await mobilePage.waitForFunction(() => document.fullscreenElement === document.documentElement)
  await mobilePage.getByRole('button', { name: 'Work', exact: true }).click()
  await mobilePage.waitForFunction(() => document.fullscreenElement === null)

  assert.deepEqual(pageErrors, [])
  assert.deepEqual(failed.filter(item => !item.endsWith('/favicon.ico')), [])
  assert.deepEqual(consoleErrors.filter(item => !item.includes('404 (Not Found)')), [])
  console.log(`Reader browser evidence: ${evidence}; page errors: ${pageErrors.length}; non-favicon HTTP errors: ${failed.filter(item => !item.endsWith('/favicon.ico')).length}`)
})
