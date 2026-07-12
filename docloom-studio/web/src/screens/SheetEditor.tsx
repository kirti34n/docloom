import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Check, Download, Loader2, Plus, Trash2 } from 'lucide-react'
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

// Best-effort Excel number-format display (e.g. "#,##0.00", "0.0%"): applied
// to a cell's resting (unfocused) text only, never to what's edited, so a
// formatted "$1,234.50" never has to round-trip back through textToCell.
function formatCell(c: Cell, format?: string | null): string {
  if (typeof c !== 'number' || !format) return cellToText(c)
  const pct = format.includes('%')
  const value = pct ? c * 100 : c
  const decimals = format.match(/\.(0+)/)?.[1].length ?? 0
  const grouped = format.includes(',')
  const body = grouped
    ? value.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
    : value.toFixed(decimals)
  const prefix = format.match(/^[^0#.,%]+/)?.[0] ?? ''
  return `${prefix}${body}${pct ? '%' : ''}`
}

// Excel column width is "characters of the default font"; approximate it in
// pixels so a wide/narrow IR column actually looks wide/narrow in the grid.
function colPx(width?: number | null): number | undefined {
  return width == null ? undefined : Math.max(48, Math.round(width * 7 + 10))
}

export function SheetEditor() {
  const { artifactId, notebookId } = useParams()
  const navigate = useNavigate()
  const [artifact, setArtifact] = useState<ArtifactT | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [sheets, setSheets] = useState<SheetT[]>([])
  const [active, setActive] = useState(0)
  const [state, setState] = useState<'saved' | 'dirty' | 'saving'>('saved')
  const [exporting, setExporting] = useState<string | null>(null)
  // `${row}:${col}` of the cell currently being edited, so its value shows
  // raw and editable while every other cell shows its formatted text
  const [focused, setFocused] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!artifactId) return
    api.get<ArtifactT>(`/api/artifacts/${artifactId}`).then((a) => {
      setArtifact(a)
      setSheets(((a.payload.ir as { sheets?: SheetT[] }).sheets ?? []))
    }).catch((e) => setLoadError(e instanceof Error ? e.message : String(e)))
  }, [artifactId])

  const persist = (next: SheetT[]) => {
    setSheets(next)
    setState('dirty')
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      if (!artifact) return
      setState('saving')
      try {
        await api.put(`/api/artifacts/${artifactId}/ir`, {
          payload: { ir: { ...artifact.payload.ir, sheets: next }, theme_name: artifact.payload.theme_name },
        })
        setState('saved')
      } catch (e) {
        setState('dirty')
        toast.error(`Save failed: ${e instanceof Error ? e.message : e}`)
      }
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
  const delRow = (r: number) => {
    const next = structuredClone(sheets)
    next[active].rows.splice(r, 1)
    persist(next)
  }
  const delCol = (c: number) => {
    const next = structuredClone(sheets)
    next[active].columns.splice(c, 1)
    next[active].rows.forEach((row) => row.splice(c, 1))
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

  if (loadError) return <div className="flex h-full items-center justify-center text-madder text-[13px]">{loadError}</div>
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
              className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-ws-line px-2.5 py-1.5 text-[12px] text-ws-muted hover:text-ws-ink disabled:opacity-40">
              {exporting === fmt ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}{fmt.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {sheets.length > 1 && (
        <div className="flex gap-1 border-b border-ws-line bg-ws-panel px-4 py-1.5">
          {sheets.map((s, i) => (
            <button key={i} onClick={() => setActive(i)}
              className={`rounded-[var(--radius-sm)] px-3 py-1 text-[12px] ${i === active ? 'bg-ws-bg font-medium' : 'text-ws-muted'}`}>{s.name}</button>
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
                  <th key={c}>
                    <div className="flex items-center">
                      <input value={col.header} onChange={(e) => editHeader(c, e.target.value)}
                        style={{ width: colPx(col.width) }} />
                      <button onClick={() => delCol(c)} aria-label={`Delete column ${col.header}`}
                        title="Delete column" className="shrink-0 px-1 text-ws-muted hover:text-madder">
                        <Trash2 size={11} />
                      </button>
                    </div>
                  </th>
                ))}
                <th className="sheet-add"><button onClick={addCol} title="Add column"><Plus size={13} /></button></th>
              </tr>
            </thead>
            <tbody>
              {sheet.rows.map((row, r) => (
                <tr key={r}>
                  <td className="sheet-rownum">{r + 1}</td>
                  {sheet.columns.map((col, c) => (
                    <td key={c}>
                      <input
                        value={focused === `${r}:${c}` ? cellToText(row[c] ?? null) : formatCell(row[c] ?? null, col.format)}
                        onFocus={() => setFocused(`${r}:${c}`)}
                        onBlur={() => setFocused(null)}
                        onChange={(e) => editCell(r, c, e.target.value)}
                        style={{ width: colPx(col.width) }}
                      />
                    </td>
                  ))}
                  <td className="text-center">
                    <button onClick={() => delRow(r)} aria-label={`Delete row ${r + 1}`} title="Delete row"
                      className="px-1.5 py-1 text-ws-muted hover:text-madder">
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-ws-muted">This artifact has no sheets.</p>
        )}
        <button onClick={addRow} className="mt-3 flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-ws-line px-3 py-1.5 text-[12px] text-ws-muted hover:text-ws-ink">
          <Plus size={13} /> Add row
        </button>
      </div>
    </div>
  )
}
