import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { AlertCircle, Check, Download, Loader2, Sparkles, Pencil } from 'lucide-react'
import { api } from '../api/client'
import type { ArtifactT } from '../deck/types'
import { renderMermaid, svgToPng } from '../diagram/mermaid'

interface DiagramPayload {
  mermaid_src: string
  excalidraw_scene: unknown
  canvas_dirty: boolean
  render: unknown
}

export function DiagramEditor() {
  const { artifactId, notebookId } = useParams()
  const navigate = useNavigate()
  const [artifact, setArtifact] = useState<ArtifactT | null>(null)
  const [src, setSrc] = useState('')
  const [svg, setSvg] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [state, setState] = useState<'saved' | 'dirty' | 'saving'>('saved')
  const [repairing, setRepairing] = useState(false)
  const [canvas, setCanvas] = useState(false)
  const excalidrawRef = useRef<unknown>(null)
  const [Excalidraw, setExcalidraw] = useState<React.ComponentType<Record<string, unknown>> | null>(null)
  const [scene, setScene] = useState<{ elements: unknown[]; files?: unknown } | null>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!artifactId) return
    api.get<ArtifactT>(`/api/artifacts/${artifactId}`).then((a) => {
      setArtifact(a)
      const p = a.payload as unknown as DiagramPayload
      setSrc(p.mermaid_src ?? 'flowchart TD\n  A[Start] --> B[End]')
      setCanvas(!!p.canvas_dirty)
    })
  }, [artifactId])

  // render mermaid + persist source/renders (debounced)
  const rerender = useCallback(async (code: string) => {
    const { svg: out, error: err } = await renderMermaid(code)
    if (err) {
      setError(err)
      return
    }
    setError(null)
    setSvg(out!)
    if (saveTimer.current) clearTimeout(saveTimer.current)
    setState('dirty')
    saveTimer.current = setTimeout(async () => {
      setState('saving')
      await api.put(`/api/artifacts/${artifactId}/payload`, {
        payload: { mermaid_src: code, excalidraw_scene: null, canvas_dirty: false, render: 'svg' },
      })
      // always persist the SVG; the PNG is a best-effort extra for PPTX embeds
      let png: string | null = null
      try {
        png = await svgToPng(out!)
      } catch { /* tainted/complex svg — skip png */ }
      await api.post(`/api/artifacts/${artifactId}/renders`, { svg: out, png_base64: png })
      setState('saved')
    }, 700)
  }, [artifactId])

  useEffect(() => {
    if (src && !canvas) void rerender(src)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, canvas])

  const fixWithAI = async () => {
    if (!error) return
    setRepairing(true)
    try {
      const res = await api.post<{ mermaid: string }>(`/api/artifacts/${artifactId}/repair`, {
        src, error,
      })
      setSrc(res.mermaid)
    } finally {
      setRepairing(false)
    }
  }

  const toCanvas = async () => {
    const [{ Excalidraw: Ex, convertToExcalidrawElements }, { parseMermaidToExcalidraw }] =
      await Promise.all([
        import('@excalidraw/excalidraw'),
        import('@excalidraw/mermaid-to-excalidraw'),
      ])
    await import('@excalidraw/excalidraw/index.css')
    try {
      const { elements, files } = await parseMermaidToExcalidraw(src)
      const converted = convertToExcalidrawElements(elements)
      setScene({ elements: converted, files })
      setExcalidraw(() => Ex as never)
      setCanvas(true)
    } catch (e) {
      alert(`Could not convert to canvas: ${e instanceof Error ? e.message : e}`)
    }
  }

  const saveCanvas = async () => {
    const apiRef = excalidrawRef.current as {
      getSceneElements: () => unknown[]
      getFiles: () => unknown
    } | null
    if (!apiRef) return
    const { exportToSvg, exportToBlob } = await import('@excalidraw/excalidraw')
    const elements = apiRef.getSceneElements()
    const files = apiRef.getFiles()
    const svgEl = await exportToSvg({ elements: elements as never, files: files as never, appState: { exportBackground: true } as never })
    const svgStr = new XMLSerializer().serializeToString(svgEl)
    const blob = await exportToBlob({ elements: elements as never, files: files as never, mimeType: 'image/png', appState: { exportBackground: true } as never })
    const png = await new Promise<string>((res) => {
      const r = new FileReader()
      r.onload = () => res((r.result as string).split(',')[1])
      r.readAsDataURL(blob)
    })
    setState('saving')
    await api.put(`/api/artifacts/${artifactId}/payload`, {
      payload: { mermaid_src: src, excalidraw_scene: { elements }, canvas_dirty: true, render: 'svg' },
    })
    await api.post(`/api/artifacts/${artifactId}/renders`, { svg: svgStr, png_base64: png })
    setState('saved')
  }

  const downloadRender = (ext: string) => {
    const a = document.createElement('a')
    a.href = `/api/artifacts/${artifactId}/render.${ext}`
    a.download = `${artifact?.title ?? 'diagram'}.${ext}`
    a.click()
  }

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
          {canvas && <button onClick={saveCanvas} className="rounded-lg border border-stage-line px-2.5 py-1.5 text-[12px] text-stage-muted hover:text-white">Save canvas</button>}
          {['svg', 'png'].map((ext) => (
            <button key={ext} onClick={() => downloadRender(ext)}
              className="flex items-center gap-1.5 rounded-lg border border-stage-line px-2.5 py-1.5 text-[12px] text-stage-muted hover:text-white">
              <Download size={12} /> {ext.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {canvas && Excalidraw ? (
        <div className="min-h-0 flex-1">
          <Excalidraw
            excalidrawAPI={(a: unknown) => (excalidrawRef.current = a)}
            initialData={{ elements: scene?.elements ?? [], files: scene?.files, appState: { viewBackgroundColor: '#ffffff' } }}
          />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <div className="flex w-96 shrink-0 flex-col border-r border-stage-line">
            <div className="flex items-center justify-between px-4 py-2 text-[12px] text-stage-muted">
              Mermaid source
              <button onClick={toCanvas} disabled={!!error}
                className="flex items-center gap-1 rounded-md border border-stage-line px-2 py-1 text-[11px] hover:text-white disabled:opacity-40">
                <Pencil size={11} /> Edit on canvas
              </button>
            </div>
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
                <button onClick={fixWithAI} disabled={repairing}
                  className="mt-2 flex items-center gap-1.5 rounded-lg bg-ws-accent px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-50">
                  {repairing ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />} Fix with AI
                </button>
              </div>
            )}
          </div>
          <div className="flex min-w-0 flex-1 items-center justify-center overflow-auto bg-white p-8">
            {svg ? <div className="mmd-preview" dangerouslySetInnerHTML={{ __html: svg }} />
              : <span className="text-ws-muted">rendering…</span>}
          </div>
        </div>
      )}
    </div>
  )
}
