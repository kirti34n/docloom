/** Inline editors for the non-text blocks (table, chart, stats, image) that
 * used to render read-only. Each keeps the block's shape intact and calls
 * onChange with a new block so the deck/doc store persists it. */
import { Plus, Trash2 } from 'lucide-react'
import type { Block, SeriesT, Stat } from './types'
import { plain } from './RichText'

function asText(v: unknown): string {
  return typeof v === 'string' ? v : plain((v ?? '') as never)
}

function Btn({ onClick, children, title }: {
  onClick: () => void; children: React.ReactNode; title?: string
}) {
  return (
    <button onClick={onClick} title={title}
      className="inline-flex items-center gap-1 rounded border border-white/15 px-1.5 py-0.5 text-[11px] text-white/70 hover:bg-white/10">
      {children}
    </button>
  )
}

function TableEditor({ block, onChange }: { block: Block; onChange: (b: Block) => void }) {
  const header = (block.header ?? []).map(asText)
  const rows = (block.rows ?? []).map((r) => r.map(asText))
  const cols = Math.max(header.length, ...rows.map((r) => r.length), 1)

  const setHeader = (i: number, v: string) => {
    const h = [...header]; h[i] = v; onChange({ ...block, header: h })
  }
  const setCell = (r: number, c: number, v: string) => {
    const next = rows.map((row) => [...row])
    next[r][c] = v
    onChange({ ...block, rows: next })
  }
  const addRow = () => onChange({ ...block, rows: [...rows, Array(cols).fill('')] })
  const addCol = () => onChange({
    ...block,
    header: [...header, `Column ${cols + 1}`],
    rows: rows.map((r) => [...r, '']),
  })
  const delRow = (r: number) => onChange({ ...block, rows: rows.filter((_, i) => i !== r) })
  const delCol = (c: number) => onChange({
    ...block,
    header: header.filter((_, i) => i !== c),
    rows: rows.map((row) => row.filter((_, i) => i !== c)),
  })

  return (
    <div className="rbe">
      <table className="rbe-table">
        <thead>
          <tr>
            {Array.from({ length: cols }, (_, c) => (
              <th key={c}>
                <input value={header[c] ?? ''} onChange={(e) => setHeader(c, e.target.value)}
                  placeholder={`Column ${c + 1}`} />
                <button className="rbe-x" title="Delete column" aria-label="Delete column" onClick={() => delCol(c)}>×</button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {Array.from({ length: cols }, (_, c) => (
                <td key={c}>
                  <input value={row[c] ?? ''} onChange={(e) => setCell(r, c, e.target.value)} />
                </td>
              ))}
              <td className="rbe-rowdel">
                <button title="Delete row" aria-label="Delete row" onClick={() => delRow(r)}><Trash2 size={12} /></button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="rbe-actions">
        <Btn onClick={addRow}><Plus size={11} /> Row</Btn>
        <Btn onClick={addCol}><Plus size={11} /> Column</Btn>
      </div>
      <input className="rbe-caption" value={block.caption ?? ''}
        placeholder="Caption (optional)"
        onChange={(e) => onChange({ ...block, caption: e.target.value || null })} />
    </div>
  )
}

function StatsEditor({ block, onChange }: { block: Block; onChange: (b: Block) => void }) {
  const items = (block.items ?? []) as Stat[]
  const set = (i: number, patch: Partial<Stat>) => {
    const next = items.map((it, ix) => (ix === i ? { ...it, ...patch } : it))
    onChange({ ...block, items: next })
  }
  const add = () => onChange({ ...block, items: [...items, { label: 'Metric', value: '0' }] })
  const del = (i: number) => onChange({ ...block, items: items.filter((_, ix) => ix !== i) })

  return (
    <div className="rbe">
      <div className="rbe-stats">
        {items.map((s, i) => (
          <div key={i} className="rbe-stat">
            <input className="rbe-stat-val" value={s.value ?? ''} placeholder="Value"
              onChange={(e) => set(i, { value: e.target.value })} />
            <input className="rbe-stat-lbl" value={s.label ?? ''} placeholder="Label"
              onChange={(e) => set(i, { label: e.target.value })} />
            <input className="rbe-stat-delta" value={s.delta ?? ''} placeholder="Δ (optional)"
              onChange={(e) => set(i, { delta: e.target.value || null })} />
            <button className="rbe-x" title="Delete stat" aria-label="Delete stat" onClick={() => del(i)}>×</button>
          </div>
        ))}
      </div>
      <div className="rbe-actions"><Btn onClick={add}><Plus size={11} /> Stat</Btn></div>
    </div>
  )
}

function ChartEditor({ block, onChange }: { block: Block; onChange: (b: Block) => void }) {
  const labels = block.labels ?? []
  const series = (block.series ?? []) as SeriesT[]

  const setLabel = (i: number, v: string) => {
    const next = [...labels]; next[i] = v; onChange({ ...block, labels: next })
  }
  const setVal = (si: number, li: number, v: string) => {
    const num = v === '' ? null : Number(v)
    const next = series.map((s, ix) =>
      ix === si ? { ...s, values: s.values.map((x, j) => (j === li ? num : x)) } : s)
    onChange({ ...block, series: next })
  }
  const setSeriesName = (si: number, v: string) => {
    onChange({ ...block, series: series.map((s, ix) => (ix === si ? { ...s, name: v } : s)) })
  }
  const addPoint = () => onChange({
    ...block,
    labels: [...labels, `P${labels.length + 1}`],
    series: series.map((s) => ({ ...s, values: [...s.values, 0] })),
  })
  const addSeries = () => onChange({
    ...block,
    series: [...series, { name: `Series ${series.length + 1}`, values: labels.map(() => 0) }],
  })
  const delPoint = (li: number) => onChange({
    ...block,
    labels: labels.filter((_, i) => i !== li),
    series: series.map((s) => ({ ...s, values: s.values.filter((_, i) => i !== li) })),
  })

  return (
    <div className="rbe">
      <input className="rbe-caption" value={block.title ?? ''} placeholder="Chart title"
        onChange={(e) => onChange({ ...block, title: e.target.value || null })} />
      <table className="rbe-table">
        <thead>
          <tr>
            <th className="rbe-corner">Series ↓ / Point →</th>
            {labels.map((l, i) => (
              <th key={i}>
                <input value={l} onChange={(e) => setLabel(i, e.target.value)} />
                <button className="rbe-x" title="Delete point" aria-label="Delete point" onClick={() => delPoint(i)}>×</button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {series.map((s, si) => (
            <tr key={si}>
              <td>
                <input value={s.name ?? ''} placeholder={`Series ${si + 1}`}
                  onChange={(e) => setSeriesName(si, e.target.value)} />
              </td>
              {labels.map((_, li) => (
                <td key={li}>
                  <input type="number" value={s.values[li] ?? ''}
                    onChange={(e) => setVal(si, li, e.target.value)} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="rbe-actions">
        <Btn onClick={addPoint}><Plus size={11} /> Point</Btn>
        <Btn onClick={addSeries}><Plus size={11} /> Series</Btn>
      </div>
    </div>
  )
}

function ImageEditor({ block, onChange }: { block: Block; onChange: (b: Block) => void }) {
  const src = block.asset_id
    ? `/api/assets/${block.asset_id}/file`
    : block.path && !block.path.startsWith('asset://')
      ? block.path
      : null
  return (
    <div className="rbe rbe-image">
      {src ? (
        <img src={src} alt={block.alt ?? ''} className="rbe-img-preview" />
      ) : (
        <div className="rbe-img-empty">No image bound — set one from the Inspector’s Image panel.</div>
      )}
      <input className="rbe-caption" value={block.alt ?? ''} placeholder="Alt text (accessibility)"
        onChange={(e) => onChange({ ...block, alt: e.target.value })} />
      <input className="rbe-caption" value={block.caption ?? ''} placeholder="Caption (optional)"
        onChange={(e) => onChange({ ...block, caption: e.target.value || null })} />
    </div>
  )
}

const EDITORS: Record<string, (p: { block: Block; onChange: (b: Block) => void }) => React.ReactElement> = {
  table: TableEditor,
  stats: StatsEditor,
  chart: ChartEditor,
  image: ImageEditor,
}

export function hasRichEditor(type: string): boolean {
  return type in EDITORS
}

export function RichBlockEditor({ block, onChange }: {
  block: Block; onChange: (b: Block) => void
}) {
  const Editor = EDITORS[block.type]
  return Editor ? <Editor block={block} onChange={onChange} /> : null
}
