// Does the filtered response actually reach the list?
// Logs every /inbox/conversations request AND response, then samples the
// rendered row count over time, so an aborted or overwritten result is visible.
import { chromium } from 'playwright'

const BASE_URL = process.env.QA_BASE_URL || 'http://localhost:5173'
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

const events = []
page.on('request', (r) => {
  if (r.url().includes('/inbox/conversations?')) events.push({ t: Date.now(), kind: 'REQ', url: r.url().split('?')[1] })
})
page.on('response', async (r) => {
  if (!r.url().includes('/inbox/conversations?')) return
  let n = '?'
  try { n = (await r.json()).length } catch { n = 'unreadable' }
  events.push({ t: Date.now(), kind: 'RES', url: r.url().split('?')[1], n })
})
page.on('requestfailed', (r) => {
  if (r.url().includes('/inbox/conversations?')) events.push({ t: Date.now(), kind: 'FAILED', url: r.url().split('?')[1], n: r.failure()?.errorText })
})

await page.goto(`${BASE_URL}/?page=inbox&sort=newest`, { waitUntil: 'domcontentloaded' })
await page.waitForFunction(() => !document.body.innerText.includes('Loading saved settings'), null, { timeout: 60000 }).catch(() => {})
// Wait for the big unfiltered load to finish FIRST, so typing happens against
// a settled list - this is what a real user does.
await page.waitForFunction(() => document.querySelectorAll('.conversationListItem').length > 0, null, { timeout: 90000 }).catch(() => {})
await page.waitForTimeout(3000)
console.log('settled rows before typing:', await page.locator('.conversationListItem').count())
const t0 = Date.now()
events.length = 0

const field = page.locator('.filterSortBar label', { hasText: /gmail label/i }).locator('input').first()
await field.click()
await field.type('RTR Requested', { delay: 60 })

for (let i = 0; i < 10; i++) {
  await page.waitForTimeout(1000)
  const rows = await page.locator('.conversationListItem').count()
  console.log(`t+${i + 1}s  rendered=${rows}`)
}

console.log('\n--- network ---')
for (const e of events) console.log(`  t+${((e.t - t0) / 1000).toFixed(1)}s ${e.kind} ${e.url}${e.n !== undefined ? ` -> ${e.n}` : ''}`)

await browser.close()
