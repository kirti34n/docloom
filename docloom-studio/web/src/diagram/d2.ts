// D2 diagram renderer (replaces Mermaid). D2 runs its layout engine as WASM in
// a web worker, fully offline, and produces materially nicer diagrams.
import { D2 } from '@terrastruct/d2'

let d2: D2 | null = null
function inst(): D2 {
  if (!d2) d2 = new D2()
  return d2
}

// 0 = D2's clean neutral theme; diagram styling (Aurora colors) is set in the
// D2 source via classes so it stays consistent across LLM-authored diagrams.
const THEME_ID = 0

/** Render D2 source to a self-contained SVG string. Returns { svg } or { error }. */
export async function renderD2(code: string): Promise<{ svg?: string; error?: string }> {
  try {
    const d = inst()
    const result = await d.compile(code, { options: { layout: 'elk' } })
    const svg = await d.render(result.diagram, {
      themeID: THEME_ID,
      sketch: false,
      center: true,
      pad: 28,
      noXMLTag: true, // safe for direct HTML embedding
    })
    return { svg }
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) }
  }
}

/** Rasterize an SVG string to a base64 PNG (white background). D2 embeds its
 * fonts in the SVG, so the canvas stays untainted and toDataURL succeeds. */
export function svgToPng(svg: string, width = 1600): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    img.onload = () => {
      try {
        const scale = width / (img.width || width)
        const canvas = document.createElement('canvas')
        canvas.width = width
        canvas.height = Math.max(1, Math.round((img.height || 400) * scale))
        const ctx = canvas.getContext('2d')!
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, canvas.width, canvas.height)
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
        const data = canvas.toDataURL('image/png').split(',')[1]
        URL.revokeObjectURL(url)
        resolve(data)
      } catch (e) {
        URL.revokeObjectURL(url)
        reject(e)
      }
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('svg rasterization failed'))
    }
    img.src = url
  })
}
