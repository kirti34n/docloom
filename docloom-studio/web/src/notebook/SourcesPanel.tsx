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

interface Source {
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

export function SourcesPanel({ notebookId }: { notebookId: string }) {
  const [sources, setSources] = useState<Source[]>([])
  const [adding, setAdding] = useState<null | 'url' | 'text'>(null)
  const [urlVal, setUrlVal] = useState('')
  const [textVal, setTextVal] = useState('')
  const [researchVal, setResearchVal] = useState('')
  const [researchStage, setResearchStage] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const load = () =>
    api.get<Source[]>(`/api/notebooks/${notebookId}/sources`).then(setSources)

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
    await fetch(`/api/notebooks/${notebookId}/sources/file`, { method: 'POST', body: fd })
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

  const RESEARCH_LABELS: Record<string, string> = {
    plan: 'Planning searches…',
    search: 'Searching the web…',
    read: 'Reading pages…',
    ingest: 'Adding sources…',
  }

  const runResearch = async () => {
    const q = researchVal.trim()
    if (!q || researchStage) return
    setResearchVal('')
    setResearchStage('plan')
    const res = await api.post<{ job_id: string }>(
      `/api/notebooks/${notebookId}/research`, { query: q })
    jobEvents(
      res.job_id,
      (e) => {
        if (RESEARCH_LABELS[e.stage]) setResearchStage(e.stage)
        if (e.stage === 'ingest' && e.status === 'done') load()
      },
      () => {
        setResearchStage(null)
        load()
      },
    )
  }

  return (
    <div className="flex h-full w-72 shrink-0 flex-col border-r border-ws-line bg-ws-panel">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-[13px] font-semibold">Sources</h2>
        <div className="flex gap-1">
          <button title="Upload file" onClick={() => fileInput.current?.click()}
            className="rounded p-1 text-ws-muted hover:text-ws-ink">
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
          <div className="flex items-center gap-2 rounded-lg border border-ws-accent/40 bg-ws-accent/5 px-3 py-2 text-[12px] text-ws-ink">
            <Loader2 size={13} className="animate-spin text-ws-accent" />
            {RESEARCH_LABELS[researchStage] ?? 'Researching…'}
          </div>
        ) : (
          <div className="flex items-center gap-1.5 rounded-lg border border-ws-line px-2.5 py-1.5">
            <Search size={13} className="text-ws-muted" />
            <input value={researchVal} onChange={(e) => setResearchVal(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && runResearch()}
              placeholder="Research a topic on the web…"
              className="flex-1 bg-transparent text-[12px] outline-none" />
          </div>
        )}
      </div>

      <div className="px-3 pb-1 text-[10px] font-medium uppercase tracking-wide text-ws-muted">
        or add your own
      </div>
      <div className="flex gap-1 px-3 pb-2">
        <button onClick={() => fileInput.current?.click()}
          className="flex-1 rounded-md border border-ws-line py-1.5 text-[11px] text-ws-muted hover:text-ws-ink">
          File
        </button>
        <button onClick={() => setAdding(adding === 'url' ? null : 'url')}
          className="flex-1 rounded-md border border-ws-line py-1.5 text-[11px] text-ws-muted hover:text-ws-ink">
          URL
        </button>
        <button onClick={() => setAdding(adding === 'text' ? null : 'text')}
          className="flex-1 rounded-md border border-ws-line py-1.5 text-[11px] text-ws-muted hover:text-ws-ink">
          Text
        </button>
      </div>

      {adding === 'url' && (
        <div className="px-3 pb-2">
          <input value={urlVal} onChange={(e) => setUrlVal(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addUrl()}
            placeholder="Web page or YouTube link…" autoFocus
            className="w-full rounded-md border border-ws-line px-2 py-1.5 text-[12px]" />
        </div>
      )}
      {adding === 'text' && (
        <div className="px-3 pb-2">
          <textarea value={textVal} onChange={(e) => setTextVal(e.target.value)}
            placeholder="Paste text…" rows={3} autoFocus
            className="w-full resize-none rounded-md border border-ws-line px-2 py-1.5 text-[12px]" />
          <button onClick={addText}
            className="mt-1 w-full rounded-md bg-ws-ink py-1.5 text-[11px] text-white">Add</button>
        </div>
      )}

      <div className="flex-1 overflow-auto px-3 pb-3">
        {sources.length === 0 ? (
          <p className="mt-6 text-center text-[12px] text-ws-muted">
            Add documents, links, or text. The agent grounds its work in them.
          </p>
        ) : (
          <ul className="space-y-2">
            {sources.map((s) => {
              const Icon = KIND_ICON[s.kind] ?? FileText
              return (
                <li key={s.id} className="group rounded-lg border border-ws-line p-2.5">
                  <div className="flex items-start gap-2">
                    {s.status === 'pending' ? (
                      <Loader2 size={14} className="mt-0.5 animate-spin text-ws-accent" />
                    ) : s.status === 'failed' ? (
                      <AlertCircle size={14} className="mt-0.5 text-ws-danger" />
                    ) : s.status === 'stale' ? (
                      <AlertTriangle size={14} className="mt-0.5 text-ws-warn" />
                    ) : (
                      <Icon size={14} className="mt-0.5 text-ws-muted" />
                    )}
                    <span className="flex-1 truncate text-[12.5px]" title={s.title}>
                      {s.title}
                    </span>
                    <button onClick={() => remove(s.id)}
                      aria-label="Remove source"
                      className="hidden text-ws-muted hover:text-ws-danger group-hover:block">
                      <Trash2 size={13} />
                    </button>
                  </div>
                  {s.error && <p className="mt-1 text-[11px] text-ws-danger">{s.error}</p>}
                  {s.status === 'stale' && (
                    <div className="mt-1 flex items-center justify-between gap-2">
                      <span className="text-[11px] text-ws-warn">
                        Index out of date — re-embed to make it searchable.
                      </span>
                      <button onClick={() => reingest(s.id)}
                        className="shrink-0 rounded border border-ws-line px-1.5 py-0.5 text-[10.5px] hover:bg-ws-bg">
                        Re-ingest
                      </button>
                    </div>
                  )}
                  {s.status === 'ready' && (
                    <div className="mt-2 flex gap-1">
                      {MODES.map(([m, label]) => (
                        <button key={m} onClick={() => setMode(s.id, m)}
                          className={`flex-1 rounded py-0.5 text-[10.5px] ${
                            s.context_mode === m
                              ? 'bg-ws-ink text-white'
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
