import type { StudioTheme } from './types'
import { useDeck } from './deckStore'

const LAYOUTS = [
  ['content', 'Content'],
  ['two_column', 'Two column'],
  ['section', 'Section'],
  ['title', 'Title'],
  ['quote', 'Quote'],
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
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-stage-muted">
          Theme
        </h3>
        <div className="mt-2 grid grid-cols-1 gap-1.5">
          {themes.map((t) => (
            <button
              key={t.name}
              onClick={() => setTheme(t.name)}
              className={`flex items-center gap-2 rounded-lg border px-2.5 py-2 text-left text-[13px] ${
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
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-stage-muted">
              Layout
            </h3>
            <select
              value={slide.layout}
              onChange={(e) => updateSlide(slideId!, { layout: e.target.value })}
              className="mt-2 w-full rounded-lg border border-stage-line bg-stage-bg px-2.5 py-2 text-[13px]"
            >
              {LAYOUTS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </section>

          <section>
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-stage-muted">
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
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-stage-muted">
              Speaker notes
            </h3>
            <textarea
              value={slide.notes ?? ''}
              onChange={(e) => updateSlide(slideId!, { notes: e.target.value })}
              rows={4}
              placeholder="Only you see these…"
              className="mt-2 w-full resize-none rounded-lg border border-stage-line bg-stage-bg px-2.5 py-2 text-[13px]"
            />
          </section>

          {slideFindings.length > 0 && (
            <section>
              <h3 className="text-[11px] font-semibold uppercase tracking-wide text-stage-muted">
                Checks
              </h3>
              <ul className="mt-2 space-y-1.5">
                {slideFindings.map((f, i) => (
                  <li
                    key={i}
                    className={`rounded px-2 py-1.5 text-[12px] ${
                      f.severity === 'error'
                        ? 'bg-red-500/15 text-red-300'
                        : 'bg-amber-500/15 text-amber-300'
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
