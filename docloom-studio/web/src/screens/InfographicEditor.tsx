import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Check, Download, Loader2, Plus, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import type { ArtifactT } from '../deck/types'

interface Item { label: string; desc: string }
interface AntvSpec { template: string; data: { title: string; lists: Item[] } }
interface IgPayload { style: string; antv: AntvSpec; render: unknown }

// templates that share the {title, lists:[{label,desc}]} shape
const TEMPLATES: Record<string, string[]> = {
  list: ['list-column-vertical-icon-arrow', 'list-grid-badge-card', 'list-column-done-list'],
  steps: ['sequence-steps-badge-card', 'sequence-steps-simple', 'sequence-timeline-done-list'],
  pyramid: ['list-pyramid-badge-card', 'list-pyramid-rounded-rect-node', 'list-pyramid-compact-card'],
  grid: ['list-grid-badge-card', 'list-column-vertical-icon-arrow'],
}

function dataUrlToSvg(url: string): string | null {
  if (!url.startsWith('data:image/svg+xml')) return null
  const comma = url.indexOf(',')
  if (comma < 0) return null
  const body = url.slice(comma + 1)
  return url.slice(0, comma).includes('base64') ? atob(body) : decodeURIComponent(body)
}

export function InfographicEditor() {
  const { artifactId, notebookId } = useParams()
  const navigate = useNavigate()
  const [artifact, setArtifact] = useState<ArtifactT | null>(null)
  const [style, setStyle] = useState('list')
  const [template, setTemplate] = useState('')
  const [title, setTitle] = useState('')
  const [items, setItems] = useState<Item[]>([])
  const [state, setState] = useState<'saved' | 'dirty' | 'saving'>('saved')
  const [error, setError] = useState<string | null>(null)
  const container = useRef<HTMLDivElement>(null)
  const ig = useRef<{ render: () => void; toDataURL: (o?: unknown) => Promise<string>; destroy: () => void } | null>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!artifactId) return
    api.get<ArtifactT>(`/api/artifacts/${artifactId}`).then((a) => {
      setArtifact(a)
      const p = a.payload as unknown as IgPayload
      setStyle(p.style ?? 'list')
      setTemplate(p.antv?.template ?? TEMPLATES.list[0])
      setTitle(p.antv?.data?.title ?? '')
      setItems(p.antv?.data?.lists ?? [])
    })
  }, [artifactId])

  // (re)render the infographic when spec changes
  useEffect(() => {
    if (!artifact || !container.current || !template) return
    let cancelled = false
    ;(async () => {
      const { Infographic } = await import('@antv/infographic')
      if (cancelled || !container.current) return
      ig.current?.destroy()
      container.current.innerHTML = ''
      try {
        const inst = new Infographic({
          container: container.current,
          template,
          data: { title, lists: items },
          editable: true,
          width: 900,
          height: 620,
        } as never)
        inst.render()
        ig.current = inst as never
        setError(null)
        scheduleSave()
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifact, template, title, JSON.stringify(items)])

  const scheduleSave = () => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    setState('dirty')
    saveTimer.current = setTimeout(async () => {
      setState('saving')
      await api.put(`/api/artifacts/${artifactId}/payload`, {
        payload: { style, antv: { template, data: { title, lists: items } }, render: 'svg' },
      })
      try {
        const url = await ig.current!.toDataURL({ type: 'svg', embedResources: true })
        const svg = dataUrlToSvg(url)
        if (svg) await api.post(`/api/artifacts/${artifactId}/renders`, { svg })
      } catch { /* render best-effort */ }
      setState('saved')
    }, 800)
  }

  const setItem = (i: number, patch: Partial<Item>) =>
    setItems((list) => list.map((it, j) => (j === i ? { ...it, ...patch } : it)))
  const addItem = () => setItems((l) => [...l, { label: 'New item', desc: '' }])
  const delItem = (i: number) => setItems((l) => l.filter((_, j) => j !== i))

  const exportSvg = async () => {
    try {
      const url = await ig.current!.toDataURL({ type: 'svg', embedResources: true })
      const a = document.createElement('a')
      a.href = url
      a.download = `${title || 'infographic'}.svg`
      a.click()
    } catch (e) {
      alert(`Export failed: ${e instanceof Error ? e.message : e}`)
    }
  }

  if (!artifact)
    return <div className="flex h-full items-center justify-center bg-stage-bg text-stage-muted"><Loader2 className="animate-spin" /></div>

  return (
    <div className="flex h-full flex-col bg-stage-bg text-white">
      <div className="flex items-center gap-3 border-b border-stage-line px-5 py-2.5">
        <button onClick={() => navigate(`/n/${notebookId}`)} className="text-[12px] text-stage-muted hover:text-white">← Notebook</button>
        <span className="font-display text-[14px] font-semibold">{title}</span>
        <span className="text-[12px] text-stage-muted">
          {state === 'saving' ? <span className="flex items-center gap-1"><Loader2 size={12} className="animate-spin" /> Saving…</span>
            : state === 'dirty' ? 'Unsaved' : <span className="flex items-center gap-1"><Check size={12} /> Saved</span>}
        </span>
        <button onClick={exportSvg} className="ml-auto flex items-center gap-1.5 rounded-lg border border-stage-line px-2.5 py-1.5 text-[12px] text-stage-muted hover:text-white">
          <Download size={12} /> SVG
        </button>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* editor sidebar */}
        <div className="w-72 shrink-0 space-y-4 overflow-auto border-r border-stage-line p-4">
          <div>
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-stage-muted">Style</h3>
            <div className="mt-2 grid grid-cols-2 gap-1.5">
              {Object.keys(TEMPLATES).map((s) => (
                <button key={s} onClick={() => { setStyle(s); setTemplate(TEMPLATES[s][0]) }}
                  className={`rounded-lg border px-2 py-1.5 text-[12px] capitalize ${s === style ? 'border-ws-accent' : 'border-stage-line text-stage-muted'}`}>
                  {s}
                </button>
              ))}
            </div>
          </div>
          <div>
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-stage-muted">Template</h3>
            <select value={template} onChange={(e) => setTemplate(e.target.value)}
              className="mt-2 w-full rounded-lg border border-stage-line bg-stage-bg px-2 py-1.5 text-[12px]">
              {(TEMPLATES[style] ?? []).map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-stage-muted">Title</h3>
            <input value={title} onChange={(e) => setTitle(e.target.value)}
              className="mt-2 w-full rounded-lg border border-stage-line bg-stage-bg px-2 py-1.5 text-[13px]" />
          </div>
          <div>
            <div className="flex items-center justify-between">
              <h3 className="text-[11px] font-semibold uppercase tracking-wide text-stage-muted">Items</h3>
              <button onClick={addItem} className="text-stage-muted hover:text-white"><Plus size={14} /></button>
            </div>
            <div className="mt-2 space-y-2">
              {items.map((it, i) => (
                <div key={i} className="rounded-lg border border-stage-line p-2">
                  <div className="flex items-center gap-1">
                    <input value={it.label} onChange={(e) => setItem(i, { label: e.target.value })}
                      placeholder="Label"
                      className="flex-1 bg-transparent text-[13px] font-medium outline-none" />
                    <button onClick={() => delItem(i)} className="text-stage-muted hover:text-red-400"><Trash2 size={12} /></button>
                  </div>
                  <input value={it.desc} onChange={(e) => setItem(i, { desc: e.target.value })}
                    placeholder="Description"
                    className="mt-1 w-full bg-transparent text-[12px] text-stage-muted outline-none" />
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* canvas */}
        <div className="flex min-w-0 flex-1 items-center justify-center overflow-auto bg-white p-8">
          {error && <div className="absolute rounded bg-red-500/90 px-3 py-1.5 text-[12px] text-white">{error.slice(0, 120)}</div>}
          <div ref={container} style={{ width: 900, height: 620 }} className="[&_svg]:h-full [&_svg]:w-full" />
        </div>
      </div>
    </div>
  )
}
