import { useLayoutEffect, useRef, useState } from 'react'
import type { Block, DocumentT, SlideT, StudioTheme } from './types'
import { themeVars } from './types'
import { BlockView } from './blocks'
import { plain } from './RichText'
import './stage.css'

const STAGE_W = 1280
const STAGE_H = 720

function citeMap(doc: DocumentT): Map<string, number> {
  const m = new Map<string, number>()
  ;(doc.sources ?? []).forEach((s, i) => {
    if (!m.has(s.id)) m.set(s.id, i + 1)
  })
  return m
}

function usableImage(img: Block | null | undefined): boolean {
  return !!img?.path
}

function imageUrl(path: string): string {
  if (path.startsWith('asset://')) return `/api/assets/${path.slice(8)}/file`
  if (/^https?:/i.test(path)) return path
  return `/api/files?path=${encodeURIComponent(path)}`
}

function TitleBand({ title, accent }: { title?: string | null; accent?: string | null }) {
  if (!title) return null
  return (
    <div className="slot title-band" style={accentStyle(accent)}>
      <h2>{title}</h2>
      <div className="title-rule" />
    </div>
  )
}

function accentStyle(accent?: string | null): React.CSSProperties {
  return accent ? ({ '--slide-accent': accent } as React.CSSProperties) : {}
}

function Body({ blocks, cites }: { blocks: Block[]; cites: Map<string, number> }) {
  const ref = useRef<HTMLDivElement>(null)
  const [overflow, setOverflow] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (el) setOverflow(el.scrollHeight > el.clientHeight + 2)
  }, [blocks])
  return (
    <div ref={ref} className={`slot body-flow ${overflow ? 'overflowing' : ''}`}>
      {blocks.map((b, i) => (
        <BlockView key={b.id ?? i} block={b} citeNumbers={cites} />
      ))}
    </div>
  )
}

export function SlideView({
  slide,
  doc,
  className = '',
}: {
  slide: SlideT
  doc: DocumentT
  className?: string
}) {
  const cites = citeMap(doc)
  const blocks = slide.blocks ?? []
  const acc = accentStyle(slide.accent)
  const stage = (extra = '') => ['deck-stage', extra, className].filter(Boolean).join(' ')

  switch (slide.layout) {
    case 'title':
      return (
        <div className={stage('layout-title')} style={acc}>
          <div className="edge-bar" />
          <h1>{slide.title ?? doc.title}</h1>
          {slide.subtitle && <div className="subtitle">{slide.subtitle}</div>}
          {(doc.authors?.length || doc.date) && (
            <div className="byline">
              {[doc.authors?.join(', '), doc.date].filter(Boolean).join('  ·  ')}
            </div>
          )}
        </div>
      )
    case 'section':
      return (
        <div className={stage('layout-section')}>
          <h1>{slide.title}</h1>
          {slide.subtitle && <div className="subtitle">{slide.subtitle}</div>}
        </div>
      )
    case 'quote': {
      const q = blocks.find((b) => b.type === 'quote')
      return (
        <div className={stage('layout-quote')} style={acc}>
          <div className="accent-bar" />
          <div className="quote-text">{q ? plain(q.text) : slide.title}</div>
          {q?.attribution && <cite>— {q.attribution}</cite>}
        </div>
      )
    }
    case 'hero':
      return (
        <div className={stage('layout-hero')} style={acc}>
          {usableImage(slide.image) && (
            <img className="hero-img" src={imageUrl(slide.image!.path!)} alt="" />
          )}
          <div className="hero-band">
            <h1>{slide.title}</h1>
          </div>
        </div>
      )
    case 'image_left':
    case 'image_right': {
      const side = slide.layout === 'image_left' ? 'left' : 'right'
      const textInset =
        side === 'left'
          ? { left: 'calc(45% + 40px)', right: '56px' }
          : { left: '56px', right: 'calc(45% + 40px)' }
      return (
        <div className={stage()} style={acc}>
          <div className={`image-pane ${side}`}>
            {usableImage(slide.image) ? (
              <img src={imageUrl(slide.image!.path!)} alt={slide.image!.alt ?? ''} />
            ) : (
              <div className="slot-empty">
                {slide.image?.query ? `image: ${slide.image.query}` : 'image slot'}
              </div>
            )}
          </div>
          <div className="slot" style={{ top: '56px', ...textInset }}>
            {slide.title && <h2 className="blk-heading lvl-1">{slide.title}</h2>}
          </div>
          <div className="slot body-flow" style={{ top: '150px', ...textInset, bottom: '56px' }}>
            {blocks.map((b, i) => (
              <BlockView key={b.id ?? i} block={b} citeNumbers={cites} />
            ))}
          </div>
        </div>
      )
    }
    case 'two_column':
      return (
        <div className={stage()} style={acc}>
          <TitleBand title={slide.title} accent={slide.accent} />
          <div className="slot body-flow">
            <div className="two-cols">
              <div className="blk-list" style={{ gap: 18 }}>
                {blocks.map((b, i) => (
                  <BlockView key={b.id ?? i} block={b} citeNumbers={cites} />
                ))}
              </div>
              <div className="blk-list" style={{ gap: 18 }}>
                {(slide.right ?? []).map((b, i) => (
                  <BlockView key={b.id ?? i} block={b} citeNumbers={cites} />
                ))}
              </div>
            </div>
          </div>
        </div>
      )
    default:
      return (
        <div className={stage()} style={acc}>
          <TitleBand title={slide.title} accent={slide.accent} />
          <Body blocks={blocks} cites={cites} />
        </div>
      )
  }
}

/** A slide scaled to fit its container while holding 16:9. */
export function ScaledSlide({
  slide,
  doc,
  theme,
}: {
  slide: SlideT
  doc: DocumentT
  theme: StudioTheme
}) {
  const wrap = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(0.5)

  useLayoutEffect(() => {
    const el = wrap.current
    if (!el) return
    const resize = () => {
      const w = el.clientWidth
      setScale(w / STAGE_W)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const vars = themeVars(theme) as React.CSSProperties
  return (
    <div
      ref={wrap}
      className="deck-scale-wrap"
      style={{ width: '100%', aspectRatio: `${STAGE_W} / ${STAGE_H}` }}
    >
      <div
        style={{
          width: STAGE_W,
          height: STAGE_H,
          transform: `scale(${scale})`,
          transformOrigin: 'top left',
          ...vars,
        }}
      >
        <SlideView slide={slide} doc={doc} className={theme.bg_style === 'gradient' ? 'bg-gradient' : ''} />
      </div>
    </div>
  )
}
