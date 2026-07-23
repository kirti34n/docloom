import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Maximize2, Minimize2, PanelRightClose, X } from 'lucide-react'
import { api } from '../api/client'
import { Eyebrow, IconButton } from '../ui'

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
  index,
  onClose,
  onCollapse,
}: {
  sourceId: string
  highlight?: string
  index?: number
  onClose: () => void
  onCollapse?: () => void
}) {
  const [content, setContent] = useState<Content | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
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
    <div className={`flex shrink-0 flex-col border-l border-ws-line bg-ws-panel transition-[width] duration-[var(--dur)] ${expanded ? 'w-[36rem] max-w-[45vw]' : 'w-96'}`}>
      <div className="flex items-center justify-between gap-3 border-b border-ws-line px-4 py-2.5">
        <div className="min-w-0">
          {typeof index === 'number' && <Eyebrow>Source {String(index).padStart(2, '0')}</Eyebrow>}
          <span className="block truncate text-sm font-medium" title={content?.title}>
            {content?.title ?? 'Source'}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <IconButton label={expanded ? 'Collapse reading pane' : 'Expand reading pane'} onClick={() => setExpanded((v) => !v)}>
            {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </IconButton>
          {onCollapse && (
            <IconButton label="Hide reader" onClick={onCollapse}>
              <PanelRightClose size={15} />
            </IconButton>
          )}
          <IconButton label="Close reader" onClick={onClose}>
            <X size={15} />
          </IconButton>
        </div>
      </div>
      <div className="flex-1 space-y-3 overflow-auto px-4 py-4 text-base leading-[1.6]">
        {error ? (
          <p className="text-madder">{error}</p>
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
              className={`rounded-[var(--radius)] px-3 py-2 ${
                i === hlIndex ? 'bg-woad/10 ring-1 ring-woad/40' : ''
              }`}
            >
              {(c.page || c.section) && (
                <Eyebrow className="mb-1">
                  {c.section}
                  {c.section && c.page ? ' · ' : ''}
                  {c.page ? `p.${c.page}` : ''}
                </Eyebrow>
              )}
              <p className="whitespace-pre-wrap text-ws-ink">{c.text}</p>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
