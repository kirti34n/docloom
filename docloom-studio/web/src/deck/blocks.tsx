import type { Block, ListItem, SeriesT, Stat } from './types'
import { RichText, plain } from './RichText'

interface BlockProps {
  block: Block
  citeNumbers?: Map<string, number>
}

function List({ block, ordered, citeNumbers }: BlockProps & { ordered: boolean }) {
  const items = (block.items ?? []) as ListItem[]
  let counter = 0
  return (
    <div className="blk-list">
      {items.map((item, i) => {
        const level = Math.min(item.level ?? 0, 4)
        if (level === 0 && ordered) counter += 1
        return (
          <div key={i} className="blk-li" style={{ paddingLeft: `${level * 26}px` }}>
            <span className="blk-marker">{ordered && level === 0 ? `${counter}.` : '•'}</span>
            <span>
              <RichText value={item.text} citeNumbers={citeNumbers} />
            </span>
          </div>
        )
      })}
    </div>
  )
}

const SERIES_COLORS = ['var(--primary)', 'var(--accent)', 'var(--accent-2)']

function MiniChart({ block }: { block: Block }) {
  // grouped-bar preview until the gpt-vis block lands (M7); non-bar charts
  // summarize as a data table
  const labels = block.labels ?? []
  const series = (block.series ?? []) as SeriesT[]
  const allValues = series.flatMap((s) => s.values).filter((v): v is number => v != null)
  const max = Math.max(...allValues, 1)
  const barLike = ['column', 'bar'].includes(block.chart ?? '') && allValues.length > 0

  return (
    <figure className="blk-chart">
      {block.title && <figcaption className="blk-chart-title">{block.title}</figcaption>}
      {barLike ? (
        <>
          <div className="chart-plot">
            {labels.map((label, i) => (
              <div key={i} className="chart-group">
                <div className="chart-group-bars">
                  {series.map((s, si) => {
                    const v = s.values[i] ?? 0
                    return (
                      <div
                        key={si}
                        className="chart-bar"
                        style={{
                          height: `${Math.max(3, (100 * (v ?? 0)) / max)}%`,
                          background: SERIES_COLORS[si % SERIES_COLORS.length],
                        }}
                        title={`${s.name}: ${v}`}
                      />
                    )
                  })}
                </div>
                <span className="chart-label">{label}</span>
              </div>
            ))}
          </div>
          {series.length > 1 && (
            <div className="chart-legend">
              {series.map((s, si) => (
                <span key={si} className="chart-legend-item">
                  <i style={{ background: SERIES_COLORS[si % SERIES_COLORS.length] }} />
                  {s.name}
                </span>
              ))}
            </div>
          )}
        </>
      ) : (
        <table className="blk-table chart-table">
          <thead>
            <tr>
              <th />
              {labels.map((l, i) => (
                <th key={i}>{l}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {series.map((s, i) => (
              <tr key={i}>
                <td className="chart-series">{s.name}</td>
                {labels.map((_, j) => (
                  <td key={j}>{s.values[j] ?? ''}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {block.caption && <div className="blk-caption">{block.caption}</div>}
    </figure>
  )
}

export function BlockView({ block, citeNumbers }: BlockProps) {
  switch (block.type) {
    case 'heading':
      return (
        <div className={`blk-heading lvl-${Math.min(block.level ?? 2, 4)}`}>
          <RichText value={block.text} citeNumbers={citeNumbers} />
        </div>
      )
    case 'paragraph':
      return (
        <p className="blk-para">
          <RichText value={block.text} citeNumbers={citeNumbers} />
        </p>
      )
    case 'bullets':
      return <List block={block} ordered={false} citeNumbers={citeNumbers} />
    case 'numbered':
      return <List block={block} ordered citeNumbers={citeNumbers} />
    case 'quote':
      return (
        <blockquote className="blk-quote">
          <RichText value={block.text} citeNumbers={citeNumbers} />
          {block.attribution && <cite>— {block.attribution}</cite>}
        </blockquote>
      )
    case 'code':
      return (
        <pre className="blk-code">
          <code>{block.code}</code>
        </pre>
      )
    case 'table':
      return (
        <figure className="blk-figure">
          <table className="blk-table">
            <thead>
              <tr>
                {(block.header ?? []).map((h, i) => (
                  <th key={i}>{plain(h)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(block.rows ?? []).map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j}>
                      <RichText value={cell} citeNumbers={citeNumbers} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {block.caption && <div className="blk-caption">{block.caption}</div>}
        </figure>
      )
    case 'image':
    case 'artifact':
      if (!block.path)
        return (
          <div className="blk-image-slot">
            <span>{block.query ? `image: ${block.query}` : 'empty image slot'}</span>
          </div>
        )
      return (
        <figure className="blk-figure">
          <img src={toUrl(block.path)} alt={block.alt ?? ''} className="blk-image" />
          {block.caption && <div className="blk-caption">{block.caption}</div>}
        </figure>
      )
    case 'callout':
      return (
        <div className={`blk-callout style-${block.style ?? 'info'}`}>
          <RichText value={block.text} citeNumbers={citeNumbers} />
        </div>
      )
    case 'divider':
      return <hr className="blk-divider" />
    case 'chart':
      return <MiniChart block={block} />
    case 'stats':
      return (
        <div className="blk-stats">
          {((block.items ?? []) as Stat[]).slice(0, 5).map((s, i) => (
            <div key={i} className="stat-card">
              <span className="stat-value">{s.value}</span>
              <span className="stat-label">{s.label}</span>
              {s.delta && <span className="stat-delta">{s.delta}</span>}
            </div>
          ))}
        </div>
      )
    default:
      return null
  }
}

function toUrl(path: string): string {
  if (path.startsWith('asset://'))
    return `/api/assets/${path.slice('asset://'.length)}/file`
  if (/^https?:/i.test(path)) return path
  return `/api/files?path=${encodeURIComponent(path)}`
}
