import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Check, Download, GitBranch, Loader2, Plus, Sparkles } from 'lucide-react'
import { api } from '../api/client'
import { toast } from '../ui/toast'
import { Button } from '../ui'
import type { ArtifactT } from '../deck/types'
import {
  emptyDiagramIR, layoutReportToSkeletons, sceneToDiagramIR,
  type DiagramIR, type DiagramMeta, type LayoutReport, type SceneElementLike,
} from '../diagram/irScene'
// Excalidraw's own stylesheet, imported once. Because this whole module is
// itself lazy-loaded (main.tsx's route wrapper only imports DiagramIRCanvas for
// an IR diagram), this CSS -- like the runtime below -- only ships when an IR
// diagram is actually opened.
import '@excalidraw/excalidraw/index.css'

// The whole @excalidraw/excalidraw runtime, loaded exactly once via a cached
// dynamic import. We mount <Excalidraw> WITH the diagram already in initialData
// (rather than React.lazy + a post-mount updateScene) so the scene is never
// momentarily empty -- an empty mount fires an onChange that would read back as
// an empty IR and wipe the diagram, and its coordinates would sit off-screen.
type ExcalidrawModule = typeof import('@excalidraw/excalidraw')
let excalidrawModule: ExcalidrawModule | null = null
let excalidrawModulePromise: Promise<ExcalidrawModule> | null = null
function loadExcalidraw(): Promise<ExcalidrawModule> {
  if (!excalidrawModulePromise) {
    excalidrawModulePromise = import('@excalidraw/excalidraw').then((m) => {
      excalidrawModule = m
      return m
    })
  }
  return excalidrawModulePromise
}

const RENDER_EXTS = ['svg', 'png'] as const
type RenderExt = (typeof RENDER_EXTS)[number]
type LayoutKind = 'native' | 'dot'

interface DiagramIrPayload {
  type?: string
  diagram_ir?: DiagramIR
  theme_name?: string
  layout?: LayoutKind | 'auto'
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

export function DiagramIRCanvas() {
  const { artifactId, notebookId } = useParams()
  const navigate = useNavigate()
  const [artifact, setArtifact] = useState<ArtifactT | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [svg, setSvg] = useState('')
  const [state, setState] = useState<'saved' | 'dirty' | 'saving'>('saved')
  const [layout, setLayout] = useState<LayoutKind>('native')
  const [renders, setRenders] = useState<{ svg: boolean; png: boolean }>({ svg: false, png: false })
  const [initialReport, setInitialReport] = useState<LayoutReport | null>(null)
  const [mod, setMod] = useState<ExcalidrawModule | null>(excalidrawModule)
  const [excalidrawApi, setExcalidrawApi] =
    useState<import('@excalidraw/excalidraw/types').ExcalidrawImperativeAPI | null>(null)

  // Mutable mirrors of state that timers/callbacks read without risking a
  // stale closure (debounced saves, the toolbar's add-node/add-edge).
  const irRef = useRef<DiagramIR>(emptyDiagramIR())
  const themeNameRef = useRef('paper')
  const layoutRef = useRef<LayoutKind>('native')
  const metaRef = useRef<DiagramMeta>({ direction: 'LR' })
  layoutRef.current = layout

  // `readyRef` gates the onChange read-back: it is only flipped true a beat
  // after the canvas has mounted with its seeded scene, so the mount's own
  // onChange (and any programmatic re-seed) never gets read back and saved.
  const readyRef = useRef(false)
  const seedingRef = useRef(false)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => { loadExcalidraw().then(setMod) }, [])

  /** Push freshly-solved geometry into an already-mounted canvas (used after
   *  every save/re-layout, NOT for the first mount -- that goes through
   *  initialData below). Guarded by `seedingRef` so our own update isn't read
   *  back as a user edit, and re-fitted to the viewport on the next frame. */
  const seedScene = useCallback((report: LayoutReport) => {
    if (!excalidrawApi || !excalidrawModule) return
    const { convertToExcalidrawElements, CaptureUpdateAction } = excalidrawModule
    const elements = convertToExcalidrawElements(layoutReportToSkeletons(report), { regenerateIds: false })
    seedingRef.current = true
    excalidrawApi.updateScene({ elements, captureUpdate: CaptureUpdateAction.NEVER })
    requestAnimationFrame(() => {
      const current = excalidrawApi.getSceneElements()
      if (current.length) excalidrawApi.scrollToContent(current, { fitToContent: true, animate: false })
      setTimeout(() => { seedingRef.current = false }, 150)
    })
  }, [excalidrawApi])

  // Load the artifact, solve its working IR once to seed the canvas, and prime
  // the export-preview pane with the real engine render.
  useEffect(() => {
    if (!artifactId) return
    readyRef.current = false
    setInitialReport(null)
    api.get<ArtifactT>(`/api/artifacts/${artifactId}`).then(async (a) => {
      setArtifact(a)
      const p = a.payload as unknown as DiagramIrPayload
      const ir = p.diagram_ir ?? emptyDiagramIR()
      irRef.current = ir
      themeNameRef.current = p.theme_name ?? 'paper'
      const initialLayout: LayoutKind = p.layout === 'dot' ? 'dot' : 'native'
      setLayout(initialLayout)
      layoutRef.current = initialLayout
      metaRef.current = { id: ir.id, title: ir.title, direction: ir.direction, caption: ir.caption, alt: ir.alt }
      try {
        const report = await api.post<LayoutReport>(`/api/artifacts/${artifactId}/diagram/layout`, {
          diagram_ir: ir, layout: initialLayout, theme_name: themeNameRef.current,
        })
        if (report.warning) toast.info(report.warning)
        setInitialReport(report)
        api.post<{ svg: string }>(`/api/artifacts/${artifactId}/diagram/render`, {
          diagram_ir: ir, theme_name: themeNameRef.current, layout: initialLayout,
        }).then((r) => setSvg(r.svg)).catch(() => {})
      } catch (e) {
        toast.error(`Layout failed: ${e instanceof Error ? e.message : e}`)
      }
    }).catch((e) => setLoadError(e instanceof Error ? e.message : String(e)))
    probeRenders(artifactId).then(setRenders)
  }, [artifactId])

  // The scene the canvas mounts with: the solved geometry, converted to
  // Excalidraw elements, with scrollToContent so it opens fitted and centered.
  const initialData = useMemo(() => {
    if (!mod || !initialReport) return null
    return {
      elements: mod.convertToExcalidrawElements(layoutReportToSkeletons(initialReport), { regenerateIds: false }),
      appState: { viewBackgroundColor: '#0b0f19' },
      scrollToContent: true,
    }
  }, [mod, initialReport])

  // Zoom-to-fit once the seeded canvas is live. initialData's scrollToContent
  // centers but keeps 100% zoom, so a wide diagram overflows the viewport;
  // fitToContent zooms it to fit. Then enable read-back a beat later, so this
  // programmatic viewport change isn't mistaken for a user edit.
  useEffect(() => {
    if (!excalidrawApi || !initialData) return
    const fit = setTimeout(() => {
      const els = excalidrawApi.getSceneElements()
      if (els.length) excalidrawApi.scrollToContent(els, { fitToContent: true, animate: false })
    }, 200)
    const ready = setTimeout(() => { readyRef.current = true }, 700)
    return () => { clearTimeout(fit); clearTimeout(ready) }
  }, [excalidrawApi, initialData])

  /** The one save path: persist the working IR, render it through the exact
   *  engine path export uses (parity), refresh the preview, then re-solve +
   *  re-seed the canvas -- which is also where a dragged node snaps back to its
   *  solved position (Phase A: auto-layout owns coordinates). */
  const applyIr = useCallback(async (next: DiagramIR) => {
    if (!artifactId) return
    irRef.current = next
    metaRef.current = { id: next.id, title: next.title, direction: next.direction, caption: next.caption, alt: next.alt }
    setState('saving')
    try {
      await api.put(`/api/artifacts/${artifactId}/payload`, {
        payload: {
          type: 'diagram_ir', diagram_ir: next,
          theme_name: themeNameRef.current, layout: layoutRef.current, overlay: null, render: 'svg',
        },
      })
      const rendered = await api.post<{ svg: string }>(`/api/artifacts/${artifactId}/diagram/render`, {
        diagram_ir: next, theme_name: themeNameRef.current, layout: layoutRef.current,
      })
      setSvg(rendered.svg)
      const report = await api.post<LayoutReport>(`/api/artifacts/${artifactId}/diagram/layout`, {
        diagram_ir: next, layout: layoutRef.current, theme_name: themeNameRef.current,
      })
      if (report.warning) toast.info(report.warning)
      seedScene(report)
      setRenders(await probeRenders(artifactId))
      setState('saved')
    } catch (e) {
      setState('dirty')
      toast.error(`Save failed: ${e instanceof Error ? e.message : e}`)
    }
  }, [artifactId, seedScene])

  // Debounced structural read-back: fires on every drag tick, so wait for the
  // scene to settle (~600ms). Skipped before the first seed (readyRef) and
  // during our own programmatic re-seeds (seedingRef).
  const handleSceneChange = useCallback((elements: readonly SceneElementLike[]) => {
    if (!readyRef.current || seedingRef.current) return
    setState('dirty')
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      void applyIr(sceneToDiagramIR(elements, metaRef.current))
    }, 600)
  }, [applyIr])

  const addNode = () => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    const id = `n_${Math.random().toString(36).slice(2, 8)}`
    void applyIr({
      ...irRef.current,
      nodes: [...irRef.current.nodes, { id, label: 'New node', type: 'service', sublabel: null, tag: null, group: null }],
    })
  }

  const addEdge = () => {
    const ir = irRef.current
    if (ir.nodes.length < 2) { toast.error('Add at least two nodes before connecting them.'); return }
    const ids = ir.nodes.map((n) => n.id).join(', ')
    const source = window.prompt(`Source node id (one of: ${ids})`, ir.nodes[0].id)
    if (!source) return
    const target = window.prompt(`Target node id (one of: ${ids})`, ir.nodes[ir.nodes.length - 1].id)
    if (!target) return
    if (!ir.nodes.some((n) => n.id === source) || !ir.nodes.some((n) => n.id === target)) {
      toast.error('Unknown node id -- pick one from the list shown.'); return
    }
    if (saveTimer.current) clearTimeout(saveTimer.current)
    void applyIr({ ...ir, edges: [...ir.edges, { source, target, label: null, style: 'solid' }] })
  }

  const changeLayout = (next: LayoutKind) => {
    setLayout(next)
    layoutRef.current = next
    if (saveTimer.current) clearTimeout(saveTimer.current)
    void applyIr(irRef.current)
  }

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

  const Excalidraw = mod?.Excalidraw

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

      <div className="flex items-center gap-2 border-b border-stage-line px-5 py-2">
        <Button variant="quiet" className="text-[12px]" onClick={addNode}><Plus size={12} /> Add node</Button>
        <Button variant="quiet" className="text-[12px]" onClick={addEdge}><GitBranch size={12} /> Add edge</Button>
        <label className="ml-2 flex items-center gap-1.5 text-[12px] text-stage-muted">
          Re-layout
          <select value={layout} onChange={(e) => changeLayout(e.target.value as LayoutKind)}
            className="rounded-[var(--radius-sm)] border border-stage-line bg-stage-bg px-1.5 py-1 text-[12px] text-white">
            <option value="native">native</option>
            <option value="dot">dot</option>
          </select>
        </label>
        <span
          title="Positions are computed by the layout solver. Dragging a node moves it on screen, but it snaps back to its solved position once the edit saves -- edit structure (add/connect/retype/group), not placement."
          className="ml-2 flex items-center gap-1 rounded-full border border-stage-line px-2 py-0.5 text-[11px] text-stage-muted">
          <Sparkles size={11} /> Auto-layout
        </span>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1 border-r border-stage-line">
          {Excalidraw && initialData ? (
            <Excalidraw
              initialData={initialData}
              excalidrawAPI={(a) => setExcalidrawApi(a)}
              onChange={(elements) => handleSceneChange(elements)}
              theme="dark"
            />
          ) : (
            <div className="flex h-full items-center justify-center text-stage-muted"><Loader2 className="animate-spin" /></div>
          )}
        </div>
        <div className="flex w-[40%] shrink-0 flex-col">
          <div className="px-4 py-2 font-mono text-[11px] uppercase leading-4 tracking-[0.08em] text-stage-muted">Export preview</div>
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-white p-6">
            {svg ? <div className="w-full [&_svg]:h-auto [&_svg]:w-full" dangerouslySetInnerHTML={{ __html: svg }} />
              : <span className="text-ws-muted">rendering…</span>}
          </div>
        </div>
      </div>
    </div>
  )
}
