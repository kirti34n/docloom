import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { api } from '../api/client'
import type { Block, SlideT, StudioTheme } from './types'
import { useDeck } from './deckStore'

interface Asset {
  id: string
  type: string
  filename: string
}

const IMAGE_LAYOUTS = ['image_left', 'image_right', 'hero', 'title']

const LAYOUTS = [
  ['content', 'Content'],
  ['two_column', 'Two column'],
  ['section', 'Section'],
  ['title', 'Title'],
  ['quote', 'Quote'],
  ['hero', 'Hero'],
  ['image_left', 'Image left'],
  ['image_right', 'Image right'],
] as const

export function Inspector({ themes }: { themes: StudioTheme[] }) {
  const slideId = useDeck((s) => s.selected)
  const slide = useDeck((s) => (s.selected ? s.slides[s.selected] : null))
  const themeName = useDeck((s) => s.themeName)
  const findings = useDeck((s) => s.findings)
  const updateSlide = useDeck((s) => s.updateSlide)
  const setTheme = useDeck((s) => s.setTheme)

  const [assets, setAssets] = useState<Asset[]>([])
  useEffect(() => {
    api
      .get<Asset[]>('/api/assets')
      .then((a) => setAssets(a.filter((x) => x.type === 'image' || x.type === 'logo')))
      .catch(() => {})
  }, [])

  const imgPath = slide?.image?.path ?? ''
  const currentAssetId =
    slide?.image?.asset_id ?? (imgPath.startsWith('asset://') ? imgPath.slice(8) : null)

  const setImage = (id: string | null) => {
    if (!slideId || !slide) return
    if (id === null) {
      updateSlide(slideId, { image: null })
      return
    }
    const patch: Partial<SlideT> = {
      image: { type: 'image', path: `asset://${id}`, asset_id: id } as Block,
    }
    if (!IMAGE_LAYOUTS.includes(slide.layout)) patch.layout = 'image_left'
    updateSlide(slideId, patch)
  }

  const theme = themes.find((t) => t.name === themeName)
  const accents = theme
    ? [theme.primary, theme.accent, theme.accent_2].filter(Boolean) as string[]
    : []
  const slideFindings = findings.filter(
    (f) => slideId && f.where.includes(`slides[${useDeck.getState().order.indexOf(slideId)}]`),
  )

  return (
    <aside className="flex w-64 shrink-0 flex-col gap-6 overflow-auto border-l border-stage-line p-4 text-white">
      {/* theme */}
      <section>
        <h3 className="font-mono text-[11px] uppercase tracking-[0.08em] text-stage-muted">
          Theme
        </h3>
        <div className="mt-2 grid grid-cols-1 gap-1.5">
          {themes.map((t) => (
            <button
              key={t.name}
              onClick={() => setTheme(t.name)}
              className={`flex items-center gap-2 rounded-[var(--radius)] border px-2.5 py-2 text-left text-[13px] ${
                t.name === themeName ? 'border-ws-accent' : 'border-stage-line'
              }`}
            >
              <span className="flex gap-1">
                {[t.primary, t.accent, t.background].map((c, i) => (
                  <i key={i} className="h-3.5 w-3.5 rounded-full" style={{ background: c }} />
                ))}
              </span>
              {t.label ?? t.name}
            </button>
          ))}
        </div>
      </section>

      {slide && (
        <>
          <section>
            <h3 className="font-mono text-[11px] uppercase tracking-[0.08em] text-stage-muted">
              Layout
            </h3>
            <select
              value={slide.layout}
              onChange={(e) => updateSlide(slideId!, { layout: e.target.value })}
              className="mt-2 w-full rounded-[var(--radius)] border border-stage-line bg-stage-bg px-2.5 py-2 text-[13px]"
            >
              {LAYOUTS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </section>

          <section>
            <h3 className="font-mono text-[11px] uppercase tracking-[0.08em] text-stage-muted">
              Image
            </h3>
            <div className="mt-2 grid grid-cols-3 gap-1.5">
              <button
                onClick={() => setImage(null)}
                title="No image"
                className={`flex h-12 items-center justify-center rounded-[var(--radius-sm)] border ${
                  !currentAssetId ? 'border-ws-accent' : 'border-stage-line'
                }`}
              >
                <X size={14} className="text-stage-muted" />
              </button>
              {assets.map((a) => (
                <button
                  key={a.id}
                  onClick={() => setImage(a.id)}
                  title={a.filename}
                  className={`overflow-hidden rounded-[var(--radius-sm)] border-2 ${
                    currentAssetId === a.id ? 'border-ws-accent' : 'border-transparent'
                  }`}
                >
                  <img
                    src={`/api/assets/${a.id}/file`}
                    alt={a.filename}
                    className="h-12 w-full object-cover"
                  />
                </button>
              ))}
            </div>
            {assets.length === 0 && (
              <p className="mt-2 text-[12px] text-stage-muted">
                Upload images under Assets to place them on slides.
              </p>
            )}
          </section>

          <section>
            <h3 className="font-mono text-[11px] uppercase tracking-[0.08em] text-stage-muted">
              Accent
            </h3>
            <div className="mt-2 flex gap-2">
              <button
                onClick={() => updateSlide(slideId!, { accent: null })}
                className={`h-7 w-7 rounded-full border ${
                  !slide.accent ? 'border-ws-accent' : 'border-stage-line'
                }`}
                title="Theme default"
              >
                <span className="text-[10px] text-stage-muted">—</span>
              </button>
              {accents.map((c) => (
                <button
                  key={c}
                  onClick={() => updateSlide(slideId!, { accent: c })}
                  className={`h-7 w-7 rounded-full border-2 ${
                    slide.accent === c ? 'border-white' : 'border-transparent'
                  }`}
                  style={{ background: c }}
                />
              ))}
            </div>
          </section>

          <section>
            <h3 className="font-mono text-[11px] uppercase tracking-[0.08em] text-stage-muted">
              Speaker notes
            </h3>
            <textarea
              value={slide.notes ?? ''}
              onChange={(e) => updateSlide(slideId!, { notes: e.target.value })}
              rows={4}
              placeholder="Only you see these…"
              className="mt-2 w-full resize-none rounded-[var(--radius)] border border-stage-line bg-stage-bg px-2.5 py-2 text-[13px]"
            />
          </section>

          {slideFindings.length > 0 && (
            <section>
              <h3 className="font-mono text-[11px] uppercase tracking-[0.08em] text-stage-muted">
                Checks
              </h3>
              <ul className="mt-2 space-y-1.5">
                {slideFindings.map((f, i) => (
                  <li
                    key={i}
                    className={`rounded-[var(--radius-sm)] px-2 py-1.5 text-[12px] ${
                      f.severity === 'error'
                        ? 'bg-madder/15 text-[color-mix(in_srgb,var(--madder)_65%,white)]'
                        : 'bg-ws-warn/15 text-[color-mix(in_srgb,var(--ws-warn)_65%,white)]'
                    }`}
                  >
                    {f.message}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </aside>
  )
}
