import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.env.SHOT_BASE || 'http://127.0.0.1:8899'
const OUT = process.env.SHOT_DIR || './shots'
mkdirSync(OUT, { recursive: true })

const notebooks = await (await fetch(BASE + '/api/notebooks')).json()
const nb = notebooks[0].id
const detail = await (await fetch(`${BASE}/api/notebooks/${nb}`)).json()
const deck = detail.artifacts.find((a) => a.kind === 'deck')

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
const errs = []
page.on('pageerror', (e) => errs.push(String(e)))

await page.goto(`${BASE}/assets`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)
await page.screenshot({ path: `${OUT}/asset-library.png` })
console.log('shot asset-library | errors:', errs.slice(0, 2).join(' | ') || 'none')

await page.goto(`${BASE}/n/${nb}/deck/${deck.id}`, { waitUntil: 'networkidle' })
await page.waitForSelector('.deck-stage', { timeout: 15000 }).catch(() => {})
// jump to the image slide (2nd)
await page.waitForTimeout(800)
const rail = await page.$$('.slide-rail button, [class*=rail] button')
if (rail[1]) await rail[1].click()
await page.waitForTimeout(1000)
await page.screenshot({ path: `${OUT}/deck-with-image.png` })
console.log('shot deck-with-image | errors:', errs.slice(0, 2).join(' | ') || 'none')
await browser.close()
