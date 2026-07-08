import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.env.SHOT_BASE || 'http://127.0.0.1:8899'
const OUT = process.env.SHOT_DIR || './shots'
mkdirSync(OUT, { recursive: true })

const notebooks = await (await fetch(BASE + '/api/notebooks')).json()
const nb = notebooks[0].id
const detail = await (await fetch(`${BASE}/api/notebooks/${nb}`)).json()
const diagram = detail.artifacts.find((a) => a.kind === 'diagram')

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
const errs = []
page.on('pageerror', (e) => errs.push(String(e)))

await page.goto(`${BASE}/n/${nb}/diagram/${diagram.id}`, { waitUntil: 'networkidle' })
await page.waitForSelector('svg', { timeout: 15000 }).catch(() => {})
await page.waitForTimeout(1500)
await page.screenshot({ path: `${OUT}/diagram-editor.png` })
console.log('shot diagram | errors:', errs.slice(0, 3).join(' | ') || 'none')

// convert to Excalidraw canvas
await page.getByRole('button', { name: 'Edit on canvas' }).click()
const canvasOk = await page.waitForSelector('.excalidraw', { timeout: 20000 })
  .then(() => true).catch(() => false)
await page.waitForTimeout(1500)
await page.screenshot({ path: `${OUT}/diagram-canvas.png` })
console.log('canvas rendered:', canvasOk, '| errors:', errs.slice(0, 3).join(' | ') || 'none')
await browser.close()
