import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.env.SHOT_BASE || 'http://127.0.0.1:8899'
const OUT = process.env.SHOT_DIR || './shots'
mkdirSync(OUT, { recursive: true })

const notebooks = await (await fetch(BASE + '/api/notebooks')).json()
const nb = notebooks[0].id
const detail = await (await fetch(`${BASE}/api/notebooks/${nb}`)).json()
const ig = detail.artifacts.find((a) => a.kind === 'infographic')

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
const errs = []
page.on('pageerror', (e) => errs.push(String(e)))

await page.goto(`${BASE}/n/${nb}/infographic/${ig.id}`, { waitUntil: 'networkidle' })
await page.waitForSelector('svg', { timeout: 20000 }).catch(() => {})
await page.waitForTimeout(2500)
await page.screenshot({ path: `${OUT}/infographic-editor.png` })
const svgCount = await page.evaluate(() => document.querySelectorAll('svg').length)
console.log('shot infographic | svgs:', svgCount, '| errors:', errs.slice(0, 3).join(' | ') || 'none')
await browser.close()
