import { useEffect, useRef, useState } from 'react'
import {
  AlertCircle,
  AlertTriangle,
  FileText,
  Globe,
  Loader2,
  Plus,
  Search,
  Trash2,
  Type,
} from 'lucide-react'
import { api, jobEvents } from '../api/client'
import { Button, Empty, Eyebrow } from '../ui'
import { toast } from '../ui/toast'

export interface Source {
  id: string
  kind: string
  title: string
  status: string
  context_mode: string
  url?: string
  error?: string | null
}

const MODES = [
  ['full', 'Full'],
  ['insights', 'Summary'],
  ['excluded', 'Off'],
] as const

const KIND_ICON: Record<string, typeof FileText> = {
  file: FileText,
  url: Globe,
  text: Type,
  research: Globe,
}

const RESEARCH_LABELS: Record<string, string> = {
  plan: 'Planning searches…',
  search: 'Searching the web…',
  read: 'Reading pages…',
  ingest: 'Adding sources…',
}

export function SourcesPanel({
  notebookId,
  activeSourceId,
  onOpenSource,
  onSourcesChange,
}: {
  notebookId: string
  activeSourceId?: string
  onOpenSource: (sourceId: string) => void
  onSourcesChange?: (sources: Source[]) => void
}) {
  const [sources, setSources] = useState<Source[]>([])
  const [adding, setAdding] = useState<null | 'url' | 'text'>(null)
  const [urlVal, setUrlVal] = useState('')
  const [textVal, setTextVal] = useState('')
  const [researchVal, setResearchVal] = useState('')
  const [researchStage, setResearchStage] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const load = () =>
    api.get<Source[]>(`/api/notebooks/${notebookId}/sources`).then((list) => {
      setSources(list)
      onSourcesChange?.(list)
    })

  useEffect(() => {
    load()
  }, [notebookId])

  // poll while anything is still ingesting
  useEffect(() => {
    if (!sources.some((s) => s.status === 'pending')) return
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [sources])

  const uploadFile = async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`/api/notebooks/${notebookId}/sources/file`, { method: 'POST', body: fd })
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      let message = text || res.statusText
      try {
        const body = JSON.parse(text) as { detail?: unknown }
        if (typeof body.detail === 'string') message = body.detail
      } catch {
        // not JSON: message already falls back to the raw response text
      }
      toast.error(`Couldn't add ${file.name}: ${message}`)
      return
    }
    load()
  }

  const addUrl = async () => {
    if (!urlVal.trim()) return
    await api.post(`/api/notebooks/${notebookId}/sources/url`, { url: urlVal })
    setUrlVal('')
    setAdding(null)
    load()
  }

  const addText = async () => {
    if (!textVal.trim()) return
    await api.post(`/api/notebooks/${notebookId}/sources/text`, {
      title: textVal.slice(0, 40),
      text: textVal,
    })
    setTextVal('')
    setAdding(null)
    load()
  }

  const setMode = async (id: string, mode: string) => {
    setSources((s) => s.map((x) => (x.id === id ? { ...x, context_mode: mode } : x)))
    await api.patch(`/api/sources/${id}`, { context_mode: mode })
  }

  const remove = async (id: string) => {
    await api.del(`/api/sources/${id}`)
    load()
  }

  const reingest = async (id: string) => {
    setSources((s) => s.map((x) => (x.id === id ? { ...x, status: 'pending' } : x)))
    await api.post(`/api/sources/${id}/reingest`, {})
    load()
  }

  const runResearch = async () => {
    const q = researchVal.trim()
    if (!q || researchStage) return
    setResearchVal('')
    setResearchStage('plan')
    try {
      const res = await api.post<{ job_id: string }>(`/api/notebooks/${notebookId}/research`, { query: q })
      jobEvents(
        res.job_id,
        (e) => {
          if (RESEARCH_LABELS[e.stage]) setResearchStage(e.stage)
          if (e.stage === 'ingest' && e.status === 'done') load()
          if (e.stage === 'research' && e.status === 'failed') {
            toast.error(e.detail || 'No readable pages were found for that topic.')
          }
          if (e.stage === 'job' && e.status === 'failed') {
            toast.error(`Research failed: ${e.detail || 'unknown error'}`)
          }
        },
        () => {
          setResearchStage(null)
          load()
        },
      )
    } catch (e) {
      setResearchStage(null)
      toast.error(`Couldn't start research: ${e instanceof Error ? e.message : e}`)
    }
  }

  return (
    <div className="flex h-full w-72 shrink-0 flex-col border-r border-ws-line bg-ws-panel">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-[13px] font-semibold">Sources</h2>
        <div className="flex gap-1">
          <button title="Upload file" onClick={() => fileInput.current?.click()}
            className="rounded-[var(--radius-sm)] p-1 text-ws-muted hover:text-ws-ink">
            <Plus size={15} />
          </button>
        </div>
        <input ref={fileInput} type="file" hidden
          accept=".pdf,.docx,.pptx,.xlsx,.xlsm,.csv,.html,.htm,.epub,.txt,.md,.markdown,.rst,.json"
          onChange={(e) => e.target.files?.[0] && uploadFile(e.target.files[0])} />
      </div>

      {/* research the web */}
      <div className="px-3 pb-2">
        {researchStage ? (
          <div className="flex items-center gap-2 rounded-[var(--radius)] border border-woad/40 bg-woad/5 px-3 py-2 text-[12px] text-ws-ink">
            <Loader2 size={13} className="animate-spin text-woad" />
            {RESEARCH_LABELS[researchStage] ?? 'Researching…'}
          </div>
        ) : (
          <div className="ds-focusable flex items-center gap-1.5 rounded-[var(--radius)] border border-ws-line px-2.5 py-1.5">
            <Search size={13} className="text-ws-muted" />
            <input value={researchVal} onChange={(e) => setResearchVal(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && runResearch()}
              placeholder="Research a topic on the web…"
              className="flex-1 bg-transparent text-[12px] outline-none" />
          </div>
        )}
      </div>

      <Eyebrow className="px-3 pb-1">Add a source</Eyebrow>
      <div className="flex gap-1 px-3 pb-2">
        <button onClick={() => fileInput.current?.click()}
          className="flex-1 rounded-[var(--radius-sm)] border border-ws-line py-1.5 text-[11px] text-ws-muted hover:text-ws-ink">
          File
        </button>
        <button onClick={() => setAdding(adding === 'url' ? null : 'url')}
          className="flex-1 rounded-[var(--radius-sm)] border border-ws-line py-1.5 text-[11px] text-ws-muted hover:text-ws-ink">
          URL
        </button>
        <button onClick={() => setAdding(adding === 'text' ? null : 'text')}
          className="flex-1 rounded-[var(--radius-sm)] border border-ws-line py-1.5 text-[11px] text-ws-muted hover:text-ws-ink">
          Text
        </button>
      </div>

      {adding === 'url' && (
        <div className="px-3 pb-2">
          <input value={urlVal} onChange={(e) => setUrlVal(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addUrl()}
            placeholder="Web page or YouTube link…" autoFocus
            className="w-full rounded-[var(--radius-sm)] border border-ws-line px-2 py-1.5 text-[12px]" />
        </div>
      )}
      {adding === 'text' && (
        <div className="px-3 pb-2">
          <textarea value={textVal} onChange={(e) => setTextVal(e.target.value)}
            placeholder="Paste text…" rows={3} autoFocus
            className="w-full resize-none rounded-[var(--radius-sm)] border border-ws-line px-2 py-1.5 text-[12px]" />
          <button onClick={addText}
            className="mt-1 w-full rounded-[var(--radius-sm)] bg-ws-ink py-1.5 text-[11px] text-ws-bg">Add</button>
        </div>
      )}

      <div className="flex-1 overflow-auto px-3 pb-3">
        {sources.length === 0 ? (
          <Empty
            title="No sources yet"
            body="Add documents, links, or text. The agent grounds every answer in them."
            action={<Button variant="quiet" onClick={() => fileInput.current?.click()}>Upload a file</Button>}
          />
        ) : (
          <ul className="space-y-2">
            {sources.map((s, idx) => {
              const Icon = KIND_ICON[s.kind] ?? FileText
              const enabled = s.context_mode !== 'excluded'
              return (
                <li key={s.id} data-warp-id={s.id}
                  className={`group relative overflow-hidden rounded-[var(--radius)] border py-2.5 pl-3 pr-2.5 ${
                    s.id === activeSourceId ? 'border-woad' : 'border-ws-line'
                  }`}>
                  <span aria-hidden="true" className="absolute inset-y-0 left-0 w-[2px]"
                    style={{ background: enabled ? 'var(--woad)' : 'var(--rule)' }} />
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 shrink-0 font-mono text-[10.5px] leading-4 text-ws-muted">
                      {String(idx + 1).padStart(2, '0')}
                    </span>
                    {s.status === 'pending' ? (
                      <Loader2 size={14} className="mt-0.5 shrink-0 animate-spin text-woad" />
                    ) : s.status === 'failed' ? (
                      <AlertCircle size={14} className="mt-0.5 shrink-0 text-madder" />
                    ) : s.status === 'stale' ? (
                      <AlertTriangle size={14} className="mt-0.5 shrink-0 text-ws-warn" />
                    ) : (
                      <Icon size={14} className="mt-0.5 shrink-0 text-ws-muted" />
                    )}
                    <button onClick={() => onOpenSource(s.id)} title={s.title}
                      className="min-w-0 flex-1 truncate text-left text-[12.5px] text-ws-ink hover:text-woad hover:underline underline-offset-2">
                      {s.title}
                    </button>
                    <button onClick={() => remove(s.id)}
                      aria-label="Remove source"
                      className="hidden shrink-0 text-ws-muted hover:text-madder group-hover:block">
                      <Trash2 size={13} />
                    </button>
                  </div>
                  {s.error && <p className="mt-1 text-[11px] text-madder">{s.error}</p>}
                  {s.status === 'stale' && (
                    <div className="mt-1 flex items-center justify-between gap-2">
                      <span className="text-[11px] text-ws-warn">
                        Index out of date. Re-embed to make it searchable.
                      </span>
                      <button onClick={() => reingest(s.id)}
                        className="shrink-0 rounded-[var(--radius-sm)] border border-ws-line px-1.5 py-0.5 text-[10.5px] hover:bg-ws-bg">
                        Re-ingest
                      </button>
                    </div>
                  )}
                  {s.status === 'ready' && (
                    <div className="mt-2 flex gap-1">
                      {MODES.map(([m, label]) => (
                        <button key={m} onClick={() => setMode(s.id, m)}
                          className={`flex-1 rounded-[var(--radius-sm)] py-0.5 text-[10.5px] ${
                            s.context_mode === m
                              ? 'bg-ws-ink text-ws-bg'
                              : 'bg-ws-bg text-ws-muted hover:text-ws-ink'
                          }`}>
                          {label}
                        </button>
                      ))}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
