import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = 'http://127.0.0.1:8899'
const OUT = process.env.SHOT_DIR || './shots'
mkdirSync(OUT, { recursive: true })

const nb = 'LMLk7SyrR_oB'
const detail = await (await fetch(`${BASE}/api/notebooks/${nb}`)).json()
const deck = detail.artifacts.find((a) => a.title.includes('(Claude)'))
if (!deck) { console.log('deck not found'); process.exit(1) }

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 2 })
const errs = []
page.on('pageerror', (e) => errs.push(String(e)))
await page.goto(`${BASE}/n/${nb}/deck/${deck.id}`, { waitUntil: 'networkidle' })
await page.waitForSelector('.deck-stage', { timeout: 15000 }).catch(() => {})
await page.waitForTimeout(1000)

const rail = await page.$$('[class*=rail] button, .slide-rail button, aside button')
async function shot(i, name) {
  if (rail[i]) { await rail[i].click(); await page.waitForTimeout(700) }
  await page.screenshot({ path: `${OUT}/claude-${name}.png` })
  console.log('shot', name)
}
await shot(2, 'content')   // "Where AI already earns its keep"
await shot(3, 'stats')     // stats row
await shot(4, 'twocol')    // promise/peril
await shot(5, 'chart')     // native chart
console.log('errors:', errs.slice(0, 3).join(' | ') || 'none')
await browser.close()
