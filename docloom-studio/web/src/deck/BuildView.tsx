import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { Check, Loader2, X } from 'lucide-react'
import { jobEvents, type JobEvent } from '../api/client'
import type { SlideT, StudioTheme } from './types'
import { ScaledSlide } from './DeckStage'

const STAGES = ['context', 'outline', 'body', 'lint', 'save'] as const
const STAGE_LABELS: Record<string, string> = {
  context: 'Reading context',
  outline: 'Planning',
  body: 'Writing',
  lint: 'Checking',
  save: 'Finishing',
}
// pipeline-specific stage names all map to the generic "body" rail step
const BODY_STAGES = new Set(['slide', 'section', 'sheet'])

interface OutlineSlide {
  title: string
  layout: string
}

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
  const [stageStatus, setStageStatus] = useState<Record<string, string>>({})
  const [outline, setOutline] = useState<OutlineSlide[]>([])
  const [slides, setSlides] = useState<Record<number, SlideT>>({})
  const [docTitle, setDocTitle] = useState(deckTitle)
  const [error, setError] = useState<string | null>(null)
  const railRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const stop = jobEvents(
      jobId,
      (e: JobEvent) => {
        const stage = BODY_STAGES.has(e.stage) ? 'body' : e.stage
        setStageStatus((s) => ({ ...s, [stage]: e.status }))
        if (e.stage === 'outline' && e.status === 'done') {
          const data = e.data as { deck_title: string; slides: OutlineSlide[] }
          setDocTitle(data.deck_title)
          setOutline(data.slides)
        }
        if (e.stage === 'slide' && e.status === 'done') {
          const data = e.data as { index: number; slide: SlideT }
          setSlides((prev) => ({ ...prev, [data.index]: data.slide }))
        }
        if (e.stage === 'save' && e.status === 'done') {
          const data = e.data as { artifact_id: string }
          setTimeout(() => onDone(data.artifact_id), 500)
        }
        if (e.stage === 'job' && (e.status === 'failed' || e.status === 'cancelled')) {
          setError(e.detail || e.status)
        }
      },
    )
    return stop
  }, [jobId, onDone])

  const previewDoc = { title: docTitle, slides: [] }

  return (
    <div className="min-h-full bg-stage-bg px-8 py-8 text-white">
      <div className="mx-auto max-w-5xl">
        <div className="flex items-center gap-3">
          <span className="font-display text-lg font-semibold">{docTitle}</span>
          {error && (
            <span className="flex items-center gap-1 text-[13px] text-red-400">
              <X size={14} /> {error}
            </span>
          )}
        </div>

        {/* stage rail */}
        <div className="mt-5 flex items-center gap-2">
          {STAGES.map((stage, i) => {
            const status = stageStatus[stage]
            const state = status === 'done' ? 'done' : status ? 'run' : 'idle'
            return (
              <div key={stage} className="flex items-center gap-2">
                <div
                  className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] transition-colors ${
                    state === 'done'
                      ? 'border-emerald-500/60 text-emerald-300'
                      : state === 'run'
                        ? 'border-ws-accent/70 text-blue-300'
                        : 'border-stage-line text-stage-muted'
                  }`}
                >
                  {state === 'done' ? (
                    <Check size={12} />
                  ) : state === 'run' ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : null}
                  {STAGE_LABELS[stage]}
                </div>
                {i < STAGES.length - 1 && (
                  <div
                    className={`h-px w-5 ${
                      stageStatus[stage] === 'done' ? 'bg-emerald-500/60' : 'bg-stage-line'
                    }`}
                  />
                )}
              </div>
            )
          })}
        </div>

        {/* skeleton → real slide grid */}
        <div ref={railRef} className="mt-8 grid grid-cols-2 gap-5">
          {outline.map((item, i) => {
            const index = i + 1
            const slide = slides[index]
            return (
              <div key={i} className="overflow-hidden rounded-xl border border-stage-line">
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
                      <div className="text-[13px] text-stage-muted">
                        {index}. {item.title}
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
      </div>
    </div>
  )
}
