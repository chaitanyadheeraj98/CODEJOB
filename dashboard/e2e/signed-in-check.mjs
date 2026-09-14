/**
 * Proves the dashboard sends its session cookie to the API.
 *
 * The bug this guards: `fetch` defaults to `credentials: 'same-origin'`, the
 * API is on another origin, and only 3 of 115 call sites opted in - so a
 * signed-in user got 401 on everything and an app that rendered but did not
 * work.
 *
 * A real Google sign-in cannot be automated, so the session is injected as a
 * cookie, which is exactly what the callback would have set.
 *
 * Usage: node e2e/signed-in-check.mjs <session-token>
 */
import { chromium } from 'playwright'

const TOKEN = process.argv[2]
const APP = process.env.APP_URL || 'http://localhost:5173'
const API = process.env.API_URL || 'http://localhost:8000'

if (!TOKEN) {
  console.error('usage: node e2e/signed-in-check.mjs <session-token>')
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
const api = []
page.on('response', (r) => {
  if (r.url().startsWith(API)) api.push({ url: r.url(), status: r.status() })
})

await page.goto(APP, { waitUntil: 'networkidle' })
await page.waitForTimeout(3000)

const unauthorized = api.filter((r) => r.status === 401)
const ok = api.filter((r) => r.status >= 200 && r.status < 300)

check('API calls were made', api.length > 0, `${api.length} requests`)
check(
  'no 401 from the API',
  unauthorized.length === 0,
  unauthorized.length ? unauthorized.slice(0, 6).map((r) => new URL(r.url).pathname).join(', ') : `${ok.length} succeeded`,
)

const body = await page.textContent('body')
check('no "Not signed in." on the page', !body.includes('Not signed in.'))
check('settings loaded', !body.includes('Failed to load saved settings'))

// The reported symptom: the header offered "Connect Gmail" on an account that
// was already connected, and clicking it did nothing. That button is shown
// whenever `status` is null - which is what a 401 on /gmail/status produces -
// so it is a direct readout of whether the cookie arrived.
const status = await (await context.request.get(`${API}/gmail/status`)).json()
const label = await page.locator('header button, .topbar button').allTextContents()
const offersConnect = label.some((t) => /connect gmail/i.test(t))
check(
  'gmail control matches the real connection state',
  status.authenticated ? !offersConnect : offersConnect,
  `api authenticated=${status.authenticated}, buttons=[${label.map((t) => t.trim()).filter(Boolean).join(' | ')}]`,
)

await page.screenshot({ path: 'e2e/out/signed-in-check.png', fullPage: false })

await browser.close()

const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
