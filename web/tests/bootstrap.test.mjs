import assert from 'node:assert/strict'
import { once } from 'node:events'
import test from 'node:test'
import { chromium } from 'playwright'
import { preview } from 'vite'

test('production bootstrap mounts React and the root route with compiled Tailwind', async (t) => {
  const server = await preview({ preview: { port: 0, open: false } })
  t.after(async () => {
    const closed = once(server.httpServer, 'close')
    server.httpServer.close()
    server.httpServer.closeAllConnections()
    await closed
  })
  const browser = await chromium.launch()
  t.after(() => browser.close())
  const page = await browser.newPage()
  const errors = []
  const apiRequests = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api')) apiRequests.push(request.url())
  })
  const address = server.httpServer.address()
  await page.goto(`http://127.0.0.1:${address.port}`, { waitUntil: 'networkidle' })
  const marker = page.locator('#root [data-bootstrap="ready"]')
  await marker.waitFor({ state: 'attached' })
  assert.equal(await marker.evaluate((node) => getComputedStyle(node).display), 'none')
  assert.equal(await page.locator('body').innerText(), '')
  assert.deepEqual(apiRequests, [])
  assert.deepEqual(errors, [])
})
