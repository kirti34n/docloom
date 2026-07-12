import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { ChevronLeft, ChevronRight, Loader2, NotebookPen, X } from 'lucide-react'
import { api } from '../api/client'
import type { ArtifactT, DocumentT } from './types'
import { useThemes, themeByName } from './useThemes'
import { ScaledSlide } from './DeckStage'

/** Full-viewport presenting for a saved deck: arrow keys / space to
 *  advance, Escape to exit, a slide counter, and a speaker-notes toggle.
 *  Reuses ScaledSlide (DeckStage) for the slide itself, letterboxed to the
 *  viewport via container-query units so it fits regardless of aspect. */
export function PresentMode() {
  const { notebookId, artifactId } = useParams()
  const navigate = useNavigate()
  const themes = useThemes()
  const [doc, setDoc] = useState<DocumentT | null>(null)
  const [themeName, setThemeName] = useState('paper')
  const [index, setIndex] = useState(0)
  const [showNotes, setShowNotes] = useState(false)

  useEffect(() => {
    if (!artifactId) return
    api.get<ArtifactT>(`/api/artifacts/${artifactId}`).then((a) => {
      setDoc(a.payload.ir)
      setThemeName(a.payload.theme_name)
    })
  }, [artifactId])

  const slides = doc?.slides ?? []
  const total = slides.length

  const exit = useCallback(() => {
    navigate(`/n/${notebookId}/deck/${artifactId}`)
  }, [navigate, notebookId, artifactId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        exit()
        return
      }
      if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown') {
        e.preventDefault()
        setIndex((i) => Math.min(i + 1, Math.max(total - 1, 0)))
        return
      }
      if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
        e.preventDefault()
        setIndex((i) => Math.max(i - 1, 0))
        return
      }
      if (e.key.toLowerCase() === 'n') setShowNotes((v) => !v)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [exit, total])

  if (!doc || themes.length === 0) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black text-white/60">
        <Loader2 className="animate-spin" />
      </div>
    )
  }

  const theme = themeByName(themes, themeName)!
  const slide = slides[index]

  if (!slide) {
    return (
      <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 bg-black text-white">
        <p className="font-display text-xl">This deck has no slides yet.</p>
        <button
          onClick={exit}
          className="rounded-[var(--radius-sm)] border border-white/25 px-4 py-2 text-[13px] hover:bg-white/10"
        >
          Exit
        </button>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black" role="dialog" aria-label={`Presenting ${doc.title}`}>
      <div className="flex flex-1 items-center justify-center overflow-hidden p-6 [container-type:size]">
        <div className="aspect-[1280/720] w-[min(100cqw,177.78cqh)]">
          <ScaledSlide slide={slide} doc={doc} theme={theme} />
        </div>
      </div>

      {showNotes && (
        <div className="border-t border-white/15 bg-black px-8 py-4">
          <p className="font-mono text-[11px] uppercase tracking-[0.08em] text-white/50">Speaker notes</p>
          <p className="mt-1.5 max-w-3xl text-[13px] leading-relaxed text-white/85">
            {slide.notes || 'No notes on this slide.'}
          </p>
        </div>
      )}

      <div className="flex items-center gap-4 border-t border-white/15 px-5 py-3 text-white/70">
        <button onClick={exit} className="flex items-center gap-1.5 text-[12px] hover:text-white" title="Exit (Esc)">
          <X size={14} /> Exit
        </button>
        <span className="font-mono text-[12px] text-white/50">
          {index + 1} / {total}
        </span>
        <button
          onClick={() => setShowNotes((v) => !v)}
          className={`flex items-center gap-1.5 text-[12px] hover:text-white ${showNotes ? 'text-brass' : ''}`}
          title="Toggle speaker notes (N)"
        >
          <NotebookPen size={14} /> Notes
        </button>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={() => setIndex((i) => Math.max(i - 1, 0))}
            disabled={index === 0}
            className="rounded-[var(--radius-sm)] border border-white/20 p-1.5 hover:bg-white/10 disabled:opacity-30"
            title="Previous slide (←)"
          >
            <ChevronLeft size={15} />
          </button>
          <button
            onClick={() => setIndex((i) => Math.min(i + 1, total - 1))}
            disabled={index === total - 1}
            className="rounded-[var(--radius-sm)] border border-white/20 p-1.5 hover:bg-white/10 disabled:opacity-30"
            title="Next slide (→ / Space)"
          >
            <ChevronRight size={15} />
          </button>
        </div>
      </div>
    </div>
  )
}
