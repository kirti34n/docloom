import mermaid from 'mermaid'

let inited = false
function ensure() {
  if (!inited) {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'default',
      securityLevel: 'strict',
      // SVG <text> labels instead of <foreignObject>, so rasterizing the SVG
      // to PNG doesn't taint the canvas
      flowchart: { htmlLabels: false, useMaxWidth: true },
    })
    inited = true
  }
}

/** Render Mermaid to SVG. Returns { svg } or { error }. */
export async function renderMermaid(code: string): Promise<{ svg?: string; error?: string }> {
  ensure()
  try {
    await mermaid.parse(code)
    const id = 'mmd-' + Math.random().toString(36).slice(2)
    const { svg } = await mermaid.render(id, code)
    return { svg }
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) }
  }
}

/** Rasterize an SVG string to a base64 PNG (white background). */
export function svgToPng(svg: string, width = 1400): Promise<string> {
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
        // toDataURL throws synchronously on a tainted canvas — catch & reject
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
