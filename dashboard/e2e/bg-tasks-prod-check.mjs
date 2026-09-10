// End-to-end check against the running stack: the container-served dashboard on
// :5173 talking to the real backend on :8000. Nothing is stubbed and nothing is
// written - it opens the Assistant, opens the panel, and reports what rendered.
import { chromium } from 'playwright'
import path from 'node:path'

const BASE_URL = process.env.QA_BASE_URL || 'http://localhost:5173'
const OUT_DIR = process.argv[2] || './e2e/chat-report'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })

const errors = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)) })
page.on('pageerror', (e) => errors.push('PAGEERROR: ' + String(e).slice(0, 300)))

const T0 = Date.now()
const calls = []
page.on('response', async (r) => {
  const u = new URL(r.url())
  if (u.pathname === '/jobs' || u.pathname.endsWith('/cancel')) calls.push(`${Date.now() - T0}ms ${r.status()} ${u.pathname}`)
})

await page.goto(`${BASE_URL}/?page=assistant`, { waitUntil: 'networkidle' })
await page.waitForTimeout(2000)

const report = { url: BASE_URL }
report.triggerPresent = await page.locator('.bgTasksTrigger').count()
if (report.triggerPresent) {
  report.triggerText = await page.locator('.bgTasksTrigger').first().textContent()
  await page.locator('.bgTasksTrigger').first().click()
  await page.waitForTimeout(1200)
  report.heading = await page.locator('.bgTasksPanelHead strong').textContent().catch(() => null)
  report.summary = await page.locator('.bgTasksPanelHead small').textContent().catch(() => null)
  report.rows = await page.locator('.bgTask').count()
  report.firstNames = (await page.locator('.bgTaskName').allTextContents()).slice(0, 4)
  report.firstStatuses = (await page.locator('.bgTaskStatus').allTextContents()).slice(0, 4)
  report.panelError = await page.locator('.bgTaskError').first().textContent().catch(() => null)
  // Any raw snake_case reaching the screen is the defect the live data caught.
  report.rawTokensOnScreen = (await page.locator('.bgTaskStatus').allTextContents()).filter((t) => /_/.test(t))
  await page.screenshot({ path: path.join(OUT_DIR, 'bg-tasks-prod.png') })
}
report.jobsCalls = calls
report.consoleErrors = errors
console.log(JSON.stringify(report, null, 2))
await browser.close()
