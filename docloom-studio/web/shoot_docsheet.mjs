import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.env.SHOT_BASE || 'http://127.0.0.1:8899'
const OUT = process.env.SHOT_DIR || './shots'
mkdirSync(OUT, { recursive: true })

const notebooks = await (await fetch(BASE + '/api/notebooks')).json()
const nb = notebooks[0].id
const detail = await (await fetch(`${BASE}/api/notebooks/${nb}`)).json()
const doc = detail.artifacts.find((a) => a.kind === 'doc')
const sheet = detail.artifacts.find((a) => a.kind === 'sheet')

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })

for (const [id, kind, sel] of [
  [doc?.id, 'doc', '.doc-block'],
  [sheet?.id, 'sheet', '.sheet-grid'],
]) {
  if (!id) continue
  await page.goto(`${BASE}/n/${nb}/${kind}/${id}`, { waitUntil: 'networkidle' })
  await page.waitForSelector(sel, { timeout: 15000 }).catch(() => {})
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${OUT}/${kind}-editor.png` })
  console.log('shot', kind)
}
await browser.close()
