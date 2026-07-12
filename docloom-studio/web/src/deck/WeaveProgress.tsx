import { useEffect, useState } from 'react'
import type { WeaveSource, WeaveUnit } from './buildProgress'
import './weave.css'

// grid geometry, in SVG user units
const COL = 34 // px between warp threads
const ROW = 34 // px between weft rows
const TOP = 30 // room for the source index labels above the grid
const SIDE = 16 // margin either side of the outermost warp thread
const LABEL_GAP = 22 // gap from the grid's right edge to the unit label

const STATUS_WORD: Record<WeaveUnit['status'], string> = {
  pending: 'waiting',
  running: 'weaving',
  done: 'woven',
  skipped: 'skipped',
}

const pad2 = (n: number) => String(n).padStart(2, '0')

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduced
}

/** Fallback list used when there is nothing to draw a loom from (a notebook
 *  with no enabled sources): still shows real progress, just not the graphic. */
function PlainProgress({ units }: { units: WeaveUnit[] }) {
  return (
    <div>
      <Eyebrow />
      {units.length === 0 ? (
        <p className="mt-2 text-[13px] text-stage-muted">Weaving…</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1.5">
          {units.map((u) => (
            <li key={u.key} className="flex items-center justify-between gap-3 text-[13px]">
              <span
                className={
                  u.status === 'done' ? 'text-white' : u.status === 'skipped' ? 'text-madder' : 'text-stage-muted'
                }
              >
                {u.label || `Item ${u.key}`}
              </span>
              <span className="shrink-0 text-[11px] text-stage-muted">{STATUS_WORD[u.status]}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Eyebrow() {
  return <p className="font-mono text-[11px] uppercase tracking-[0.08em] text-stage-muted">The weave</p>
}

/** stroke-dashoffset draw-in that autoplays on mount; the reduced-motion
 *  branch skips the animation entirely (see weave.css: no `from` state is
 *  ever reached, so the line just renders solid). */
function drawStyle(reduced: boolean, len: number, delayMs = 0): React.CSSProperties | undefined {
  if (reduced) return undefined
  return {
    '--weave-len': String(len),
    strokeDasharray: String(len),
    animation: `weave-draw var(--dur-slow) var(--ease-out) ${delayMs}ms forwards`,
  } as React.CSSProperties
}

/** Draws the loom: one vertical warp line per enabled source, and one
 *  horizontal weft line per unit that has landed (done or skipped), knotted
 *  at the warp threads that unit's content actually cited. The document is
 *  visibly woven out of the user's own sources while they watch. */
export function WeaveProgress({ sources, units }: { sources: WeaveSource[]; units: WeaveUnit[] }) {
  const reduced = usePrefersReducedMotion()
  const enabled = sources.filter((s) => s.enabled)

  if (enabled.length === 0) return <PlainProgress units={units} />

  const rows = Math.max(units.length, 1)
  const warpX = enabled.map((_, i) => SIDE + i * COL)
  const gridLeft = SIDE - 12
  const gridRight = warpX[warpX.length - 1] + 12
  const warpTop = TOP - 8
  const warpBottom = TOP + rows * ROW + 4
  const warpLen = warpBottom - warpTop
  const weftLen = gridRight - gridLeft
  const labelX = gridRight + LABEL_GAP
  const width = labelX + 220
  const height = warpBottom + 12
  const bySourceX = new Map(enabled.map((s, i) => [s.id, warpX[i]] as const))
  const doneCount = units.filter((u) => u.status === 'done' || u.status === 'skipped').length

  return (
    <div>
      <Eyebrow />
      <div className="mt-2 overflow-x-auto">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          role="img"
          aria-label={`Weaving ${doneCount} of ${units.length || 'unknown'} from ${enabled.length} source${enabled.length === 1 ? '' : 's'}`}
          className="block"
        >
          {enabled.map((s, i) => (
            <g key={s.id}>
              <text x={warpX[i]} y={TOP - 14} textAnchor="middle" className="weave-index">
                {pad2(s.index)}
              </text>
              <line
                x1={warpX[i]}
                y1={warpTop}
                x2={warpX[i]}
                y2={warpBottom}
                className="weave-warp"
                style={drawStyle(reduced, warpLen, i * 40)}
              />
            </g>
          ))}

          {units.map((unit, j) => {
            const y = TOP + j * ROW + ROW / 2
            const landed = unit.status === 'done' || unit.status === 'skipped'
            return (
              <g key={unit.key}>
                {landed ? (
                  <>
                    <line
                      x1={gridLeft}
                      y1={y}
                      x2={gridRight}
                      y2={y}
                      className={unit.status === 'skipped' ? 'weave-weft weave-weft-skipped' : 'weave-weft'}
                      style={drawStyle(reduced, weftLen)}
                    />
                    {unit.citedSourceIds?.map((sid) => {
                      const x = bySourceX.get(sid)
                      if (x == null) return null
                      return (
                        <circle
                          key={sid}
                          cx={x}
                          cy={y}
                          r={3.5}
                          className="weave-knot"
                          style={
                            reduced
                              ? undefined
                              : { animation: 'weave-knot-in var(--dur-fast) var(--ease-out) var(--dur-slow) forwards' }
                          }
                        />
                      )
                    })}
                  </>
                ) : unit.status === 'running' ? (
                  <circle cx={gridLeft} cy={y} r={3} className="weave-pulse" />
                ) : (
                  <line x1={gridLeft} y1={y} x2={gridLeft + 12} y2={y} stroke="var(--stage-rule)" strokeWidth={1.5} />
                )}
                <text
                  x={labelX}
                  y={y}
                  className={`weave-label ${
                    unit.status === 'done' ? 'weave-label-done' : unit.status === 'skipped' ? 'weave-label-skipped' : ''
                  }`}
                >
                  {unit.label || `Item ${unit.key}`}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
    </div>
  )
}
