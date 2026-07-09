import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Check, Download, Loader2, Plus } from 'lucide-react'
import { api } from '../api/client'
import { toast } from '../ui/toast'
import type { ArtifactT } from '../deck/types'

type Cell = string | number | boolean | null | { formula: string }
interface Column { header: string; width?: number | null; format?: string | null }
interface SheetT { name: string; columns: Column[]; rows: Cell[][] }

const EXPORTS = ['xlsx', 'pdf', 'html'] as const

function cellToText(c: Cell): string {
  if (c == null) return ''
  if (typeof c === 'object' && 'formula' in c) return c.formula
  return String(c)
}
function textToCell(t: string): Cell {
  if (t === '') return null
  if (t.startsWith('=')) return { formula: t }
  const n = Number(t)
  return t.trim() !== '' && !Number.isNaN(n) ? n : t
}

export function SheetEditor() {
  const { artifactId, notebookId } = useParams()
  const navigate = useNavigate()
  const [artifact, setArtifact] = useState<ArtifactT | null>(null)
  const [sheets, setSheets] = useState<SheetT[]>([])
  const [active, setActive] = useState(0)
  const [state, setState] = useState<'saved' | 'dirty' | 'saving'>('saved')
  const [exporting, setExporting] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!artifactId) return
    api.get<ArtifactT>(`/api/artifacts/${artifactId}`).then((a) => {
      setArtifact(a)
      setSheets(((a.payload.ir as { sheets?: SheetT[] }).sheets ?? []))
    })
  }, [artifactId])

  const persist = (next: SheetT[]) => {
    setSheets(next)
    setState('dirty')
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      if (!artifact) return
      setState('saving')
      await api.put(`/api/artifacts/${artifactId}/ir`, {
        payload: { ir: { ...artifact.payload.ir, sheets: next }, theme_name: artifact.payload.theme_name },
      })
      setState('saved')
    }, 700)
  }

  const editCell = (r: number, c: number, text: string) => {
    const next = structuredClone(sheets)
    next[active].rows[r][c] = textToCell(text)
    persist(next)
  }
  const editHeader = (c: number, text: string) => {
    const next = structuredClone(sheets)
    next[active].columns[c].header = text
    persist(next)
  }
  const addRow = () => {
    const next = structuredClone(sheets)
    next[active].rows.push(next[active].columns.map(() => null))
    persist(next)
  }
  const addCol = () => {
    const next = structuredClone(sheets)
    next[active].columns.push({ header: 'Column' })
    next[active].rows.forEach((row) => row.push(null))
    persist(next)
  }

  const exportAs = async (fmt: string) => {
    setExporting(fmt)
    try {
      const res = await api.post<{ url: string; filename: string }>(`/api/artifacts/${artifactId}/export`, { format: fmt })
      const a = document.createElement('a')
      a.href = res.url; a.download = res.filename; a.click()
    } catch (e) { toast.error(`Export failed: ${e instanceof Error ? e.message : e}`) }
    finally { setExporting(null) }
  }

  if (!artifact) return <div className="flex h-full items-center justify-center text-ws-muted"><Loader2 className="animate-spin" /></div>
  const sheet = sheets[active]

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-ws-line px-5 py-2.5">
        <button onClick={() => navigate(`/n/${notebookId}`)} className="text-[12px] text-ws-muted hover:text-ws-ink">← Notebook</button>
        <span className="font-display text-[14px] font-semibold">{artifact.title}</span>
        <span className="text-[12px] text-ws-muted">
          {state === 'saving' ? <span className="flex items-center gap-1"><Loader2 size={12} className="animate-spin" /> Saving…</span>
            : state === 'dirty' ? 'Unsaved' : <span className="flex items-center gap-1"><Check size={12} /> Saved</span>}
        </span>
        <div className="ml-auto flex gap-1.5">
          {EXPORTS.map((fmt) => (
            <button key={fmt} onClick={() => exportAs(fmt)} disabled={exporting !== null}
              className="flex items-center gap-1.5 rounded-lg border border-ws-line px-2.5 py-1.5 text-[12px] text-ws-muted hover:text-ws-ink disabled:opacity-40">
              {exporting === fmt ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}{fmt.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {sheets.length > 1 && (
        <div className="flex gap-1 border-b border-ws-line bg-ws-panel px-4 py-1.5">
          {sheets.map((s, i) => (
            <button key={i} onClick={() => setActive(i)}
              className={`rounded-md px-3 py-1 text-[12px] ${i === active ? 'bg-ws-bg font-medium' : 'text-ws-muted'}`}>{s.name}</button>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-auto p-6">
        {sheet ? (
          <table className="sheet-grid">
            <thead>
              <tr>
                <th className="sheet-corner" />
                {sheet.columns.map((col, c) => (
                  <th key={c}><input value={col.header} onChange={(e) => editHeader(c, e.target.value)} /></th>
                ))}
                <th className="sheet-add"><button onClick={addCol} title="Add column"><Plus size={13} /></button></th>
              </tr>
            </thead>
            <tbody>
              {sheet.rows.map((row, r) => (
                <tr key={r}>
                  <td className="sheet-rownum">{r + 1}</td>
                  {sheet.columns.map((_, c) => (
                    <td key={c}>
                      <input value={cellToText(row[c] ?? null)} onChange={(e) => editCell(r, c, e.target.value)} />
                    </td>
                  ))}
                  <td />
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-ws-muted">This artifact has no sheets.</p>
        )}
        <button onClick={addRow} className="mt-3 flex items-center gap-1.5 rounded-lg border border-ws-line px-3 py-1.5 text-[12px] text-ws-muted hover:text-ws-ink">
          <Plus size={13} /> Add row
        </button>
      </div>
    </div>
  )
}
