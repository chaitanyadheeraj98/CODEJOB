// Design monitor for Resume Tracking > Editor.
// Usage: node e2e/editor-monitor.mjs [outDir]
//
// Opens the builder with a real draft loaded, expands the panels that are collapsed by
// default, and reports the measurements that decide whether the layout is actually
// working: overflow, pane widths, control density, type sizes, hit targets, contrast.
// Screenshots desktop and mobile so a change can be compared against the previous run.
//
// Safety: identical posture to qa-sweep.mjs - GETs reach the real backend, every other
// method is intercepted and fulfilled with a mock, so a click can be inspected without
// mutating live dev data.
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const BASE_URL = process.env.QA_BASE_URL || 'http://localhost:5173'
const API_BASE = process.env.QA_API_BASE || 'http://localhost:8000'
const OUT_DIR = process.argv[2] || './e2e/editor-report'
const SHOTS = path.join(OUT_DIR, 'screenshots')
fs.mkdirSync(SHOTS, { recursive: true })

const DESKTOP = { width: 1440, height: 900 }
const WIDE = { width: 1920, height: 1080 }
const MOBILE = { width: 390, height: 844 }

// Panes that make up the builder, in reading order.
const PANES = [
  ['sidebar', '.resumeBuilderSidebar'],
  ['main', '.resumeEditorMain'],
  ['preview', '.resumeBuilderPreview'],
]

const luminance = ([r, g, b]) => {
  const f = (v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4 }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
// `rgb(91, 100, 120)` counts to 255; `color(srgb 0.94 0.95 0.98)` - which is how
// a computed color-mix() serialises - counts to 1. Read as 0-255 the second form
// is almost black, which reported a light blue row as failing contrast and then
// reported the fix as 1:1.
const parseRgb = (value) => {
  const numbers = (value.match(/\d*\.?\d+/g) || []).map(Number).slice(0, 3)
  return value.includes('color(') ? numbers.map((part) => part * 255) : numbers
}
const contrast = (fg, bg) => {
  const [a, b] = [luminance(parseRgb(fg)), luminance(parseRgb(bg))].sort((x, y) => y - x)
  return Number(((a + 0.05) / (b + 0.05)).toFixed(2))
}

async function main() {
  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: DESKTOP, deviceScaleFactor: 2 })
  const page = await context.newPage()
  page.setDefaultTimeout(10000)

  const consoleErrors = []
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('pageerror', (e) => consoleErrors.push(String(e)))

  await context.route(`${API_BASE}/**`, async (route) => {
    if (route.request().method() === 'GET') { await route.continue(); return }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  const report = { at: new Date().toISOString(), viewport: DESKTOP, findings: [] }
  const flag = (severity, what) => report.findings.push({ severity, what })

  await page.goto(`${BASE_URL}/?page=resume_tracking&tab=editor`, { waitUntil: 'load', timeout: 30000 })
  await page.waitForTimeout(1200)

  // Open the draft with the most text: an empty draft hides most of the surface.
  const drafts = page.locator('.resumeEditorListItem')
  report.draftCount = await drafts.count()
  if (report.draftCount) {
    const sizes = await drafts.evaluateAll((nodes) => nodes.map((n) => Number((n.textContent.match(/([\d,]+)\s*characters/) || [0, '0'])[1].replace(/,/g, ''))))
    await drafts.nth(sizes.indexOf(Math.max(...sizes))).click()
    await page.waitForTimeout(1200)
  }

  // Both panels ship collapsed; the density problem only shows once they are open.
  for (const label of ['Templates and employer layouts', 'Formatting']) {
    const summary = page.locator('summary', { hasText: label }).first()
    if (await summary.count()) {
      const open = await summary.evaluate((el) => el.parentElement.hasAttribute('open'))
      if (!open) { await summary.click(); await page.waitForTimeout(400) }
    }
  }
  await page.waitForTimeout(600)

  const measure = async (name) => {
    const geometry = await page.evaluate((panes) => {
      const doc = document.documentElement
      const out = {
        docScrollWidth: doc.scrollWidth,
        innerWidth: window.innerWidth,
        horizontalOverflow: doc.scrollWidth > window.innerWidth + 4,
        panes: {},
        offscreen: [],
      }
      for (const [key, selector] of panes) {
        const el = document.querySelector(selector)
        if (!el) continue
        const r = el.getBoundingClientRect()
        out.panes[key] = {
          width: Math.round(r.width),
          height: Math.round(r.height),
          scrollWidth: el.scrollWidth,
          overflowsX: el.scrollWidth > el.clientWidth + 4,
        }
      }
      // Anything sticking out past the right edge is the thing that made the page
      // scroll - except elements that are hidden or inside a clipping ancestor.
      // The pagination probe is a full-width copy of the document parked offscreen
      // under visibility:hidden, and reporting it every run would train the eye to
      // skip this list.
      const clipped = (el) => {
        for (let node = el.parentElement; node; node = node.parentElement) {
          const o = getComputedStyle(node)
          if (o.overflowX === 'hidden' || o.overflowX === 'auto' || o.overflowX === 'scroll') return true
        }
        return false
      }
      for (const el of document.querySelectorAll('.resumeEditorPanel *')) {
        const style = getComputedStyle(el)
        if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') continue
        if (el.closest('[aria-hidden="true"], [inert]')) continue
        if (clipped(el)) continue
        const r = el.getBoundingClientRect()
        if (r.width > 0 && r.right > window.innerWidth + 4) {
          out.offscreen.push({
            tag: el.tagName.toLowerCase(),
            cls: (el.className || '').toString().slice(0, 60),
            right: Math.round(r.right),
            width: Math.round(r.width),
          })
        }
      }
      out.offscreen = out.offscreen.slice(0, 12)

      // Panes must sit beside each other, never on top. A sticky rail next to a
      // full-width row is the way this happens, and it reads as a floating card
      // printed over the panel below it.
      out.overlaps = []
      const boxes = panes.map(([key, sel]) => [key, document.querySelector(sel)]).filter(([, el]) => el)
      for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i][1].getBoundingClientRect()
          const b = boxes[j][1].getBoundingClientRect()
          const x = Math.min(a.right, b.right) - Math.max(a.left, b.left)
          const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
          if (x > 8 && y > 8) out.overlaps.push({ a: boxes[i][0], b: boxes[j][0], x: Math.round(x), y: Math.round(y) })
        }
      }
      return out
    }, PANES)

    const controls = await page.evaluate(() => {
      const panel = document.querySelector('.resumeEditorPanel')
      if (!panel) return null
      const fields = [...panel.querySelectorAll('input, select, textarea, button')]
      const small = []
      const tiny = []
      for (const el of fields) {
        const r = el.getBoundingClientRect()
        const cs = getComputedStyle(el)
        if (r.height > 0 && r.height < 32 && el.type !== 'checkbox' && el.type !== 'color') {
          small.push({ tag: el.tagName.toLowerCase(), h: Math.round(r.height), label: (el.getAttribute('aria-label') || el.name || el.type || '').slice(0, 30) })
        }
        if (parseFloat(cs.fontSize) < 12) tiny.push({ tag: el.tagName.toLowerCase(), size: cs.fontSize })
      }
      return {
        total: fields.length,
        numberInputs: panel.querySelectorAll('input[type=number]').length,
        // Density is fine here; an undifferentiated wall of it is not. What
        // matters is whether each number sits under a heading that says what
        // part of the layout it belongs to.
        ungroupedNumbers: [...panel.querySelectorAll('input[type=number]')].filter((el) => !el.closest('.resumeFormatGroup')).length,
        groups: panel.querySelectorAll('.resumeFormatGroup').length,
        checkboxes: panel.querySelectorAll('input[type=checkbox]').length,
        selects: panel.querySelectorAll('select').length,
        buttons: panel.querySelectorAll('button').length,
        shortControls: small.slice(0, 10),
        shortControlCount: small.length,
        tinyText: tiny.slice(0, 6),
      }
    })

    // Contrast of the label text that carries the panel, against its own backdrop.
    const contrastSamples = await page.evaluate(() => {
      // Every text role in the panel that is not plain body ink. Keep these in
      // step with the markup: a selector that stops matching silently stops
      // being checked, which is how the formatting labels went unmeasured.
      const pick = [
        '.resumeFormatField > span', '.resumeFormatGroup h4', '.resumeFormatUnit', '.resumeSwitchLabel',
        '.subtle', '.resumeSectionJump', '.resumeEditorListText span', '.resumeCount', '.resumePreviewTools .subtle',
      ]
      const out = []
      for (const selector of pick) {
        const el = document.querySelector(selector)
        if (!el) continue
        let bg = 'rgb(255,255,255)'
        for (let node = el; node; node = node.parentElement) {
          const c = getComputedStyle(node).backgroundColor
          if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') { bg = c; break }
        }
        out.push({ selector, color: getComputedStyle(el).color, background: bg, fontSize: getComputedStyle(el).fontSize })
      }
      return out
    })
    for (const s of contrastSamples) s.ratio = contrast(s.color, s.background)

    return { geometry, controls, contrastSamples }
  }

  report.desktop = await measure('desktop')
  await page.screenshot({ path: path.join(SHOTS, 'editor-desktop.png'), fullPage: true })
  await page.screenshot({ path: path.join(SHOTS, 'editor-desktop-fold.png') })

  // How big is the page actually drawn? A preview fitted into a third of the
  // builder lands near 38%, which passes every geometry check above and is still
  // useless to look at - so measure the drawn page against the real one.
  const pageScale = async () => page.evaluate(() => {
    const el = document.querySelector('.resumePage')
    if (!el) return null
    const natural = parseFloat(el.style.width) * 96 || 816
    return Math.round((el.getBoundingClientRect().width / natural) * 100)
  })
  report.previewScale = { inColumn: await pageScale() }
  const widen = page.locator('.resumePreviewWiden')
  if (await widen.count()) {
    await widen.click()
    await page.waitForTimeout(700)
    report.previewScale.widened = await pageScale()
    await page.screenshot({ path: path.join(SHOTS, 'editor-preview-wide.png'), fullPage: true })
    await widen.click()
    await page.waitForTimeout(500)
  }

  await page.setViewportSize(WIDE)
  await page.waitForTimeout(500)
  report.wide = (await measure('wide')).geometry
  await page.screenshot({ path: path.join(SHOTS, 'editor-wide-fold.png') })

  await page.setViewportSize(MOBILE)
  await page.waitForTimeout(600)
  report.mobile = (await measure('mobile')).geometry
  await page.screenshot({ path: path.join(SHOTS, 'editor-mobile.png'), fullPage: true })
  await page.setViewportSize(DESKTOP)

  // ---- Findings ----
  for (const [name, data] of [['desktop', report.desktop.geometry], ['wide', report.wide], ['mobile', report.mobile]]) {
    if (data.horizontalOverflow) flag('p0', `${name}: page scrolls horizontally (${data.docScrollWidth}px into ${data.innerWidth}px)`)
    for (const [pane, m] of Object.entries(data.panes)) {
      if (m.overflowsX) flag('p1', `${name}: ${pane} pane overflows its own width (${m.scrollWidth} > ${m.width})`)
    }
    for (const o of data.overlaps || []) flag('p0', `${name}: ${o.a} and ${o.b} panes overlap by ${o.x}x${o.y}px`)
  }
  for (const [name, data] of [['desktop', report.desktop.geometry], ['wide', report.wide], ['mobile', report.mobile]]) {
    if (data.offscreen.length) {
      flag('p0', `${name} offscreen: ${data.offscreen.map((o) => `${o.tag}.${o.cls.split(' ')[0]}@${o.right}px(w${o.width})`).join(', ')}`)
    }
  }
  const c = report.desktop.controls
  if (c) {
    if (c.ungroupedNumbers >= 8) flag('p1', `${c.ungroupedNumbers} number inputs with no group heading - needs grouping or progressive disclosure`)
    if (c.shortControlCount) flag('p2', `${c.shortControlCount} controls under 32px tall`)
    if (c.tinyText.length) flag('p2', `text under 12px on ${c.tinyText.length} controls`)
  }
  for (const s of report.desktop.contrastSamples) {
    if (s.ratio < 4.5) flag('p1', `contrast ${s.ratio}:1 on ${s.selector} (${s.color} on ${s.background})`)
  }
  const ps = report.previewScale || {}
  // The in-column fit is allowed to be small; what is not allowed is having no
  // way out of it. Widening has to reach a size the page can be read at.
  if (ps.inColumn != null && Math.max(ps.inColumn, ps.widened ?? 0) < 62) flag('p1', `preview never exceeds ${Math.max(ps.inColumn, ps.widened ?? 0)}% in either layout - not readable`)
  if (ps.widened == null && ps.inColumn != null && ps.inColumn < 62) flag('p1', `preview is ${ps.inColumn}% with no control to enlarge it`)
  if (consoleErrors.length) flag('p1', `${consoleErrors.length} console errors`)
  report.consoleErrors = consoleErrors.slice(0, 8)

  fs.writeFileSync(path.join(OUT_DIR, 'report.json'), JSON.stringify(report, null, 2))

  const order = { p0: 0, p1: 1, p2: 2 }
  report.findings.sort((a, b) => order[a.severity] - order[b.severity])
  console.log(`\nEditor monitor - ${report.draftCount} drafts, ${report.findings.length} findings`)
  console.log(`preview: ${JSON.stringify(report.previewScale)}`)
  console.log(`panes desktop: ${Object.entries(report.desktop.geometry.panes).map(([k, v]) => `${k} ${v.width}px`).join(' | ') || 'none found'}`)
  if (c) console.log(`controls: ${c.total} total, ${c.numberInputs} number in ${c.groups} groups, ${c.selects} select, ${c.checkboxes} checkbox, ${c.buttons} button`)
  for (const f of report.findings) console.log(`  [${f.severity}] ${f.what}`)
  if (!report.findings.length) console.log('  clean')
  console.log(`\nreport: ${path.join(OUT_DIR, 'report.json')}`)
  console.log(`shots:  ${SHOTS}`)

  await browser.close()
  process.exit(report.findings.some((f) => f.severity === 'p0') ? 1 : 0)
}

main().catch((e) => { console.error(e); process.exit(1) })
