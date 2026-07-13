import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { Block, BrandLogoT, DocumentT, SlideT, StudioTheme } from './types'
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

// The active brand logo, fetched once per session and cached at module scope
// (same pattern as useThemes.ts). docloom only stamps `doc.logo` at export
// time, so the stored deck IR never carries a real logo path: the live
// preview must source it from the brand kit directly, the same served-URL
// pattern imageUrl() above uses for asset:// slide images.
type BrandLogoState = BrandLogoT | null
let brandLogoCache: BrandLogoState | undefined
let brandLogoInflight: Promise<BrandLogoState> | null = null

async function fetchBrandLogo(): Promise<BrandLogoState> {
  try {
    const brand = await api.get<{ logo_asset_id?: string | null }>('/api/brand-kit')
    return brand.logo_asset_id ? { url: `/api/assets/${brand.logo_asset_id}/file`, alt: 'logo' } : null
  } catch {
    return null
  }
}

export function useBrandLogo(): BrandLogoState {
  const [logo, setLogo] = useState<BrandLogoState>(brandLogoCache ?? null)
  useEffect(() => {
    if (brandLogoCache !== undefined) return
    if (!brandLogoInflight) brandLogoInflight = fetchBrandLogo()
    let cancelled = false
    brandLogoInflight.then((l) => {
      brandLogoCache = l
      if (!cancelled) setLogo(l)
    })
    return () => {
      cancelled = true
    }
  }, [])
  return logo
}

/** Call after any brand-kit mutation (upload auto-bind, manual save) so the
 *  next preview mount refetches instead of trusting a stale cached logo. */
export function invalidateBrandLogoCache(): void {
  brandLogoCache = undefined
  brandLogoInflight = null
}

// Mirrors docloom's pptx _doc_logo exactly, so the preview never shows a
// corner/contrast combination the export would not actually produce:
//  - section fills the background with theme.primary -> always scrim.
//  - hero covers the slide with its own image -> scrim only when that image
//    is actually present (no image there just falls back to the flat
//    background, same as a content slide, so no scrim is needed).
//  - image_right's image pane reaches the top-right corner, so its logo
//    moves to the opposite (top-left) corner instead of landing on the pane.
//  - image_left's pane is already on the left, so the default top-right
//    corner is safe as-is, same as every other layout.
export function logoPlacement(layout: string, hasImage: boolean): { scrim: boolean; corner: 'left' | 'right' } {
  if (layout === 'section') return { scrim: true, corner: 'right' }
  if (layout === 'hero') return { scrim: hasImage, corner: 'right' }
  // image_right only pushes the logo to the opposite corner when its pane
  // actually holds an image (matches the export's _usable_image guard); an
  // image-less image_right keeps the default top-right, same as the export
  if (layout === 'image_right' && hasImage) return { scrim: false, corner: 'left' }
  return { scrim: false, corner: 'right' }
}

/** Top-right (or top-left, see logoPlacement) brand-logo overlay, shared by
 *  SlideView and EditableSlide so the read and edit surfaces never drift
 *  apart. Mirrors docloom's pptx target: about 0.5in tall (48px at the
 *  stage's 96px/in). */
export function BrandLogoMark({
  logo, scrim, corner = 'right',
}: { logo: BrandLogoT | null; scrim: boolean; corner?: 'left' | 'right' }) {
  if (!logo) return null
  return (
    <div
      className="brand-logo-mark"
      style={{
        position: 'absolute',
        top: 28,
        ...(corner === 'left' ? { left: 32 } : { right: 32 }),
        zIndex: 5,
        display: 'flex',
        pointerEvents: 'none',
        ...(scrim
          ? { background: 'rgba(255,255,255,0.88)', borderRadius: 6, padding: '6px 10px' }
          : {}),
      }}
    >
      <img
        src={logo.url}
        alt={logo.alt}
        style={{ display: 'block', maxHeight: 48, maxWidth: 200, objectFit: 'contain' }}
      />
    </div>
  )
}

function TitleBand({
  title, accent, reserveLogo,
}: { title?: string | null; accent?: string | null; reserveLogo?: boolean }) {
  if (!title) return null
  return (
    <div
      className="slot title-band"
      style={{ ...accentStyle(accent), ...(reserveLogo ? { right: 240 } : {}) }}
    >
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
  const logo = useBrandLogo()
  const { scrim, corner } = logoPlacement(slide.layout, usableImage(slide.image))
  const logoMark = <BrandLogoMark logo={logo} scrim={scrim} corner={corner} />

  switch (slide.layout) {
    case 'title':
      return (
        <div className={stage('layout-title')} style={acc}>
          {logoMark}
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
          {logoMark}
          <h1>{slide.title}</h1>
          {slide.subtitle && <div className="subtitle">{slide.subtitle}</div>}
        </div>
      )
    case 'quote': {
      const q = blocks.find((b) => b.type === 'quote')
      return (
        <div className={stage('layout-quote')} style={acc}>
          {logoMark}
          <div className="accent-bar" />
          <div className="quote-text">{q ? plain(q.text) : slide.title}</div>
          {q?.attribution && <cite>— {q.attribution}</cite>}
        </div>
      )
    }
    case 'hero':
      return (
        <div className={stage('layout-hero')} style={acc}>
          {logoMark}
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
      // image_left keeps the logo top-right, over this pane's own text side,
      // so the reserve carves from the right; image_right flips the logo to
      // top-left (see logoPlacement), so the reserve carves from the left,
      // where the logo actually sits.
      const textInset =
        side === 'left'
          ? { left: 'calc(45% + 40px)', right: logo ? '240px' : '56px' }
          : { left: logo ? '240px' : '56px', right: 'calc(45% + 40px)' }
      return (
        <div className={stage()} style={acc}>
          {logoMark}
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
          {logoMark}
          <TitleBand title={slide.title} accent={slide.accent} reserveLogo={!!logo} />
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
          {logoMark}
          <TitleBand title={slide.title} accent={slide.accent} reserveLogo={!!logo} />
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
