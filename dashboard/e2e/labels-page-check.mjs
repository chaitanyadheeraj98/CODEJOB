// Labels workspace: live end-to-end check against the running stack.
//
// Not mocked. It drives the real page against the real backend and the real
// Gmail-derived data, screenshots desktop and mobile in one pass, and asserts
// the things the feature exists for: labels in a rail, threads in the selected
// label, and a dossier that carries both directions of the conversation.
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE_URL = process.env.QA_BASE_URL || 'http://localhost:5174'
const OUT = process.env.QA_OUT || 'e2e/out'
mkdirSync(OUT, { recursive: true })

const results = []
const check = (name, pass, detail = '') => {
  results.push({ name, pass, detail })
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`)
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } })
const consoleErrors = []
page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()) })
page.on('pageerror', (error) => consoleErrors.push(String(error)))

await page.goto(`${BASE_URL}/?page=labels`, { waitUntil: 'domcontentloaded' })
await page.waitForFunction(() => !document.body.innerText.includes('Loading saved settings'), null, { timeout: 60000 }).catch(() => {})
await page.waitForSelector('.labelsSection', { timeout: 30000 })

// The nav row only renders when label tracking is on, and that flag arrives
// with the settings bootstrap - which lands after the page itself. Waiting on
// the row rather than sampling it once, or this reports a product bug when all
// it caught was its own impatience.
await page.waitForFunction(
  () => Array.from(document.querySelectorAll('.navItem')).some((el) => el.textContent?.trim() === 'Labels'),
  null,
  { timeout: 30000 },
).catch(() => {})
check('Labels appears in the sidebar', await page.locator('.navItem', { hasText: /^Labels$/ }).count() > 0)

await page.waitForSelector('.labelRailItem', { timeout: 30000 })
const railCount = await page.locator('.labelRailItem').count()
check('label rail lists tracked labels', railCount > 0, `${railCount} labels`)

const activeLabel = (await page.locator('.labelRailItem.active').first().textContent()) || ''
check('a label is selected on arrival', activeLabel.length > 0, activeLabel.trim())

// Wait out the thread fetch rather than reading the placeholder.
await page.waitForFunction(() => !document.body.innerText.includes('Loading threads…'), null, { timeout: 30000 }).catch(() => {})
const threadCount = await page.locator('.labelThreadItem').count()
check('threads listed for the selected label', threadCount > 0, `${threadCount} threads`)

await page.waitForFunction(() => !document.body.innerText.includes('Loading conversation…'), null, { timeout: 30000 }).catch(() => {})
const messageCount = await page.locator('.labelMessage').count()
check('dossier renders the conversation', messageCount > 0, `${messageCount} messages`)

const inbound = await page.locator('.labelMessage:not(.outbound)').count()
const outbound = await page.locator('.labelMessage.outbound').count()
check('both directions are present', inbound > 0 && outbound > 0, `${inbound} received / ${outbound} sent`)

const recruiterChips = await page.locator('.labelContactChip.recruiter').count()
check('recruiters are named on the dossier', recruiterChips > 0, `${recruiterChips} tracked contacts`)

// The owner's own address must never be offered as a contact to reason about.
check('own address is not listed as a contact', await page.locator('.labelContactChip.self').count() === 0)

// Selecting a second thread must actually swap the dossier, not just the row.
if (threadCount > 1) {
  const before = await page.locator('.labelDossierHeader h3').first().textContent()
  await page.locator('.labelThreadItem').nth(1).click()
  await page.waitForFunction(
    (previous) => document.querySelector('.labelDossierHeader h3')?.textContent !== previous,
    before,
    { timeout: 20000 },
  ).catch(() => {})
  const after = await page.locator('.labelDossierHeader h3').first().textContent()
  check('selecting another thread swaps the dossier', before !== after, `${before?.slice(0, 30)} -> ${after?.slice(0, 30)}`)
}

// Keyboard reachability of the three panes.
await page.keyboard.press('Tab')
const focusVisible = await page.evaluate(() => {
  const el = document.activeElement
  return Boolean(el && el !== document.body && getComputedStyle(el).outlineStyle !== undefined)
})
check('keyboard focus lands on a control', focusVisible)

await page.screenshot({ path: `${OUT}/labels-desktop.png`, fullPage: true })

// Search narrows rather than empties.
const search = page.locator('.labelsSearch')
await search.fill('zzzznomatch')
await page.waitForTimeout(1500)
const emptyCopy = (await page.locator('.labelThreadList').innerText()).toLowerCase()
check('a search with no hits explains itself', emptyCopy.includes('no thread'), emptyCopy.slice(0, 60))
await search.fill('')
await page.waitForTimeout(1500)

// Mobile, same round.
await page.setViewportSize({ width: 390, height: 844 })
await page.waitForTimeout(600)
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
check('no horizontal overflow at 390px', overflow <= 1, `${overflow}px`)
const stacked = await page.evaluate(() => getComputedStyle(document.querySelector('.labelsLayout')).gridTemplateColumns.split(' ').length)
check('layout stacks on mobile', stacked === 1, `${stacked} column(s)`)
await page.screenshot({ path: `${OUT}/labels-mobile.png`, fullPage: true })

check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))

await browser.close()
const failed = results.filter((r) => !r.pass)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
