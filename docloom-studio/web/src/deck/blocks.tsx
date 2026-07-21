import { useEffect, useState } from 'react'
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

const PLOT_W = 480
const PLOT_H = 220

function BarPreview({ labels, series, max }: { labels: string[]; series: SeriesT[]; max: number }) {
  return (
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
      <ChartLegend series={series} />
    </>
  )
}

/** x positions spread evenly across the plot width, one per label. */
function evenXs(n: number, w: number): number[] {
  return Array.from({ length: n }, (_, i) => (n > 1 ? (i / (n - 1)) * w : w / 2))
}

/** "M x y L x y ..." per unbroken run of non-null values; a null value ends
 *  a run rather than being bridged, matching chart_svg.py: a gap is a gap. */
function linePath(xs: number[], values: (number | null)[], max: number, h: number): string {
  const segs: string[] = []
  let drawing = false
  values.forEach((v, i) => {
    if (v == null) {
      drawing = false
      return
    }
    const y = h - (Math.max(0, v) / max) * h
    segs.push(`${drawing ? 'L' : 'M'}${xs[i].toFixed(1)} ${y.toFixed(1)}`)
    drawing = true
  })
  return segs.join(' ')
}

/** Same run-breaking as linePath, but each run closes down to the baseline
 *  so it can be filled. */
function areaPath(xs: number[], values: (number | null)[], max: number, h: number): string {
  const runs: { x: number; y: number }[][] = []
  let run: { x: number; y: number }[] = []
  values.forEach((v, i) => {
    if (v == null) {
      if (run.length) runs.push(run)
      run = []
      return
    }
    run.push({ x: xs[i], y: h - (Math.max(0, v) / max) * h })
  })
  if (run.length) runs.push(run)
  return runs
    .map((r) => {
      const top = r.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
      return `${top} L${r[r.length - 1].x.toFixed(1)} ${h} L${r[0].x.toFixed(1)} ${h} Z`
    })
    .join(' ')
}

function LineAreaPreview({
  labels,
  series,
  max,
  filled,
}: {
  labels: string[]
  series: SeriesT[]
  max: number
  filled: boolean
}) {
  const n = Math.max(labels.length, ...series.map((s) => s.values.length), 1)
  const xs = evenXs(n, PLOT_W)
  return (
    <>
      <svg viewBox={`0 0 ${PLOT_W} ${PLOT_H}`} className="chart-svg" preserveAspectRatio="none" role="img">
        {series.map((s, si) => {
          const color = SERIES_COLORS[si % SERIES_COLORS.length]
          return (
            <g key={si}>
              {filled && <path d={areaPath(xs, s.values, max, PLOT_H)} fill={color} opacity={0.16} stroke="none" />}
              <path d={linePath(xs, s.values, max, PLOT_H)} fill="none" stroke={color} strokeWidth={2.5} />
            </g>
          )
        })}
      </svg>
      <div className="chart-plot-labels">
        {labels.map((l, i) => (
          <span key={i} className="chart-label">
            {l}
          </span>
        ))}
      </div>
      <ChartLegend series={series} />
    </>
  )
}

function ScatterPreview({ labels, series, max }: { labels: string[]; series: SeriesT[]; max: number }) {
  // the IR has no separate x-series (docloom's scatter uses labels parsed as
  // numeric x, see pptx.py _chart_data); fall back to even spacing when
  // labels aren't numbers so an editorial scatter still previews sensibly
  const numericXs = labels.map((l) => Number(l))
  const useNumeric = labels.length > 1 && numericXs.every((v) => Number.isFinite(v))
  const xMin = useNumeric ? Math.min(...numericXs) : 0
  const xMax = useNumeric ? Math.max(...numericXs) : Math.max(labels.length - 1, 1)
  const xSpan = xMax - xMin || 1
  const evenly = evenXs(Math.max(labels.length, 1), PLOT_W)
  return (
    <>
      <svg viewBox={`0 0 ${PLOT_W} ${PLOT_H}`} className="chart-svg" preserveAspectRatio="none" role="img">
        {series.map((s, si) => {
          const color = SERIES_COLORS[si % SERIES_COLORS.length]
          return (
            <g key={si}>
              {s.values.map((v, i) => {
                if (v == null) return null
                const x = useNumeric ? ((numericXs[i] - xMin) / xSpan) * PLOT_W : evenly[i]
                const y = PLOT_H - (Math.max(0, v) / max) * PLOT_H
                return (
                  <circle key={i} cx={x} cy={y} r={4.5} fill={color}>
                    <title>{`${s.name ? s.name + ': ' : ''}${v}`}</title>
                  </circle>
                )
              })}
            </g>
          )
        })}
      </svg>
      <div className="chart-plot-labels">
        {labels.map((l, i) => (
          <span key={i} className="chart-label">
            {l}
          </span>
        ))}
      </div>
      <ChartLegend series={series} />
    </>
  )
}

function PiePreview({ labels, series }: { labels: string[]; series: SeriesT[] }) {
  const s = series[0]
  const values = labels.map((_, i) => Math.max(0, s?.values[i] ?? 0))
  const total = values.reduce((a, b) => a + b, 0)
  const r = 68
  const circumference = 2 * Math.PI * r
  let drawn = 0
  return (
    <>
      <svg viewBox="0 0 176 176" className="chart-pie" role="img">
        {total <= 0 ? (
          <circle cx={88} cy={88} r={r} fill="none" stroke="var(--surface)" strokeWidth={26} />
        ) : (
          values.map((v, i) => {
            if (v <= 0) return null
            const len = (v / total) * circumference
            const el = (
              <circle
                key={i}
                cx={88}
                cy={88}
                r={r}
                fill="none"
                stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                strokeWidth={26}
                strokeDasharray={`${len} ${circumference - len}`}
                strokeDashoffset={-drawn}
                transform="rotate(-90 88 88)"
              >
                <title>{`${labels[i] ?? ''}: ${v}`}</title>
              </circle>
            )
            drawn += len
            return el
          })
        )}
      </svg>
      {total > 0 && (
        <div className="chart-legend">
          {labels.map((l, i) => (
            <span key={i} className="chart-legend-item">
              <i style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} />
              {l}
            </span>
          ))}
        </div>
      )}
    </>
  )
}

function ChartLegend({ series }: { series: SeriesT[] }) {
  if (series.length <= 1) return null
  return (
    <div className="chart-legend">
      {series.map((s, si) => (
        <span key={si} className="chart-legend-item">
          <i style={{ background: SERIES_COLORS[si % SERIES_COLORS.length] }} />
          {s.name}
        </span>
      ))}
    </div>
  )
}

function ChartTable({ labels, series }: { labels: string[]; series: SeriesT[] }) {
  return (
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
  )
}

function MiniChart({ block }: { block: Block }) {
  const labels = block.labels ?? []
  const series = (block.series ?? []) as SeriesT[]
  const kind = block.chart ?? 'column'
  const allValues = series.flatMap((s) => s.values).filter((v): v is number => v != null)
  const max = Math.max(...allValues, 1)
  const hasData = allValues.length > 0

  let body: React.ReactNode
  if (hasData && (kind === 'column' || kind === 'bar')) {
    body = <BarPreview labels={labels} series={series} max={max} />
  } else if (hasData && (kind === 'line' || kind === 'area')) {
    body = <LineAreaPreview labels={labels} series={series} max={max} filled={kind === 'area'} />
  } else if (hasData && kind === 'scatter') {
    body = <ScatterPreview labels={labels} series={series} max={max} />
  } else if (kind === 'pie' && series.length > 0) {
    body = <PiePreview labels={labels} series={series} />
  } else {
    // no data, or a chart kind we don't have a preview for yet: fall back
    // to the plain data table rather than drawing an empty/misleading plot
    body = <ChartTable labels={labels} series={series} />
  }

  return (
    <figure className="blk-chart">
      {block.title && <figcaption className="blk-chart-title">{block.title}</figcaption>}
      {body}
      {block.caption && <div className="blk-caption">{block.caption}</div>}
    </figure>
  )
}

// artifact_id -> in-flight/resolved render.svg fetch. Shared across every
// mount of the same block: the deck re-renders on unrelated edits elsewhere
// in the doc, and this cache is what keeps that from re-fetching (or
// flickering) a diagram's preview each time.
const artifactSvgCache = new Map<string, Promise<string | null>>()

/** Fetch and cache a diagram/artifact block's engine-rendered render.svg by
 * artifact_id — the same file `_resolve_artifact_render` bakes into the
 * export, so the in-deck preview becomes pixel-faithful to it. Exported for
 * testing. Resolves to `null` (never rejects) on a 404 or network failure so
 * callers can fall back to the legacy `block.path` <img>. */
export function fetchArtifactSvg(artifactId: string): Promise<string | null> {
  const cached = artifactSvgCache.get(artifactId)
  if (cached) return cached
  const pending = fetch(`/api/artifacts/${artifactId}/render.svg`)
    .then((r) => (r.ok ? r.text() : null))
    .catch(() => null)
  artifactSvgCache.set(artifactId, pending)
  return pending
}

/** Test-only: forget every cached render.svg fetch. */
export function _resetArtifactSvgCacheForTests(): void {
  artifactSvgCache.clear()
}

/** The engine's SVG output always opens with a single `<svg xmlns=... ...>`
 * tag at fixed native pixel dimensions (docloom's own `_stamp_hash` in
 * diagram_svg.py relies on the same invariant to inject its content-hash
 * attribute) — inject a style attribute the same way, so the inlined
 * diagram scales to its block instead of overflowing or rendering tiny.
 * Exported for testing. */
export function scaleSvgMarkup(svg: string): string {
  const marker = '<svg '
  const i = svg.indexOf(marker)
  if (i === -1) return svg
  const at = i + marker.length
  return `${svg.slice(0, at)}style="max-width:100%;height:auto;display:block" ${svg.slice(at)}`
}

/** image / artifact block: prefer the engine-rendered render.svg (resolved
 * by artifact_id) over the legacy `block.path` <img>, which stays null until
 * export's bake() sets it (irx.py). Falls back to `path` when there's no
 * artifact_id, or the artifact has no render yet (404). */
function ArtifactImage({ block }: { block: Block }) {
  const artifactId = block.artifact_id ?? null
  const [svg, setSvg] = useState<string | null>(null)
  const [pending, setPending] = useState(!!artifactId)

  useEffect(() => {
    if (!artifactId) {
      setSvg(null)
      setPending(false)
      return
    }
    let cancelled = false
    setPending(true)
    fetchArtifactSvg(artifactId).then((result) => {
      if (cancelled) return
      setSvg(result)
      setPending(false)
    })
    return () => {
      cancelled = true
    }
  }, [artifactId])

  if (artifactId && pending) {
    // usually resolves instantly (cached) or near-instantly; avoid flashing
    // the "empty image slot" placeholder while it's in flight
    return <div className="blk-image-slot" aria-busy="true" />
  }
  if (artifactId && svg) {
    return (
      <figure className="blk-figure">
        <div style={{ maxWidth: '100%' }} dangerouslySetInnerHTML={{ __html: scaleSvgMarkup(svg) }} />
        {block.caption && <div className="blk-caption">{block.caption}</div>}
      </figure>
    )
  }

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
      return <ArtifactImage block={block} />
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
