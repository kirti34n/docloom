/** Inline editors for the non-text blocks (table, chart, stats, image) that
 * used to render read-only. Each keeps the block's shape intact and calls
 * onChange with a new block so the deck/doc store persists it. */
import { Plus, Trash2 } from 'lucide-react'
import type { Block, RichText as RichTextT, SeriesT, Stat } from './types'
import { plain } from './RichText'

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

// pad a ragged row/header up to n cells so a write past its current end
// never leaves a hole (holes serialize to JSON null, which docloom rejects)
export function padded(row: RichTextT[], n: number): RichTextT[] {
  const out = [...row]
  while (out.length < n) out.push('')
  return out
}

function tableCols(block: Block): number {
  const header = block.header ?? []
  const rows = block.rows ?? []
  return Math.max(header.length, ...rows.map((r) => r.length), 1)
}

export function tableSetHeader(block: Block, i: number, v: string): Block {
  const cols = tableCols(block)
  const h = padded(block.header ?? [], cols)
  h[i] = v
  return { ...block, header: h }
}

export function tableSetCell(block: Block, r: number, c: number, v: string): Block {
  const cols = tableCols(block)
  const next = (block.rows ?? []).map((row) => padded(row, cols))
  next[r][c] = v
  return { ...block, rows: next }
}

export function tableAddRow(block: Block): Block {
  const cols = tableCols(block)
  return { ...block, rows: [...(block.rows ?? []), Array(cols).fill('')] }
}

export function tableAddColumn(block: Block): Block {
  const cols = tableCols(block)
  return {
    ...block,
    header: [...(block.header ?? []), `Column ${cols + 1}`],
    rows: (block.rows ?? []).map((r) => [...r, '']),
  }
}

export function tableDeleteRow(block: Block, r: number): Block {
  return { ...block, rows: (block.rows ?? []).filter((_, i) => i !== r) }
}

export function tableDeleteColumn(block: Block, c: number): Block {
  return {
    ...block,
    header: (block.header ?? []).filter((_, i) => i !== c),
    rows: (block.rows ?? []).map((row) => row.filter((_, i) => i !== c)),
  }
}

function TableEditor({ block, onChange }: { block: Block; onChange: (b: Block) => void }) {
  const header = block.header ?? []
  const rows = block.rows ?? []
  const cols = tableCols(block)

  return (
    <div className="rbe">
      <table className="rbe-table">
        <thead>
          <tr>
            {Array.from({ length: cols }, (_, c) => (
              <th key={c}>
                <input value={plain(header[c])} onChange={(e) => onChange(tableSetHeader(block, c, e.target.value))}
                  placeholder={`Column ${c + 1}`} />
                <button className="rbe-x" title="Delete column" aria-label="Delete column" onClick={() => onChange(tableDeleteColumn(block, c))}>×</button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {Array.from({ length: cols }, (_, c) => (
                <td key={c}>
                  <input value={plain(row[c])} onChange={(e) => onChange(tableSetCell(block, r, c, e.target.value))} />
                </td>
              ))}
              <td className="rbe-rowdel">
                <button title="Delete row" aria-label="Delete row" onClick={() => onChange(tableDeleteRow(block, r))}><Trash2 size={12} /></button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="rbe-actions">
        <Btn onClick={() => onChange(tableAddRow(block))}><Plus size={11} /> Row</Btn>
        <Btn onClick={() => onChange(tableAddColumn(block))}><Plus size={11} /> Column</Btn>
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

function CodeEditor({ block, onChange }: { block: Block; onChange: (b: Block) => void }) {
  return (
    <div className="rbe">
      <textarea
        className="rbe-caption"
        style={{ width: '100%', minHeight: 120, fontFamily: 'var(--font-mono, monospace)', resize: 'vertical' }}
        value={block.code ?? ''}
        spellCheck={false}
        placeholder="Code"
        onChange={(e) => onChange({ ...block, code: e.target.value })}
      />
      <input className="rbe-caption" value={block.language ?? ''} placeholder="Language (optional)"
        onChange={(e) => onChange({ ...block, language: e.target.value || null })} />
    </div>
  )
}

const EDITORS: Record<string, (p: { block: Block; onChange: (b: Block) => void }) => React.ReactElement> = {
  table: TableEditor,
  stats: StatsEditor,
  chart: ChartEditor,
  image: ImageEditor,
  code: CodeEditor,
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
