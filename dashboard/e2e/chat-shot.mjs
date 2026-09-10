// Screenshots and contrast/spacing readings for the redesigned chat surfaces.
// Usage: node e2e/chat-shot.mjs [outDir]
//
// Read-only against a mocked API: every request to the backend is fulfilled here
// with a fixture, so this renders the real components with real content and
// never touches the dev database.
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const BASE_URL = process.env.QA_BASE_URL || 'http://localhost:5174'
const OUT_DIR = process.argv[2] || './e2e/chat-report'
fs.mkdirSync(OUT_DIR, { recursive: true })

const SESSION = { id: 1, title: 'Compare Sarah and David', created_at: '2026-09-09T10:00:00Z', updated_at: '2026-09-09T10:04:00Z' }
const REPLY = `## Ranking

**Sarah Chen** leads on the two requirements that actually gate this role.

| Candidate | AWS | Distributed systems | ATS |
| --- | --- | --- | --- |
| Sarah Chen | Strong | Strong | 82 |
| David Ruiz | Moderate | Strong | 71 |

Her main gap is limited direct evidence of Kafka operations — she names it under
tooling but never as something she ran in production.

- Sarah has shipped three services on ECS with Terraform.
- David has deeper Kafka exposure but no infrastructure-as-code evidence.

Record REC-1024 carries the full evidence trail.`

const MESSAGES = [
  { id: 1, role: 'user', content: 'Compare Sarah with David for the Senior Backend Engineer role. Focus on AWS and distributed systems.', tool_name: null, created_at: '2026-09-09T10:01:00Z' },
  { id: 2, role: 'assistant', content: REPLY, tool_name: null, created_at: '2026-09-09T10:02:00Z', answered_by: 'minimax-m3:cloud' },
  { id: 3, role: 'user', content: 'Draft outreach for Sarah.', tool_name: null, created_at: '2026-09-09T10:03:00Z' },
  { id: 4, role: 'assistant', content: 'I can prepare that. It will go on a card for you to approve before anything sends.', tool_name: null, created_at: '2026-09-09T10:04:00Z' },
]

const STATUS = {
  enabled: true, ollama_running: true, mcp_status: 'ready', model: 'gemma4:31b-cloud',
  ollama_last_error: null, ollama_last_success_at: '2026-09-09T10:04:00Z', chat_last_error: null,
  available_models: ['gemma4:31b-cloud', 'gpt-oss:120b-cloud', 'gpt-oss:20b-cloud', 'nemotron-3-nano:30b-cloud', 'nemotron-3-super:cloud', 'nemotron-3-ultra:cloud', 'minimax-m3:cloud'],
  telemetry: { window_days: 7, turns: 34, failed: 2, cancelled: 1, interrupted: 0, failed_over: 3, prompt_tokens: 148230, completion_tokens: 12045, median_duration_ms: 9400, p95_duration_ms: 41200, top_failure_code: 'ollama_rate_limited' },
}

const json = (body) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

async function readings(page) {
  return page.evaluate(() => {
    const parse = (value) => {
      const m = value.match(/[\d.]+/g)?.map(Number) ?? []
      if (value.startsWith('color(')) return m.slice(1, 4).map((c) => c * 255)
      return m.slice(0, 3)
    }
    const lum = (rgb) => {
      const [r, g, b] = rgb.map((c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4 })
      return 0.2126 * r + 0.7152 * g + 0.0722 * b
    }
    const bgOf = (el) => {
      let node = el
      while (node) {
        const bg = getComputedStyle(node).backgroundColor
        if (bg && !bg.includes('rgba(0, 0, 0, 0)') && bg !== 'transparent') return parse(bg)
        node = node.parentElement
      }
      return [255, 255, 255]
    }
    const ratio = (el) => {
      const fg = parse(getComputedStyle(el).color)
      const bg = bgOf(el)
      const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x)
      return Number(((a + 0.05) / (b + 0.05)).toFixed(2))
    }
    const out = {}
    for (const [name, sel] of Object.entries({
      assistantText: '.chatBubble.assistant .chatBubbleBody p',
      userText: '.chatBubble.user .chatBubbleBody',
      answeredBy: '.chatAnsweredBy',
      messageAction: '.messageActions button',
      exportTrigger: '.exportMenuTrigger',
    })) {
      const el = document.querySelector(sel)
      out[name] = el ? { contrast: ratio(el), fontSize: getComputedStyle(el).fontSize } : null
    }
    const assistant = document.querySelector('.chatBubble.assistant')
    const list = document.querySelector('.assistantMessages, .chatMessages')
    out.assistantBoxed = assistant
      ? getComputedStyle(assistant).borderTopWidth !== '0px' || getComputedStyle(assistant).backgroundColor !== 'rgba(0, 0, 0, 0)'
      : null
    out.turnGap = list ? getComputedStyle(list).gap : null
    out.measureCh = assistant ? Math.round(assistant.getBoundingClientRect().width / parseFloat(getComputedStyle(assistant).fontSize) * 2) : null
    return out
  })
}

async function main() {
  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
  const page = await context.newPage()

  // Predicates, not globs: `**/chat/sessions` also swallows `/chat/sessions/1`,
  // which returns a list where the thread detail was expected and renders an
  // empty conversation with no error anywhere.
  await page.route((u) => u.pathname.endsWith('/chat/status'), (r) => r.fulfill(json(STATUS)))
  await page.route((u) => u.pathname.endsWith('/attachments'), (r) => r.fulfill(json([])))
  await page.route((u) => /\/chat\/sessions\/\d+$/.test(u.pathname), (r) => r.fulfill(json({ ...SESSION, messages: MESSAGES })))
  await page.route((u) => u.pathname.endsWith('/chat/sessions'), (r) => r.fulfill(json([SESSION])))
  page.on('console', (m) => { if (m.type() === 'error') console.log('CONSOLE_ERROR:', m.text().slice(0, 200)) })

  const report = {}
  await page.goto(`${BASE_URL}/?page=assistant`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  await page.screenshot({ path: path.join(OUT_DIR, 'assistant-page.png'), fullPage: false })
  report.page = await readings(page)

  // Hover a message so the actions are visible in the capture.
  const bubble = page.locator('.chatBubble.assistant').first()
  if (await bubble.count()) {
    await bubble.hover()
    await page.waitForTimeout(200)
    await page.screenshot({ path: path.join(OUT_DIR, 'assistant-hover-actions.png') })
  }

  const exportBtn = page.locator('.exportMenuTrigger').first()
  if (await exportBtn.count()) {
    await exportBtn.click()
    await page.waitForTimeout(200)
    await page.screenshot({ path: path.join(OUT_DIR, 'assistant-export-menu.png') })
    report.exportItems = await page.locator('.exportMenuList button').allTextContents()
    await page.keyboard.press('Escape')
  }

  // Rail collapsed
  const hide = page.locator('.railToggle').first()
  if (await hide.count()) {
    await hide.click()
    await page.waitForTimeout(300)
    await page.screenshot({ path: path.join(OUT_DIR, 'assistant-rail-collapsed.png') })
    report.railCollapsed = await page.locator('.leftRail.collapsed').count() === 1
    await page.locator('.railReopen').first().click()
    await page.waitForTimeout(250)
  }

  // Widget, on another page, resized
  await page.goto(`${BASE_URL}/?page=needs_review`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(600)
  const launcher = page.locator('[aria-label="Open CodeJob assistant chat"]').first()
  if (await launcher.count()) {
    await launcher.click()
    await page.waitForTimeout(500)
    await page.screenshot({ path: path.join(OUT_DIR, 'widget-default.png') })
    report.widget = await readings(page)
    const handle = page.locator('.chatResizeHandle').first()
    if (await handle.count()) {
      const box = await handle.boundingBox()
      await page.mouse.move(box.x + 8, box.y + 8)
      await page.mouse.down()
      await page.mouse.move(box.x - 220, box.y - 140, { steps: 12 })
      await page.mouse.up()
      await page.waitForTimeout(250)
      await page.screenshot({ path: path.join(OUT_DIR, 'widget-resized.png') })
      report.resizedTo = await page.locator('.chatPanel').first().boundingBox()
    }
  }

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${BASE_URL}/?page=assistant`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(700)
  await page.screenshot({ path: path.join(OUT_DIR, 'assistant-mobile.png'), fullPage: false })

  fs.writeFileSync(path.join(OUT_DIR, 'report.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report, null, 2))
  await browser.close()
}

main().catch((error) => { console.error(error); process.exit(1) })
