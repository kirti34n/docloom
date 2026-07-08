// Visual verification: screenshot the real studio UI.
//   (from web/)  node shoot.mjs           (server must be running)
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.env.SHOT_BASE || 'http://127.0.0.1:8899'
const OUT = process.env.SHOT_DIR || './shots'
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
})

async function shot(path, name, waitFor) {
  await page.goto(BASE + path, { waitUntil: 'networkidle' })
  if (waitFor) await page.waitForSelector(waitFor, { timeout: 15000 }).catch(() => {})
  await page.waitForTimeout(700)
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log('shot', name)
}

const notebooks = await (await fetch(BASE + '/api/notebooks')).json()
let deckUrl = null
for (const nb of notebooks) {
  const detail = await (await fetch(`${BASE}/api/notebooks/${nb.id}`)).json()
  const deck = (detail.artifacts || []).find((a) => a.kind === 'deck' && a.version > 0)
  if (deck) {
    deckUrl = `/n/${nb.id}/deck/${deck.id}`
    break
  }
}

await shot('/', 'notebooks')
await shot('/settings', 'settings')
if (notebooks[0]) await shot(`/n/${notebooks[0].id}`, 'workspace')
if (deckUrl) {
  await shot(deckUrl, 'deck-viewer', '.deck-stage')
  // click through to content-rich slides to verify block rendering
  const rail = await page.$$('.w-48 button')
  for (const [i, name] of [
    [2, 'slide-content'],
    [3, 'slide-stats'],
    [4, 'slide-chart'],
    [5, 'slide-twocol'],
    [6, 'slide-image'],
  ]) {
    if (rail[i]) {
      await rail[i].click()
      await page.waitForTimeout(400)
      await page.screenshot({ path: `${OUT}/${name}.png` })
      console.log('shot', name)
    }
  }
} else console.log('no deck artifact found')

await browser.close()
