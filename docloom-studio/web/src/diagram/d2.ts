// D2 diagram renderer (replaces Mermaid). D2 runs its layout engine as WASM in
// a web worker, fully offline, and produces materially nicer diagrams.
import { D2 } from '@terrastruct/d2'

let d2: D2 | null = null
function inst(): D2 {
  if (!d2) d2 = new D2()
  return d2
}

// 0 = D2's clean neutral base theme. On top of it we apply the studio palette
// deterministically (see themePreamble) rather than trusting the model to style
// nodes: the model owns structure and shapes, the renderer owns the look, which
// is the same contract the rest of docloom runs on.
const THEME_ID = 0

/** Palette a diagram is painted with. Defaults to the loom tokens so a diagram
 *  authored with no styling at all still comes out on-brand. */
export interface D2Theme {
  node: string // node fill
  stroke: string // node + edge stroke
  ink: string // node label
  edge: string // edge label
}

const LOOM: D2Theme = { node: '#F2F1EC', stroke: '#1F3D63', ink: '#14161A', edge: '#6A6E76' }

/** A D2 preamble that themes every node and edge via glob selectors, so it
 *  applies to any model-emitted source whether or not the model declared
 *  classes. Glob style assignments are authoritative over per-node class
 *  styling in D2 (verified), so this is the single source of visual truth;
 *  the model's shape declarations (cylinder, person, document) pass through
 *  untouched because only style properties are set here, never `shape`. */
function themePreamble(t: D2Theme): string {
  return [
    `**.style.font-size: 15`,
    `**.style.stroke-width: 2`,
    `**.style.border-radius: 6`,
    `**.style.shadow: false`,
    `**.style.fill: "${t.node}"`,
    `**.style.stroke: "${t.stroke}"`,
    `**.style.font-color: "${t.ink}"`,
    `(** -> **)[*].style.stroke: "${t.stroke}"`,
    `(** -> **)[*].style.stroke-width: 2`,
    `(** -> **)[*].style.font-size: 12`,
    `(** -> **)[*].style.font-color: "${t.edge}"`,
    '',
  ].join('\n')
}

/** Render D2 source to a self-contained SVG string. Returns { svg } or { error }.
 *  The studio palette is applied deterministically; pass a theme to match a
 *  deck, or omit it for the default loom palette. */
export async function renderD2(
  code: string,
  theme: D2Theme = LOOM,
): Promise<{ svg?: string; error?: string }> {
  const d = inst()
  // Compile the bare source first so any parse error reports line numbers the
  // user can act on, not positions shifted by the theme preamble.
  try {
    await d.compile(code, { options: { layout: 'elk' } })
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) }
  }
  try {
    const result = await d.compile(themePreamble(theme) + code, {
      options: { layout: 'elk' },
    })
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
