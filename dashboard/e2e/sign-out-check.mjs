/**
 * The sign-out round trip, end to end.
 *
 * Asserts the part that actually matters: the session is revoked *server-side*,
 * not merely forgotten by the browser. A logout that only drops the cookie
 * leaves a valid token behind.
 *
 * Usage: node e2e/sign-out-check.mjs <session-token>
 */
import { chromium } from 'playwright'

const TOKEN = process.argv[2]
const APP = process.env.APP_URL || 'http://localhost:5173'
const API = process.env.API_URL || 'http://localhost:8000'

if (!TOKEN) {
  console.error('usage: node e2e/sign-out-check.mjs <session-token>')
  process.exit(2)
}

const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` - ${detail}` : ''}`)
}

const browser = await chromium.launch()
const context = await browser.newContext()
await context.addCookies([
  { name: 'codejob_session', value: TOKEN, domain: 'localhost', path: '/', httpOnly: true },
])
const page = await context.newPage()
await page.goto(APP, { waitUntil: 'networkidle' })
await page.waitForTimeout(2000)

const block = page.locator('.accountBlock')
check('account block rendered', (await block.count()) > 0)
const email = await page.locator('.accountEmail').first().textContent()
check('signed-in address shown', Boolean(email && email.includes('@')), email ?? '')

await page.screenshot({ path: 'e2e/out/sign-out-before.png', fullPage: false })
await page.locator('.leftRail').screenshot({ path: 'e2e/out/sign-out-rail.png' })

const button = page.getByRole('button', { name: /^sign out$/i })
check('sign out button present', (await button.count()) > 0)

await button.click()
await page.waitForTimeout(2500)

const loginVisible = (await page.getByRole('button', { name: /sign in with google/i }).count()) > 0
check('login page shown after sign out', loginVisible, loginVisible ? '' : (await page.textContent('body')).slice(0, 120))

// The real assertion: the token must be dead at the server, whatever the
// browser kept.
const probe = await context.request.get(`${API}/candidates?limit=1`, {
  headers: { Cookie: `codejob_session=${TOKEN}` },
})
check('revoked token is refused by the API', probe.status() === 401, `HTTP ${probe.status()}`)

await page.screenshot({ path: 'e2e/out/sign-out-after.png', fullPage: false })
await browser.close()

const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
