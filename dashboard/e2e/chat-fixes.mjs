// Measures the four reported layout defects, before/after.
// Usage: node e2e/chat-fixes.mjs [outDir]
//
// Each check reads geometry rather than eyeballing a screenshot, because all
// four were invisible to a contrast/spacing sweep and only show up as
// positions: a track that did not collapse, a bubble that is not at the edge,
// rows that overlap, and a panel that stops short of the fold.
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const BASE_URL = process.env.QA_BASE_URL || 'http://localhost:5174'
const OUT_DIR = process.argv[2] || './e2e/chat-report'
fs.mkdirSync(OUT_DIR, { recursive: true })

const SESSION = { id: 1, title: 'Compare Sarah and David', created_at: '2026-09-09T10:00:00Z', updated_at: '2026-09-09T10:04:00Z' }
const MESSAGES = [
  { id: 1, role: 'user', content: 'hi', tool_name: null, created_at: '2026-09-09T10:01:00Z' },
  { id: 2, role: 'assistant', content: 'Hello! How can I help you with your job search or CodeJob today?', tool_name: null, created_at: '2026-09-09T10:02:00Z' },
  { id: 3, role: 'user', content: 'write a resume for performance testor profile.', tool_name: null, created_at: '2026-09-09T10:03:00Z' },
  { id: 4, role: 'assistant', content: 'To get started on a professional Performance Tester resume, I will need to gather some specific details from you first. Since we are building this from scratch, I will ask for your information one step at a time.\n\nFirst, could you please provide your **full legal name** and the **contact details** you want on it?', tool_name: null, created_at: '2026-09-09T10:04:00Z' },
]
const STATUS = {
  enabled: true, ollama_running: true, mcp_status: 'ready', model: 'gemma4:31b-cloud',
  ollama_last_error: null, ollama_last_success_at: null, chat_last_error: null,
  available_models: ['gemma4:31b-cloud', 'gpt-oss:120b-cloud', 'nemotron-3-ultra:cloud'],
}
const json = (body) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

async function main() {
  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
  const page = await context.newPage()
  await page.route((u) => u.pathname.endsWith('/chat/status'), (r) => r.fulfill(json(STATUS)))
  await page.route((u) => u.pathname.endsWith('/attachments'), (r) => r.fulfill(json([])))
  await page.route((u) => /\/chat\/sessions\/\d+$/.test(u.pathname), (r) => r.fulfill(json({ ...SESSION, messages: MESSAGES })))
  await page.route((u) => u.pathname.endsWith('/chat/sessions'), (r) => r.fulfill(json([SESSION])))

  const report = {}
  await page.addInitScript(() => localStorage.setItem('codejob.rail.collapsed', '0'))
  await page.goto(`${BASE_URL}/?page=assistant`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)

  // 2 — the user bubble must reach the thread's right edge, not a centred column.
  report.bubbleRightGap = await page.evaluate(() => {
    const bubble = document.querySelector('.assistantMessages .chatBubble.user')
    const list = document.querySelector('.assistantMessages')
    if (!bubble || !list) return null
    const b = bubble.getBoundingClientRect(); const l = list.getBoundingClientRect()
    const pad = parseFloat(getComputedStyle(list).paddingRight)
    return Math.round(l.right - b.right - pad)  // 0 = flush against the padding edge
  })

  // 4 — the workspace must reach the fold.
  report.workspaceBottomGap = await page.evaluate(() => {
    const el = document.querySelector('.assistantWorkspace')
    return el ? Math.round(window.innerHeight - el.getBoundingClientRect().bottom) : null
  })

  await page.screenshot({ path: path.join(OUT_DIR, 'fix-page.png') })

  // 1 — collapsing must move the content's left edge, not just blank the rail.
  const before = await page.evaluate(() => document.querySelector('.gmailShell > :not(.leftRail):not(.railReopen)')?.getBoundingClientRect().left ?? null)
  await page.locator('.railToggle').first().click()
  await page.waitForTimeout(500)
  const after = await page.evaluate(() => document.querySelector('.gmailShell > :not(.leftRail):not(.railReopen)')?.getBoundingClientRect().left ?? null)
  report.contentLeftBefore = Math.round(before)
  report.contentLeftAfter = Math.round(after)
  report.reclaimedPx = Math.round(before - after)
  await page.screenshot({ path: path.join(OUT_DIR, 'fix-collapsed.png') })
  await page.locator('.railReopen').first().click()
  await page.waitForTimeout(400)

  // 3 — export rows must not overlap, in the widget where the header rule bit.
  await page.goto(`${BASE_URL}/?page=needs_review`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(600)
  await page.locator('[aria-label="Open CodeJob assistant chat"]').first().click()
  await page.waitForTimeout(500)
  await page.locator('.exportMenuTrigger').first().click()
  await page.waitForTimeout(250)
  report.exportRows = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('.exportMenuList button')].map((b) => b.getBoundingClientRect())
    const overlaps = rows.some((r, i) => i > 0 && r.top < rows[i - 1].bottom - 0.5)
    return { count: rows.length, heights: rows.map((r) => Math.round(r.height)), overlaps }
  })
  await page.screenshot({ path: path.join(OUT_DIR, 'fix-export-menu.png') })

  fs.writeFileSync(path.join(OUT_DIR, 'fixes.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report, null, 2))
  await browser.close()
}

main().catch((e) => { console.error(e); process.exit(1) })
