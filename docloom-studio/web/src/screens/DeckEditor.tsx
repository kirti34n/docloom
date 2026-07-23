import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Check, Download, Loader2, Play, Redo2, Undo2 } from 'lucide-react'
import { api } from '../api/client'
import { toast } from '../ui/toast'
import type { ArtifactT, StudioTheme } from '../deck/types'
import { themeVars } from '../deck/types'
import { EditableSlide } from '../deck/EditableSlide'
import { SlideRail } from '../deck/SlideRail'
import { Inspector } from '../deck/Inspector'
import { useDeck, deckHistory } from '../deck/deckStore'
import { useThemes, themeByName } from '../deck/useThemes'
import '../deck/stage.css'
import '../deck/editor.css'

const EXPORTS = ['pptx', 'pdf', 'docx', 'html'] as const

// the editable stage's own footprint: 1280x720, capped by whichever of
// max-w-6xl or the available height is tighter, so a short window never
// forces the slide box taller than what's on screen.
const STAGE_MAX_W = 1152 // max-w-6xl
const STAGE_PAD = 64 // p-8 on each axis

function EditableCanvas({ theme }: { theme: StudioTheme }) {
  const selected = useDeck((s) => s.selected)
  const outer = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(0.6)

  useLayoutEffect(() => {
    const el = outer.current
    if (!el) return
    const resize = () => {
      const w = Math.min(el.clientWidth - STAGE_PAD, STAGE_MAX_W)
      const h = el.clientHeight - STAGE_PAD
      setScale(Math.max(0.1, Math.min(1, w / 1280, h / 720)))
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  if (!selected) return null
  const vars = themeVars(theme) as React.CSSProperties
  return (
    <div ref={outer} className="flex min-w-0 flex-1 items-center justify-center overflow-auto p-8">
      <div
        className="deck-scale-wrap border border-stage-line"
        style={{ width: 1280 * scale, height: 720 * scale }}
      >
        <div style={{ width: 1280, height: 720, transform: `scale(${scale})`, transformOrigin: 'top left', ...vars }}>
          <EditableSlide key={selected} slideId={selected} />
        </div>
      </div>
    </div>
  )
}

export function DeckEditor() {
  const { artifactId, notebookId } = useParams()
  const navigate = useNavigate()
  const themes = useThemes()
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [exporting, setExporting] = useState<string | null>(null)
  const load = useDeck((s) => s.load)
  const title = useDeck((s) => s.title)
  const themeName = useDeck((s) => s.themeName)
  const saving = useDeck((s) => s.saving)
  const dirty = useDeck((s) => s.dirty)
  const findings = useDeck((s) => s.findings)

  useEffect(() => {
    if (!artifactId) return
    api.get<ArtifactT>(`/api/artifacts/${artifactId}`).then((a) => {
      load(a)
      setLoaded(true)
    }).catch((e) => setLoadError(e instanceof Error ? e.message : String(e)))
  }, [artifactId, load])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // let Tiptap own undo while a text region is focused
      const inEditor = (document.activeElement as HTMLElement)?.closest?.('.ProseMirror')
      if (inEditor) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        e.shiftKey ? deckHistory.redo() : deckHistory.undo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (loadError)
    return (
      <div className="flex h-full items-center justify-center bg-stage-bg text-madder text-sm">{loadError}</div>
    )

  if (!loaded || themes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center bg-stage-bg text-stage-muted">
        <Loader2 className="animate-spin" />
      </div>
    )
  }

  const theme = themeByName(themes, themeName)!
  const errorCount = findings.filter((f) => f.severity === 'error').length

  const exportAs = async (format: string) => {
    setExporting(format)
    try {
      const res = await api.post<{ url: string; filename: string }>(
        `/api/artifacts/${artifactId}/export`,
        { format },
      )
      const a = document.createElement('a')
      a.href = res.url
      a.download = res.filename
      a.click()
    } catch (e) {
      toast.error(`Export failed: ${e instanceof Error ? e.message : e}`)
    } finally {
      setExporting(null)
    }
  }

  return (
    <div className="flex h-full flex-col bg-stage-bg">
      <div className="flex items-center gap-3 border-b border-stage-line px-5 py-2.5">
        <button
          onClick={() => navigate(`/n/${notebookId}`)}
          className="text-xs text-stage-muted hover:text-white"
        >
          ← Notebook
        </button>
        <span className="font-display text-base font-semibold text-white">{title}</span>

        <div className="ml-2 flex items-center gap-1">
          <button
            onClick={deckHistory.undo}
            className="rounded-[var(--radius-sm)] p-1.5 text-stage-muted hover:text-white"
            title="Undo"
          >
            <Undo2 size={15} />
          </button>
          <button
            onClick={deckHistory.redo}
            className="rounded-[var(--radius-sm)] p-1.5 text-stage-muted hover:text-white"
            title="Redo"
          >
            <Redo2 size={15} />
          </button>
        </div>

        <span className="text-xs text-stage-muted">
          {saving ? (
            <span className="flex items-center gap-1"><Loader2 size={12} className="animate-spin" /> Saving…</span>
          ) : dirty ? (
            'Unsaved'
          ) : (
            <span className="flex items-center gap-1"><Check size={12} /> Saved</span>
          )}
        </span>

        {errorCount > 0 && (
          <span className="rounded-[var(--radius-sm)] border border-madder px-2 py-0.5 text-2xs text-madder">
            {errorCount} issue{errorCount > 1 ? 's' : ''}
          </span>
        )}

        <div className="ml-auto flex items-center gap-1.5">
          {EXPORTS.map((fmt) => (
            <button
              key={fmt}
              onClick={() => exportAs(fmt)}
              disabled={exporting !== null}
              className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-stage-line px-2.5 py-1.5 text-xs text-stage-muted hover:text-white disabled:opacity-40"
            >
              {exporting === fmt ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
              {fmt.toUpperCase()}
            </button>
          ))}
          <button
            onClick={() => navigate(`/n/${notebookId}/deck/${artifactId}/present`)}
            className="ml-1 flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-brass bg-brass px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
          >
            <Play size={12} /> Present
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <SlideRail theme={theme} />
        <EditableCanvas theme={theme} />
        <Inspector themes={themes} />
      </div>
    </div>
  )
}
