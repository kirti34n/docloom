import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, X } from 'lucide-react'
import { api } from '../api/client'

interface Chunk {
  chunk_ix: number | null
  page: number | null
  section: string
  text: string
}
interface Content {
  title: string
  chunks: Chunk[]
}

/** Reads a source's extracted text and scrolls to + highlights the cited passage. */
export function SourceReader({
  sourceId,
  highlight,
  onClose,
}: {
  sourceId: string
  highlight?: string
  onClose: () => void
}) {
  const [content, setContent] = useState<Content | null>(null)
  const [error, setError] = useState<string | null>(null)
  const hlRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setContent(null)
    setError(null)
    api
      .get<Content>(`/api/sources/${sourceId}/content`)
      .then(setContent)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [sourceId])

  const hlIndex = useMemo(() => {
    if (!content || !highlight) return -1
    const needle = highlight.trim().slice(0, 60)
    return content.chunks.findIndex((c) => needle.length > 0 && c.text.includes(needle))
  }, [content, highlight])

  useEffect(() => {
    if (hlIndex >= 0) hlRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [hlIndex, content])

  return (
    <div className="flex w-96 shrink-0 flex-col border-l border-ws-line bg-ws-panel">
      <div className="flex items-center justify-between border-b border-ws-line px-4 py-2.5">
        <span className="truncate text-[13px] font-medium" title={content?.title}>
          {content?.title ?? 'Source'}
        </span>
        <button
          onClick={onClose}
          aria-label="Close reader"
          className="text-ws-muted hover:text-ws-ink"
        >
          <X size={15} />
        </button>
      </div>
      <div className="flex-1 space-y-3 overflow-auto px-4 py-4 text-[13px] leading-relaxed">
        {error ? (
          <p className="text-ws-danger">{error}</p>
        ) : !content ? (
          <div className="flex justify-center pt-8">
            <Loader2 className="animate-spin text-ws-muted" />
          </div>
        ) : content.chunks.length === 0 ? (
          <p className="text-ws-muted">No extracted text for this source.</p>
        ) : (
          content.chunks.map((c, i) => (
            <div
              key={i}
              ref={i === hlIndex ? hlRef : undefined}
              className={`rounded-lg px-3 py-2 ${
                i === hlIndex ? 'bg-ws-accent/10 ring-1 ring-ws-accent/40' : ''
              }`}
            >
              {(c.page || c.section) && (
                <div className="mb-1 text-[11px] uppercase tracking-wide text-ws-muted">
                  {c.section}
                  {c.section && c.page ? ' · ' : ''}
                  {c.page ? `p.${c.page}` : ''}
                </div>
              )}
              <p className="whitespace-pre-wrap text-ws-ink">{c.text}</p>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
