import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { cp, readFile, writeFile } from 'node:fs/promises'
import test from 'node:test'
import { chromium } from 'playwright'

const LONG = 'A Remarkably Long Title That Needs Far More Than Three Lines To Be Shown In Full On A Single Library Card With Many Words'

test('Library cards share one rigid size: language on the cover, clamped title, full author at the bottom', { timeout: 90000 }, async t => {
  const server = spawn('uv', ['run', '--group', 'dev', 'python', 'tests/web_fixture_server.py'], { cwd: '..', stdio: ['pipe', 'pipe', 'pipe'] })
  let serverError = ''
  server.stderr.on('data', value => { serverError += value })
  t.after(async () => { server.stdin.end('quit\n'); await new Promise(resolve => { server.once('exit', resolve); setTimeout(() => { server.kill('SIGTERM'); resolve() }, 3000).unref() }) })
  const config = await new Promise((resolve, reject) => {
    createInterface({ input: server.stdout }).on('line', line => { try { const value = JSON.parse(line); if (value.url) resolve(value) } catch {} })
    server.once('exit', () => reject(new Error(serverError)))
  })
  for (const [name, title, author] of [['Short', 'Dracula', 'Bram Stoker'], ['Long title', LONG, 'Mary Wollstonecraft Shelley'], ['Two lines', 'The Adventures of Sherlock Holmes', 'Arthur Conan Doyle']]) {
    const destination = `${config.root}/sources/${name}`
    await cp(`${config.root}/sources/epub-source`, destination, { recursive: true })
    const opf = `${destination}/EPUB/package.opf`
    await writeFile(opf, (await readFile(opf, 'utf8')).replace('Relay Book', title).replace('Test Author', author))
  }
  const browser = await chromium.launch(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {})
  t.after(() => browser.close())
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' })
  await page.route('**/favicon.ico', route => route.fulfill({ status: 204 }))
  await page.goto(`${config.url}/work`)
  await page.getByRole('heading', { name: 'Library', exact: true }).scrollIntoViewIfNeeded()
  await page.locator('.book-card[data-source-id="Long title"]').waitFor()

  const cards = await page.locator('.book-card').evaluateAll(nodes => nodes.map(card => {
    const box = element => element.getBoundingClientRect()
    const cover = card.querySelector('.cover'), badge = card.querySelector('.cover-language'), name = card.querySelector('.book-name'), author = card.querySelector('.book-author')
    let textOffset = null
    if (badge) { const range = document.createRange(); range.selectNodeContents(badge); const text = range.getBoundingClientRect(); textOffset = (text.left + text.width / 2) - (box(badge).left + box(badge).width / 2) }
    const lineHeight = parseFloat(getComputedStyle(name).lineHeight)
    return {
      id: card.dataset.sourceId, height: Math.round(box(card).height), badge: badge?.textContent ?? null, textOffset,
      badgeLeft: badge ? box(badge).left - box(cover).left : null, badgeTop: badge ? box(badge).top - box(cover).top : null,
      titleLines: Math.round(box(name).height / lineHeight), titleClamped: name.scrollHeight > name.clientHeight + 1, titleTooltip: name.getAttribute('title'),
      authorInside: box(author).bottom <= box(card).bottom, authorClipped: author.scrollHeight > author.clientHeight + 1, authorText: author.textContent,
    }
  }))
  assert.ok(cards.length >= 4)
  assert.equal(new Set(cards.map(card => card.height)).size, 1, `every card has the same height: ${JSON.stringify(cards.map(card => card.height))}`)
  for (const card of cards) {
    assert.equal(card.badge, 'EN', `${card.id}: language badge`)
    assert.ok(card.badgeLeft >= 0 && card.badgeLeft <= 12 && card.badgeTop >= 0 && card.badgeTop <= 12, `${card.id}: badge sits in the cover's top-left corner`)
    assert.ok(Math.abs(card.textOffset) <= 1, `${card.id}: badge text is centered (${card.textOffset})`)
    assert.ok(card.titleLines <= 3, `${card.id}: title uses at most three lines`)
    assert.ok(card.authorInside && !card.authorClipped, `${card.id}: author is shown in full`)
  }
  // The dim secondary color blends into the dark card, so the dark theme uses the softer-but-brighter one.
  const authorColor = () => page.evaluate(() => {
    const probe = name => { const el = document.createElement('i'); el.style.color = `var(--${name})`; document.body.append(el); const value = getComputedStyle(el).color; el.remove(); return value }
    return { author: getComputedStyle(document.querySelector('.book-author')).color, soft: probe('text-soft'), dim: probe('text-dim') }
  })
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark' })
  let colors = await authorColor()
  assert.equal(colors.author, colors.soft, 'dark theme: author uses the brighter secondary color')
  for (const theme of ['light', 'sepia']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value }, theme)
    colors = await authorColor()
    assert.equal(colors.author, colors.dim, `${theme} theme: author keeps the dim secondary color`)
  }
  const long = cards.find(card => card.id === 'Long title')
  assert.equal(long.titleClamped, true, 'an over-long title is clamped')
  assert.equal(long.titleTooltip, LONG, 'the tooltip carries the full title')
  assert.equal(long.authorText, 'Mary Wollstonecraft Shelley')
  assert.equal(cards.find(card => card.id === 'Short').titleClamped, false)
})
