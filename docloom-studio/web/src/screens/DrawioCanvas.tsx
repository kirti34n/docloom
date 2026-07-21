import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Check, Download, Loader2 } from 'lucide-react'
import { api } from '../api/client'
import { toast } from '../ui/toast'
import type { ArtifactT } from '../deck/types'

// The self-hosted, offline draw.io app (Phase 0). Same-origin (served by the
// backend at /drawio, and proxied there by vite dev), so postMessage between
// this page and the iframe is same-origin -- no origin allowlist needed
// beyond the contentWindow identity check below.
const DRAWIO_SRC =
  '/drawio/index.html?embed=1&proto=json&offline=1&stealth=1&spin=1&pwa=0' +
  '&ui=min&noExitBtn=1&pages=0&math=0&plugins=0&dark=1'

const RENDER_EXTS = ['svg', 'png'] as const
type RenderExt = (typeof RENDER_EXTS)[number]
type SaveState = 'saved' | 'dirty' | 'saving'

interface DrawioPayload {
  type?: string
  drawio_xml?: string
  diagram_ir?: unknown
  theme_name?: string
}

async function probeRenders(artifactId: string): Promise<{ svg: boolean; png: boolean }> {
  const [svgOk, pngOk] = await Promise.all(
    RENDER_EXTS.map((ext) =>
      fetch(`/api/artifacts/${artifactId}/render.${ext}`, { method: 'HEAD' })
        .then((r) => r.ok)
        .catch(() => false),
    ),
  )
  return { svg: svgOk, png: pngOk }
}

/** GET /diagram/drawio returns raw `application/xml` (the seeded-from-IR or
 *  previously-forked mxGraph XML), not JSON -- the shared `api` client always
 *  parses a JSON body, so this one call goes through a plain fetch. */
async function fetchDrawioSeed(artifactId: string): Promise<string> {
  const res = await fetch(`/api/artifacts/${artifactId}/diagram/drawio`)
  if (!res.ok) throw new Error(`Failed to load diagram (${res.status})`)
  return res.text()
}

/** `data:image/svg+xml;base64,....` -> the decoded SVG markup. draw.io's
 *  `xmlsvg` export always base64-encodes the data URL, so unlike the antv
 *  infographic exporter (which can emit either encoding) there is only one
 *  branch to handle here. */
function decodeExportedSvg(dataUrl: string): string | null {
  const comma = dataUrl.indexOf(',')
  if (comma < 0) return null
  try {
    return atob(dataUrl.slice(comma + 1))
  } catch {
    return null
  }
}

export interface DrawioExportMessage {
  event?: string
  xml: string
  data: string
}

/** Pure handler for the one load-bearing bridge reply: draw.io's `export`
 *  event answering our `{action:'export', format:'xmlsvg'}` request. `m.xml`
 *  is the canonical mxGraph XML (saved as `drawio_xml`, the new source of
 *  truth once a draw.io edit exists); `m.data` is the rendered picture, fed
 *  through the existing renders sink so decks/exports bake it unchanged.
 *  Extracted as a standalone async function (no DOM/iframe involved) so it is
 *  directly unit-testable -- this repo has no jsdom/testing-library. */
export async function applyDrawioExport(
  artifactId: string,
  themeName: string,
  m: DrawioExportMessage,
): Promise<{ svg: string | null }> {
  const svg = decodeExportedSvg(m.data)
  await api.put(`/api/artifacts/${artifactId}/payload`, {
    payload: { type: 'diagram_drawio', drawio_xml: m.xml, theme_name: themeName, render: 'svg' },
  })
  if (svg) await api.post(`/api/artifacts/${artifactId}/renders`, { svg })
  return { svg }
}

const AUTOSAVE_DEBOUNCE_MS = 300

export function DrawioCanvas() {
  const { artifactId, notebookId } = useParams()
  const navigate = useNavigate()
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [artifact, setArtifact] = useState<ArtifactT | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [state, setState] = useState<SaveState>('saved')
  const [renders, setRenders] = useState<{ svg: boolean; png: boolean }>({ svg: false, png: false })

  // Mutable mirrors read by the message handler/timers without risking a
  // stale closure (the effect that installs the listener only depends on
  // `artifactId`, not on every field the handler reads).
  const themeNameRef = useRef('paper')
  const titleRef = useRef('diagram')
  const exportTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!artifactId) return
    setLoadError(null)
    api.get<ArtifactT>(`/api/artifacts/${artifactId}`).then((a) => {
      setArtifact(a)
      titleRef.current = a.title || 'diagram'
      const p = a.payload as unknown as DrawioPayload
      themeNameRef.current = p.theme_name ?? 'paper'
    }).catch((e) => setLoadError(e instanceof Error ? e.message : String(e)))
    probeRenders(artifactId).then(setRenders)
  }, [artifactId])

  const post = useCallback((msg: Record<string, unknown>) => {
    iframeRef.current?.contentWindow?.postMessage(JSON.stringify(msg), window.location.origin)
  }, [])

  // The bridge. Attached in an effect that runs before the iframe can finish
  // loading and emit 'init' -- React runs effects after the DOM (including
  // the iframe tag) commits, but the iframe's own document load (and thus
  // draw.io's boot sequence) is strictly slower than that, so the listener is
  // always in place first. React 19 StrictMode double-invokes this effect
  // (mount -> cleanup -> mount): add/remove is idempotent (a plain
  // add/removeEventListener pair, no external subscription to leak), and
  // 'init' is handled unconditionally on every message rather than gated by
  // a "have we already loaded" ref, because draw.io re-emits 'init' on every
  // iframe (re)mount and the seed fetch is cheap and idempotent.
  useEffect(() => {
    if (!artifactId) return
    const id = artifactId // narrowed once; closures below can't see the guard above
    function onMessage(e: MessageEvent) {
      if (e.source !== iframeRef.current?.contentWindow) return
      let m: Record<string, unknown>
      try {
        m = typeof e.data === 'string' ? JSON.parse(e.data) : e.data
      } catch {
        return
      }
      switch (m.event) {
        case 'init':
          fetchDrawioSeed(id)
            .then((xml) => post({ action: 'load', xml, autosave: 1, title: titleRef.current }))
            .catch((err) =>
              toast.error(`Failed to load diagram: ${err instanceof Error ? err.message : String(err)}`))
          break
        case 'autosave':
        case 'save':
          setState('dirty')
          if (exportTimer.current) clearTimeout(exportTimer.current)
          exportTimer.current = setTimeout(
            () => post({ action: 'export', format: 'xmlsvg' }),
            AUTOSAVE_DEBOUNCE_MS,
          )
          break
        case 'export':
          setState('saving')
          applyDrawioExport(id, themeNameRef.current, m as unknown as DrawioExportMessage)
            .then(() => probeRenders(id))
            .then((r) => { setRenders(r); setState('saved') })
            .catch((err) => {
              setState('dirty')
              toast.error(`Save failed: ${err instanceof Error ? err.message : String(err)}`)
            })
          break
        default:
          break
      }
    }
    window.addEventListener('message', onMessage)
    return () => {
      window.removeEventListener('message', onMessage)
      if (exportTimer.current) clearTimeout(exportTimer.current)
    }
  }, [artifactId, post])

  const downloadRender = (ext: RenderExt) => {
    if (!renders[ext]) { toast.error(`No ${ext.toUpperCase()} render yet. Edit the diagram and wait for it to save.`); return }
    const a = document.createElement('a')
    a.href = `/api/artifacts/${artifactId}/render.${ext}`
    a.download = `${artifact?.title ?? 'diagram'}.${ext}`
    a.click()
  }

  if (loadError)
    return <div className="flex h-full items-center justify-center bg-stage-bg text-madder text-[13px]">{loadError}</div>
  if (!artifact)
    return <div className="flex h-full items-center justify-center bg-stage-bg text-stage-muted"><Loader2 className="animate-spin" /></div>

  return (
    <div className="flex h-full flex-col bg-stage-bg text-white">
      <div className="flex items-center gap-3 border-b border-stage-line px-5 py-2.5">
        <button onClick={() => navigate(`/n/${notebookId}`)} className="text-[12px] text-stage-muted hover:text-white">← Notebook</button>
        <span className="font-display text-[14px] font-semibold">{artifact.title}</span>
        <span className="text-[12px] text-stage-muted">
          {state === 'saving' ? <span className="flex items-center gap-1"><Loader2 size={12} className="animate-spin" /> Saving…</span>
            : state === 'dirty' ? 'Unsaved' : <span className="flex items-center gap-1"><Check size={12} /> Saved</span>}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          {RENDER_EXTS.map((ext) => (
            <button key={ext} onClick={() => downloadRender(ext)} disabled={!renders[ext]}
              title={renders[ext] ? undefined : `No ${ext.toUpperCase()} render yet`}
              className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-stage-line px-2.5 py-1.5 text-[12px] text-stage-muted hover:text-white disabled:opacity-40">
              <Download size={12} /> {ext.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {/* draw.io owns the whole canvas -- no separate toolbar or preview
         pane; unlike DiagramIRCanvas, positions are free-form (draw.io's own
         editing model), not solved by a layout engine. */}
      <div className="min-h-0 flex-1">
        <iframe
          ref={iframeRef}
          src={DRAWIO_SRC}
          title="draw.io editor"
          className="h-full w-full border-0 bg-white"
        />
      </div>
    </div>
  )
}
