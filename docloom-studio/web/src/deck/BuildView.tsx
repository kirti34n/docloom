import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { AnimatePresence, motion } from 'motion/react'
import { Loader2, X } from 'lucide-react'
import { api, jobEvents, type JobEvent } from '../api/client'
import { toast } from '../ui/toast'
import type { SlideT, StudioTheme } from './types'
import { ScaledSlide } from './DeckStage'
import { WeaveProgress } from './WeaveProgress'
import { citesOf, normalizeOutline, type WeaveSource, type WeaveUnit } from './buildProgress'

interface NotebookSourceRow {
  id: string
  context_mode: string
}

// Every pipeline (deck/doc/sheet/diagram/infographic/podcast) reports its
// per-unit progress under a different stage name, and only the ones with a
// planning step (deck's slide, doc's section, a split sheet's sheet) attach
// a data.index; a single-shot pipeline (diagram/infographic's body, podcast's
// script, a small sheet's bare "sheet" event) has no index at all, so it
// collapses onto unit 1. This one set covers all of them without needing to
// know which pipeline produced the job.
const PROGRESS_STAGES = new Set(['slide', 'section', 'sheet', 'body', 'script'])

export function BuildView({
  jobId,
  theme,
  deckTitle,
  onDone,
}: {
  jobId: string
  theme: StudioTheme
  deckTitle: string
  onDone: (artifactId: string) => void
}) {
  const { notebookId } = useParams()
  const navigate = useNavigate()
  const [sources, setSources] = useState<WeaveSource[]>([])
  const [units, setUnits] = useState<WeaveUnit[]>([])
  const [slideShaped, setSlideShaped] = useState(false)
  const [slideContent, setSlideContent] = useState<Record<number, SlideT>>({})
  const [docTitle, setDocTitle] = useState(deckTitle)
  const [error, setError] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)

  // the warp: the notebook's enabled sources, independent of the job itself
  // so it is present the instant the build view mounts.
  useEffect(() => {
    if (!notebookId) return
    api
      .get<NotebookSourceRow[]>(`/api/notebooks/${notebookId}/sources`)
      .then((rows) =>
        setSources(rows.map((s, i) => ({ id: s.id, index: i + 1, enabled: s.context_mode !== 'excluded' }))),
      )
      .catch(() => {})
  }, [notebookId])

  useEffect(() => {
    const stop = jobEvents(jobId, (e: JobEvent) => {
      if (e.stage === 'outline' && e.status === 'done') {
        const norm = normalizeOutline(e.data)
        if (norm) {
          if (norm.title) setDocTitle(norm.title)
          setSlideShaped(norm.slideShaped)
          setUnits(norm.items.map((label, i) => ({ key: i + 1, label: label || `Item ${i + 1}`, status: 'pending' })))
        }
      }

      if (PROGRESS_STAGES.has(e.stage)) {
        const data = e.data as { index?: number; slide?: SlideT } | null
        const idx = data?.index ?? 1
        const slide = data?.slide
        setUnits((prev) => {
          const next = [...prev]
          while (next.length < idx) next.push({ key: next.length + 1, label: '', status: 'pending' })
          const incoming: WeaveUnit['status'] =
            e.status === 'done' ? 'done' : e.status === 'skipped' ? 'skipped' : 'running'
          // every pipeline emits "skipped" (a unit's generation failed) and
          // then unconditionally emits "done" right after it, carrying the
          // fallback content it shipped instead: keep "skipped" sticky so
          // that trailing "done" doesn't read as a normal success.
          const status = next[idx - 1]?.status === 'skipped' ? 'skipped' : incoming
          next[idx - 1] = {
            key: idx,
            label: next[idx - 1]?.label || e.detail || `Item ${idx}`,
            status,
            citedSourceIds: slide ? citesOf(slide) : next[idx - 1]?.citedSourceIds,
          }
          return next
        })
        if (slide) setSlideContent((prev) => ({ ...prev, [idx]: slide }))
      }

      if (e.stage === 'save' && e.status === 'done') {
        const data = e.data as { artifact_id: string }
        setTimeout(() => onDone(data.artifact_id), 500)
      }
      if (e.stage === 'job' && (e.status === 'failed' || e.status === 'cancelled')) {
        setError(e.status === 'cancelled' ? e.detail || 'Cancelled.' : e.detail || 'Generation failed.')
      }
    })
    return stop
  }, [jobId, onDone])

  const cancel = async () => {
    setCancelling(true)
    try {
      await api.post(`/api/jobs/${jobId}/cancel`, {})
    } catch (e) {
      setCancelling(false)
      toast.error(`Could not cancel: ${e instanceof Error ? e.message : e}`)
    }
  }

  const previewDoc = { title: docTitle, slides: [] }

  return (
    <div className="min-h-full bg-stage-bg px-8 py-8">
      <div className="mx-auto max-w-5xl">
        <div className="flex items-center gap-3">
          <span className="font-display text-lg font-semibold text-white">{docTitle}</span>
          <div className="ml-auto flex items-center gap-3">
            {error ? (
              <>
                <span role="status" className="flex items-center gap-1.5 text-sm text-madder">
                  <X size={14} /> {error}
                </span>
                <button
                  onClick={() => navigate(`/n/${notebookId}`)}
                  className="rounded-[var(--radius-sm)] border border-stage-line px-3 py-1.5 text-xs text-stage-muted hover:text-white"
                >
                  Back to notebook
                </button>
              </>
            ) : (
              <button
                onClick={cancel}
                disabled={cancelling}
                className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-stage-line px-3 py-1.5 text-xs text-stage-muted hover:text-white disabled:opacity-50"
              >
                {cancelling && <Loader2 size={12} className="animate-spin" />}
                Cancel
              </button>
            )}
          </div>
        </div>

        <div className="mt-6">
          <WeaveProgress sources={sources} units={units} />
        </div>

        {slideShaped && units.length > 0 && (
          <div className="mt-8 grid grid-cols-2 gap-5">
            {units.map((unit, i) => {
              const index = i + 1
              const slide = slideContent[index]
              return (
                <div key={unit.key} className="overflow-hidden rounded-[var(--radius)] border border-stage-line">
                  <AnimatePresence mode="wait">
                    {slide ? (
                      <motion.div
                        key="real"
                        initial={{ opacity: 0, scale: 0.98 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ duration: 0.3 }}
                      >
                        <ScaledSlide slide={slide} doc={previewDoc} theme={theme} />
                      </motion.div>
                    ) : (
                      <motion.div
                        key="skeleton"
                        className="flex aspect-video flex-col justify-center gap-3 bg-stage-line/30 px-6"
                        animate={{ opacity: [0.4, 0.8, 0.4] }}
                        transition={{ repeat: Infinity, duration: 1.4 }}
                      >
                        <div className="text-sm text-stage-muted">
                          {index}. {unit.label}
                        </div>
                        <div className="h-2 w-2/3 rounded bg-stage-line" />
                        <div className="h-2 w-1/2 rounded bg-stage-line" />
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
