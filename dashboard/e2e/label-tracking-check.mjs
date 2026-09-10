// End-to-end check of Gmail label tracking against the running stack.
// Usage: node e2e/label-tracking-check.mjs [outDir]
//
// Unlike chat-shot.mjs this does NOT mock the API: the point is to prove the
// real backend, the real Gmail sync and the real database agree with what the
// UI draws. Read-only on the UI side - it clicks filters and tabs, never a
// promote or a send.
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const BASE_URL = process.env.QA_BASE_URL || 'http://localhost:5173'
const OUT_DIR = process.argv[2] || './e2e/label-report'
fs.mkdirSync(OUT_DIR, { recursive: true })

const results = []
const record = (name, pass, detail) => {
  results.push({ name, pass, detail })
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? ` - ${detail}` : ''}`)
}
const shot = async (page, name) => page.screenshot({ path: path.join(OUT_DIR, `${name}.png`), fullPage: false })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

try {
  // 1. Settings: the catalog renders, and only user labels are offered.
  await page.goto(`${BASE_URL}/?page=settings`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)
  const fieldset = page.locator('fieldset', { hasText: 'Gmail label tracking' }).first()
  await fieldset.scrollIntoViewIfNeeded().catch(() => {})
  const labelRows = await fieldset.locator('label.toggleRow').allInnerTexts()
  const offered = labelRows.map((t) => t.trim()).filter(Boolean)
  record('settings lists the user labels', offered.some((t) => t.includes('RTR Requested')), offered.join(' | ').slice(0, 120))
  const systemOffered = offered.filter((t) => ['SENT', 'TRASH', 'INBOX', 'SPAM', 'DRAFT'].some((s) => t.trim() === s))
  record('settings hides Gmail system labels', systemOffered.length === 0, systemOffered.join(',') || 'none offered')
  const checked = await fieldset.locator('input[type=checkbox]:checked').count()
  record('a label is ticked as tracked', checked >= 1, `${checked} ticked`)
  await shot(page, '1-settings-labels')

  // 2. Inbox: label filter is present and narrows the list.
  await page.goto(`${BASE_URL}/?page=inbox&sort=newest`, { waitUntil: 'domcontentloaded' })
  await page.waitForFunction(() => !document.body.innerText.includes('Loading saved settings'), null, { timeout: 60000 }).catch(() => {})
  await page.waitForTimeout(2500)
  const filterLabels = (await page.locator('.filterSortBar label span').allInnerTexts()).map((t) => t.trim())
  record('inbox exposes a Gmail label filter', filterLabels.some((t) => /gmail label/i.test(t)), filterLabels.join(', ').slice(0, 120))
  const syncBtn = page.getByRole('button', { name: 'Sync labels' })
  record('inbox has the Sync labels action', await syncBtn.count() > 0)
  await shot(page, '2-inbox')

  // The unfiltered load is the slow one; measuring before it lands makes the
  // baseline 0 and the comparison below meaningless.
  await page.waitForFunction(() => document.querySelectorAll('.conversationListItem').length > 0, null, { timeout: 90000 }).catch(() => {})
  const unfiltered = await page.locator('.conversationListItem').count()
  record('inbox loaded unfiltered conversations', unfiltered > 0, `${unfiltered} total`)

  const labelField = page.locator('.filterSortBar label', { hasText: /gmail label/i }).locator('input').first()
  if (await labelField.count()) {
    await labelField.click()
    await labelField.type('RTR Requested', { delay: 60 })
    // The combobox debounces, then the list refetches. Both have to settle or
    // the count reads the placeholder rather than the result.
    // No Escape here: it closes the combobox by reverting the typed value, so
    // the filter never applies and the list stays at its unfiltered size.
    // Wait for the count to actually change rather than for a fixed delay - the
    // combobox debounce means the request has not even been sent yet at 1s.
    await page.waitForFunction(
      (before) => document.querySelectorAll('.conversationListItem').length !== before,
      unfiltered,
      { timeout: 60000 },
    ).catch(() => {})
    await page.waitForTimeout(1500)
    const rows = await page.locator('.conversationListItem').count()
    const badges = await page.locator('.conversationListItem .statusBadge').allInnerTexts()
    // Narrowed, not merely non-empty: the slow unfiltered load used to land
    // second and repaint every conversation, and `rows > 0` called that a pass.
    record('inbox filters by label', rows > 0 && rows < unfiltered, `${rows} of ${unfiltered} conversations`)
    record('conversations carry the label badge', badges.some((t) => /RTR Requested/i.test(t)), [...new Set(badges)].join(', ').slice(0, 80))
    record('external threads are marked', badges.some((t) => /Externally tracked/i.test(t)), 'origin badge')
    await shot(page, '3-inbox-filtered')
  }

  // 3. ATS: the third tab lists the labeled threads.
  await page.goto(`${BASE_URL}/?page=application_tracking&tab=labels&sort=newest`, { waitUntil: 'domcontentloaded' })
  // The shell renders before settings load, and the tab paints "Loading
  // labeled threads..." until its own fetch lands. Waiting a fixed 2s read that
  // placeholder as an empty result.
  await page.waitForFunction(() => !document.body.innerText.includes('Loading labeled threads'), null, { timeout: 60000 }).catch(() => {})
  await page.waitForTimeout(1500)
  const tab = page.getByRole('tab', { name: 'From Gmail Labels' })
  record('ATS shows the From Gmail Labels tab', await tab.count() > 0)
  if (await tab.count()) await tab.click().catch(() => {})
  await page.waitForTimeout(2000)
  const body = await page.locator('body').innerText()
  const empty = /No labeled threads match these filters/i.test(body)
  const countMatch = body.match(/(\d+)\s+threads?/i)
  record('ATS lists labeled threads', !empty && !!countMatch && Number(countMatch[1]) > 0, empty ? 'EMPTY STATE shown' : (countMatch?.[0] ?? 'no count'))
  record('a real RTR subject is rendered', /Rate Confirmation|RTR|Contract Role/i.test(body), body.slice(0, 0) || '')
  await shot(page, '4-ats-labels')

  record('no console errors', errors.length === 0, errors.slice(0, 2).join(' | ') || 'clean')
} finally {
  fs.writeFileSync(path.join(OUT_DIR, 'results.json'), JSON.stringify(results, null, 2))
  await browser.close()
}

const failed = results.filter((r) => !r.pass)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
