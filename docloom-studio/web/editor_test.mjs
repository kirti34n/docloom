// Drive the real deck editor: edit a title, switch theme, add a slide, and
// confirm each change persists through autosave. Server must be running.
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import assert from 'node:assert'

const BASE = process.env.SHOT_BASE || 'http://127.0.0.1:8899'
const OUT = process.env.SHOT_DIR || './shots'
mkdirSync(OUT, { recursive: true })

// find the showcase deck
const notebooks = await (await fetch(BASE + '/api/notebooks')).json()
let nb, deck
for (const n of notebooks) {
  const d = await (await fetch(`${BASE}/api/notebooks/${n.id}`)).json()
  const found = (d.artifacts || []).find((a) => a.kind === 'deck')
  if (found) { nb = n.id; deck = found.id; break }
}
assert(deck, 'no deck to edit')

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 940 }, deviceScaleFactor: 2 })
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto(`${BASE}/n/${nb}/deck/${deck}`, { waitUntil: 'networkidle' })
await page.waitForSelector('.deck-stage .ProseMirror', { timeout: 15000 })
await page.waitForTimeout(600)
await page.screenshot({ path: `${OUT}/editor.png` })
console.log('shot editor')

// --- edit the first slide's title ---
const stamp = 'EDITED-' + Math.floor(Date.now() / 1000)
const title = page.locator('.deck-stage .editable').first()
await title.click()
await page.keyboard.press('Control+A')
await page.keyboard.type(stamp)
await page.locator('body').click({ position: { x: 5, y: 5 } }) // blur → commit
await page.waitForTimeout(1400) // debounce + save

let art = await (await fetch(`${BASE}/api/artifacts/${deck}`)).json()
const savedTitle = art.payload.ir.slides[0].title
assert(savedTitle.includes(stamp), `title not saved: got "${savedTitle}"`)
console.log('OK  title edit persisted:', savedTitle)

// --- switch theme via inspector ---
const beforeTheme = art.payload.ir && art.payload.theme_name
const themeBtn = page.getByRole('button', { name: /Editorial|Terra|Pulse|Paper/ }).first()
await themeBtn.click()
await page.waitForTimeout(1400)
art = await (await fetch(`${BASE}/api/artifacts/${deck}`)).json()
assert(art.payload.theme_name !== beforeTheme, 'theme did not change')
console.log('OK  theme switched:', beforeTheme, '->', art.payload.theme_name)

// --- add a slide ---
const beforeCount = art.payload.ir.slides.length
await page.getByRole('button', { name: 'Add slide' }).click()
await page.waitForTimeout(1400)
art = await (await fetch(`${BASE}/api/artifacts/${deck}`)).json()
assert(art.payload.ir.slides.length === beforeCount + 1, 'slide not added')
console.log('OK  slide added:', beforeCount, '->', art.payload.ir.slides.length)

await page.screenshot({ path: `${OUT}/editor-after.png` })
console.log('shot editor-after')

assert(errors.length === 0, 'console errors:\n' + errors.join('\n'))
console.log('\nEDITOR TEST PASS (no console errors)')
await browser.close()
