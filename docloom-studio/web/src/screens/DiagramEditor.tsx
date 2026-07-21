import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { AlertCircle, Check, Download, Loader2 } from 'lucide-react'
import { api } from '../api/client'
import { toast } from '../ui/toast'
import type { ArtifactT } from '../deck/types'
import { renderD2, svgToPng } from '../diagram/d2'

const RENDER_EXTS = ['svg', 'png'] as const
type RenderExt = (typeof RENDER_EXTS)[number]

interface DiagramPayload {
  source?: string
  mermaid_src?: string // legacy artifacts; read once, then re-saved as `source`
  render?: unknown
}

const SAMPLE = `direction: right
a: Input
b: Process
c: Output
a -> b -> c`

export function DiagramEditor() {
  const { artifactId, notebookId } = useParams()
  const navigate = useNavigate()
  const [artifact, setArtifact] = useState<ArtifactT | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [src, setSrc] = useState('')
  const [svg, setSvg] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [state, setState] = useState<'saved' | 'dirty' | 'saving'>('saved')
  const [renders, setRenders] = useState<{ svg: boolean; png: boolean }>({ svg: false, png: false })
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // the first src-triggered render after (re)loading an artifact must not
  // schedule a save: merely opening a diagram would otherwise rewrite its
  // payload and bump the artifact version
  const firstRender = useRef(true)

  useEffect(() => {
    if (!artifactId) return
    firstRender.current = true
    api.get<ArtifactT>(`/api/artifacts/${artifactId}`).then((a) => {
      setArtifact(a)
      const p = a.payload as unknown as DiagramPayload
      setSrc(p.source ?? p.mermaid_src ?? SAMPLE)
    }).catch((e) => setLoadError(e instanceof Error ? e.message : String(e)))
    Promise.all(
      RENDER_EXTS.map((ext) =>
        fetch(`/api/artifacts/${artifactId}/render.${ext}`, { method: 'HEAD' })
          .then((r) => r.ok)
          .catch(() => false),
      ),
    ).then(([svgOk, pngOk]) => setRenders({ svg: svgOk, png: pngOk }))
  }, [artifactId])

  // render D2 -> SVG for the preview, unconditionally, then persist the
  // source (debounced). A diagram that fails to compile is still saved: a
  // render error is a preview concern, not a reason to drop the user's edit.
  const rerender = useCallback(async (code: string, persist: boolean) => {
    const { svg: out, error: err } = await renderD2(code)
    setError(err ?? null)
    if (out) setSvg(out)
    if (!persist) return

    if (saveTimer.current) clearTimeout(saveTimer.current)
    setState('dirty')
    saveTimer.current = setTimeout(async () => {
      setState('saving')
      try {
        await api.put(`/api/artifacts/${artifactId}/payload`, {
          payload: { source: code, render: 'svg' },
        })
        if (out) {
          let png: string | null = null
          try {
            png = await svgToPng(out)
          } catch { /* complex svg, skip png */ }
          await api.post(`/api/artifacts/${artifactId}/renders`, { svg: out, png_base64: png })
          setRenders({ svg: true, png: png !== null })
        }
        setState('saved')
      } catch (e) {
        setState('dirty')
        toast.error(`Save failed: ${e instanceof Error ? e.message : e}`)
      }
    }, 700)
  }, [artifactId])

  useEffect(() => {
    if (!src) return
    const persist = !firstRender.current
    firstRender.current = false
    void rerender(src, persist)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src])

  const downloadRender = (ext: RenderExt) => {
    if (!renders[ext]) {
      toast.error(`No ${ext.toUpperCase()} render yet. Fix the diagram and wait for it to save.`)
      return
    }
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

      <div className="flex min-h-0 flex-1">
        <div className="flex w-96 shrink-0 flex-col border-r border-stage-line">
          <div className="px-4 py-2 font-mono text-[11px] uppercase leading-4 tracking-[0.08em] text-stage-muted">D2 source</div>
          <textarea
            value={src}
            onChange={(e) => setSrc(e.target.value)}
            spellCheck={false}
            className="flex-1 resize-none bg-stage-bg px-4 py-2 font-mono text-[12.5px] text-white outline-none"
          />
          {error && (
            <div className="border-t border-stage-line p-3">
              <div className="flex items-start gap-2 text-[12px] text-red-300">
                <AlertCircle size={14} className="mt-0.5 shrink-0" />
                <span className="font-mono">{error.slice(0, 200)}</span>
              </div>
            </div>
          )}
        </div>
        <div className="flex min-w-0 flex-1 items-center justify-center overflow-auto bg-white p-8">
          {svg ? <div className="w-full max-w-[1100px] [&_svg]:h-auto [&_svg]:w-full" dangerouslySetInnerHTML={{ __html: svg }} />
            : <span className="text-ws-muted">rendering…</span>}
        </div>
      </div>
    </div>
  )
}
