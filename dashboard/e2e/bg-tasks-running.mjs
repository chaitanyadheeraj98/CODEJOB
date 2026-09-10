// The running-task states, which the live database only shows for the minute or
// two a job is actually going. Fixtures here so the progress bar, the badge and
// the Stop control can be checked on demand - no writes, no enqueued work.
import { chromium } from 'playwright'
import path from 'node:path'

const BASE_URL = 'http://localhost:5174'
const OUT_DIR = process.argv[2] || './e2e/chat-report'

const STATUS = {
  enabled: true, ollama_running: true, mcp_status: 'ready', model: 'gemma4:31b-cloud',
  ollama_last_error: null, ollama_last_success_at: '2026-09-09T10:04:00Z', chat_last_error: null,
  available_models: ['gemma4:31b-cloud'], telemetry: null,
}
const SESSION = { id: 1, title: 'Live check', created_at: '2026-09-09T10:00:00Z', updated_at: '2026-09-09T10:04:00Z' }
const json = (body) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

const RUNNING = {
  run_key: 'nvoids_client_search:9f2c1a', run_source: 'nvoids_sync', job_id: 'job-live',
  status: 'running', detail: 'Crawling Morgan Stanley postings, page 2 of 5.',
  processed_items: 4, total_items: 10, progress_pct: 40, queue_name: 'nvoids_sync',
  skipped_item_count: 1, failed_count: 0,
  created_at: new Date(Date.now() - 95_000).toISOString(),
}
const QUEUED = {
  ...RUNNING, run_key: 'gmail_sync:queued-1', run_source: 'gmail_sync', job_id: 'job-q',
  status: 'queued', detail: 'Queued on gmail_sync.', processed_items: 0, total_items: 25,
  progress_pct: 0, queue_name: 'gmail_sync', skipped_item_count: 0,
  created_at: new Date(Date.now() - 8_000).toISOString(),
}
const DONE = {
  ...RUNNING, run_key: 'automation_run:done-1', run_source: 'automation_run', job_id: 'job-d',
  status: 'ok', detail: 'Processed 12 unread matching emails: queued=3, skipped=9, failed=0.',
  processed_items: 12, total_items: 12, progress_pct: 100, queue_name: 'automation_run',
  created_at: new Date(Date.now() - 640_000).toISOString(),
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })

let cancelled = null
let items = [RUNNING, QUEUED, DONE]
await page.route((u) => u.pathname.endsWith('/chat/status'), (r) => r.fulfill(json(STATUS)))
await page.route((u) => u.pathname.endsWith('/attachments'), (r) => r.fulfill(json([])))
await page.route((u) => /\/chat\/sessions\/\d+$/.test(u.pathname), (r) => r.fulfill(json({ ...SESSION, messages: [] })))
await page.route((u) => u.pathname.endsWith('/chat/sessions'), (r) => r.fulfill(json([SESSION])))
await page.route((u) => u.pathname.endsWith('/cancel'), (r) => {
  cancelled = new URL(r.request().url()).pathname
  // What the real route returns, and what the next poll would then report.
  items = [{ ...RUNNING, status: 'canceled', detail: 'Background job canceled.' }, QUEUED, DONE]
  return r.fulfill(json(items[0]))
})
await page.route((u) => u.pathname === '/jobs', (r) => r.fulfill(json({
  items, active_count: items.filter((i) => i.status === 'running' || i.status === 'queued').length,
})))

const errors = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)) })
page.on('pageerror', (e) => errors.push('PAGEERROR: ' + String(e).slice(0, 300)))

await page.goto(`${BASE_URL}/?page=assistant`, { waitUntil: 'networkidle' })
await page.waitForTimeout(900)

const report = {}
report.badgeBeforeOpen = await page.locator('.bgTasksBadge').textContent().catch(() => null)
await page.locator('.bgTasksTrigger').first().click()
await page.waitForTimeout(400)
report.summary = await page.locator('.bgTasksPanelHead small').textContent()
report.names = await page.locator('.bgTaskName').allTextContents()
report.statuses = await page.locator('.bgTaskStatus').allTextContents()
report.bars = await page.locator('.bgTaskBar').count()
report.barWidth = await page.locator('.bgTaskBar span').first().evaluate((el) => el.style.width)
await page.screenshot({ path: path.join(OUT_DIR, 'bg-tasks-running.png') })

await page.locator('.bgTaskHead').first().click()
await page.waitForTimeout(300)
report.facts = await page.locator('.bgTaskFacts dd').allTextContents()
report.stopVisible = await page.locator('.bgTaskStop').count()
await page.screenshot({ path: path.join(OUT_DIR, 'bg-tasks-running-expanded.png') })

await page.locator('.bgTaskStop').first().click()
await page.waitForTimeout(900)
report.cancelPath = cancelled
report.statusesAfterStop = await page.locator('.bgTaskStatus').allTextContents()
report.summaryAfterStop = await page.locator('.bgTasksPanelHead small').textContent()
report.stopGoneAfterStop = await page.locator('.bgTaskStop').count()
await page.screenshot({ path: path.join(OUT_DIR, 'bg-tasks-stopped.png') })

// The floating widget is the tighter of the two surfaces - 360x420 at its
// smallest, and it clips its own overflow - so the panel is checked there too.
await page.goto(`${BASE_URL}/?page=needs_review`, { waitUntil: 'networkidle' })
await page.waitForTimeout(700)
await page.locator('.chatLauncher').first().click()
await page.waitForTimeout(700)
await page.locator('.bgTasksTrigger').first().click()
await page.waitForTimeout(500)
report.widget = await page.evaluate(() => {
  const panel = document.querySelector('.bgTasksPanel')
  const shell = document.querySelector('.chatPanel')
  if (!panel || !shell) return null
  const p = panel.getBoundingClientRect(); const s = shell.getBoundingClientRect()
  return {
    panel: { w: Math.round(p.width), h: Math.round(p.height) },
    shell: { w: Math.round(s.width), h: Math.round(s.height) },
    // Negative means the dropdown is being cut off by the widget it lives in.
    roomBelow: Math.round(s.bottom - p.bottom),
    roomLeft: Math.round(p.left - s.left),
  }
})
await page.screenshot({ path: path.join(OUT_DIR, 'bg-tasks-widget.png') })

report.consoleErrors = errors
console.log(JSON.stringify(report, null, 2))
await browser.close()
