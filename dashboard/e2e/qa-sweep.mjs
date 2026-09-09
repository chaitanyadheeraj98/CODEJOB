// Automated read-safe QA sweep across every dashboard page/sub-tab.
// Usage: node e2e/qa-sweep.mjs [outDir]
//
// Safety: every non-GET request to the API is intercepted, logged, and fulfilled with a
// generic mock response instead of being allowed to reach the real backend. This lets us
// verify which requests a click *would* fire (method/url/payload) without mutating the
// user's live dev data or sending real email through Gmail.
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const BASE_URL = process.env.QA_BASE_URL || 'http://localhost:5173'
const API_BASE = process.env.QA_API_BASE || 'http://localhost:8000'
const OUT_DIR = process.argv[2] || process.env.QA_OUT_DIR || './e2e/qa-report'
const SCREEN_DIR = path.join(OUT_DIR, 'screenshots')
fs.mkdirSync(SCREEN_DIR, { recursive: true })

const PAGES = [
  { key: 'run_queue', tab: null, label: 'Run Queue', registry: false },
  { key: 'needs_review', tab: null, label: 'Needs Review', registry: true },
  { key: 'failed_mapping', tab: null, label: 'Failed Mapping', registry: true },
  { key: 'premium_numbers', tab: 'inventory', label: 'Premium Contacts / Number Inventory', registry: true },
  { key: 'premium_numbers', tab: 'opportunities', label: 'Premium Contacts / Recruiter Opportunities', registry: true },
  { key: 'application_tracking', tab: 'bookmarked', label: 'Application Tracking / Bookmarked', registry: true },
  { key: 'application_tracking', tab: 'tracked', label: 'Application Tracking / Tracked', registry: true },
  { key: 'resume_tracking', tab: 'resumes', label: 'Resume Tracking / Resumes', registry: true },
  { key: 'resume_tracking', tab: 'submissions', label: 'Resume Tracking / Submissions', registry: true },
  { key: 'resume_tracking', tab: 'editor', label: 'Resume Tracking / Editor', registry: false },
  { key: 'sent_items', tab: null, label: 'Sent Items', registry: true },
  { key: 'inbox', tab: null, label: 'Inbox', registry: true },
  { key: 'recent_runs', tab: null, label: 'Recent Runs', registry: false },
  { key: 'settings', tab: null, label: 'Settings', registry: false },
]

function slug(p) { return `${p.key}${p.tab ? `-${p.tab}` : ''}` }

async function main() {
  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  page.setDefaultTimeout(8000)

  const mutatingCalls = []
  let consoleErrors = []
  let pageErrors = []
  let dialogs = []

  page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()) })
  page.on('pageerror', (err) => pageErrors.push(String(err)))
  page.on('dialog', async (dialog) => { dialogs.push({ type: dialog.type(), message: dialog.message() }); await dialog.dismiss().catch(() => {}) })

  // Endpoints that are safe to let through for real: read-state toggles with no external
  // side effect (no email sent, nothing deleted, trivially re-toggleable). Mocking these
  // produces false-positive crashes downstream when the UI updates state from the response.
  const SAFE_TO_PASS_THROUGH = [/\/inbox\/conversations\/\d+\/read$/]

  await context.route(`${API_BASE}/**`, async (route) => {
    const req = route.request()
    if (req.method() === 'GET') { await route.continue(); return }
    if (SAFE_TO_PASS_THROUGH.some((re) => re.test(new URL(req.url()).pathname))) { await route.continue(); return }
    let body = null
    try { body = req.postData() } catch { /* ignore */ }
    mutatingCalls.push({ method: req.method(), url: req.url(), body, atPage: page.url() })
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  const results = []

  const visitPage = async (p) => {
    const before = mutatingCalls.length
    consoleErrors = []
    pageErrors = []
    dialogs = []
    const url = `${BASE_URL}/?page=${p.key}${p.tab ? `&tab=${p.tab}` : ''}`
    const entry = { key: p.key, tab: p.tab, label: p.label, registry: p.registry, url, checks: {}, interactions: {} }

    try {
      await page.goto(url, { waitUntil: 'load', timeout: 20000 })
    } catch (e) {
      entry.navError = String(e)
    }
    await page.waitForTimeout(900)

    entry.checks.sidebarActiveLabel = await page.locator('.navItem.active span').first().textContent().catch(() => null)
    entry.checks.pageHeading = await page.locator('.titleBlock h1, h2').first().textContent().catch(() => null)
    entry.checks.filterBarCount = await page.locator('.filterSortBar').count()
    entry.checks.filterBarDisabled = (await page.locator('.filterSortBar--disabled').count()) > 0
    entry.checks.filterFieldLabels = await page.locator('.filterSortBar > span > label > span, .filterSortBar > label > span').allTextContents().catch(() => [])
    entry.checks.errorBannerText = await page.locator('.errorMessage, .errorBanner').first().textContent().catch(() => null)

    await page.screenshot({ path: path.join(SCREEN_DIR, `${slug(p)}-desktop.png`), fullPage: true }).catch((e) => { entry.desktopScreenshotError = String(e) })

    await page.setViewportSize({ width: 390, height: 844 })
    await page.waitForTimeout(250)
    entry.checks.mobileHorizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 4).catch(() => null)
    await page.screenshot({ path: path.join(SCREEN_DIR, `${slug(p)}-mobile.png`), fullPage: true }).catch((e) => { entry.mobileScreenshotError = String(e) })
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.waitForTimeout(250)

    // ---- Page-specific interactions ----
    try {
      if (p.key === 'needs_review') {
        entry.interactions.cardCount = await page.locator('[data-email-search-section="needs_review"]').count()
        entry.interactions.badgeCount = await page.locator('[aria-label="Candidate status badges"] .statusBadge').count()
        const viewDetailsBtn = page.getByRole('button', { name: 'View Details' }).first()
        if (await viewDetailsBtn.count()) {
          const beforeReq = mutatingCalls.length
          await viewDetailsBtn.click()
          await page.waitForTimeout(600)
          entry.interactions.detailsExpanded = (await page.locator('.parserDetailsSummaryGrid').count()) > 0
          entry.interactions.detailsTriggeredMutatingCall = mutatingCalls.length > beforeReq
          const hideBtn = page.getByRole('button', { name: 'Hide Details' }).first()
          if (await hideBtn.count()) await hideBtn.click()
        }
        const checkbox = page.locator('.emailItemCheckbox').first()
        if (await checkbox.count()) {
          await checkbox.check()
          await page.waitForTimeout(200)
          entry.interactions.selectionBarVisible = (await page.locator('.selectionBar').count()) > 0
          entry.interactions.selectionBarActions = await page.locator('.selectionBar .selectionActions button').allTextContents().catch(() => [])
          await checkbox.uncheck()
        }
      }
      if (p.key === 'failed_mapping') {
        entry.interactions.cardCount = await page.locator('[data-email-search-section="failed_mapping"]').count()
      }
      if (p.key === 'sent_items') {
        entry.interactions.cardCount = await page.locator('[data-email-search-section="sent_items"]').count()
        const viewDetailsBtn = page.getByRole('button', { name: 'View Details' }).first()
        if (await viewDetailsBtn.count()) {
          await viewDetailsBtn.click()
          await page.waitForTimeout(600)
          entry.interactions.detailsExpanded = (await page.locator('.parserDetailsSummaryGrid').count()) > 0
        }
      }
      if (p.key === 'premium_numbers' && p.tab === 'inventory') {
        entry.interactions.tableHeaders = await page.locator('.inventoryTable thead th').allTextContents().catch(() => [])
        entry.interactions.rowCount = await page.locator('.inventoryTable tbody tr').count()
        const moreFiltersToggle = page.locator('.filterSortMoreToggle summary')
        if (await moreFiltersToggle.count()) await moreFiltersToggle.click()
        const domainInput = page.locator('.filterSortBar label:has-text("Domain") input')
        if (await domainInput.count()) {
          await domainInput.fill('example')
          await page.waitForTimeout(500)
          entry.interactions.domainFilterFiredRequest = true
          await domainInput.fill('')
          await page.waitForTimeout(500)
        }
        const favSelect = page.locator('.filterSortBar label:has-text("Favorite") select')
        if (await favSelect.count()) {
          await favSelect.selectOption('favorites_only')
          await page.waitForTimeout(500)
          entry.interactions.favoriteRowCountWhenFiltered = await page.locator('.inventoryTable tbody tr').count()
          await favSelect.selectOption('all')
          await page.waitForTimeout(500)
        }
      }
      if (p.key === 'premium_numbers' && p.tab === 'opportunities') {
        entry.interactions.cardCount = await page.locator('.opportunityCard').count()
        entry.interactions.resumeFitOptionCount = await page.locator('.filterSortBar label:has-text("Resume fit") select option').count().catch(() => 0)
      }
      if (p.key === 'resume_tracking' && p.tab === 'resumes') {
        entry.interactions.resumeCardCount = await page.locator('.resumeCard').count()
      }
      if (p.key === 'resume_tracking' && p.tab === 'submissions') {
        entry.interactions.rowCount = await page.locator('.opportunityCard, .applicationCard').count()
      }
      if (p.key === 'application_tracking') {
        entry.interactions.rowCount = await page.locator('.candidateCard, .applicationCard').count()
      }
      // generic: clear-filters button + sort dropdown presence for registry pages
      if (p.registry) {
        entry.interactions.clearFiltersButtonPresent = (await page.locator('.filterSortClearButton').count()) > 0
        entry.interactions.sortOptionCount = await page.locator('.filterSortBar select').last().locator('option').count().catch(() => 0)
      }
    } catch (e) {
      entry.interactionError = String(e)
    }

    entry.consoleErrors = [...consoleErrors]
    entry.pageErrors = [...pageErrors]
    entry.dialogsSeen = [...dialogs]
    entry.mutatingCallsDuringVisit = mutatingCalls.slice(before)
    return entry
  }

  for (const p of PAGES) {
    let entry
    try {
      entry = await Promise.race([
        visitPage(p),
        new Promise((_, reject) => setTimeout(() => reject(new Error('per-page hard timeout (45s) exceeded')), 45000)),
      ])
    } catch (e) {
      entry = { key: p.key, tab: p.tab, label: p.label, registry: p.registry, hardTimeoutError: String(e) }
      // A hard timeout likely means the page/browser is in a bad state; reload before continuing.
      await page.goto('about:blank').catch(() => {})
    }
    results.push(entry)
    console.log(`[qa-sweep] visited ${p.label} (${slug(p)})`)
  }

  fs.writeFileSync(path.join(OUT_DIR, 'results.json'), JSON.stringify({ generatedAt: new Date().toISOString(), results, allMutatingCalls: mutatingCalls }, null, 2))
  await browser.close()
  console.log(`[qa-sweep] done. ${results.length} pages visited. Report: ${path.join(OUT_DIR, 'results.json')}`)
}

main().catch((e) => { console.error(e); process.exit(1) })
