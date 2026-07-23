import { useState } from 'react'

/** A citation: a small brass pill carrying a mono numeral, set superscript.
 *  Appears in chat answers, generated docs, slides, and the build view.
 *  Hovering or focusing it lights the matching thread in the warp rail: any
 *  element carrying a matching data-warp-id, wherever it renders (see the
 *  [data-warp-id] rule in index.css). Clicking it calls onOpen so the caller
 *  can scroll the source reader to the cited chunk. Pass `snippet` (the
 *  cited chunk's own text) to also show a hover popover -- it is truncated
 *  to 40 words here so callers can pass the full chunk untrimmed. */
function lightWarp(sourceId: string, on: boolean) {
  document
    .querySelectorAll(`[data-warp-id="${CSS.escape(sourceId)}"]`)
    .forEach((el) => el.classList.toggle('is-lit', on))
}

function truncateWords(text: string, maxWords: number): string {
  const words = text.trim().split(/\s+/)
  if (words.length <= maxWords) return text.trim()
  return `${words.slice(0, maxWords).join(' ')}…`
}

export function SourceMark({
  n,
  sourceId,
  snippet,
  onOpen,
}: {
  n: number
  sourceId: string
  snippet?: string
  onOpen?: (id: string) => void
}) {
  const [hovered, setHovered] = useState(false)
  const index = String(n).padStart(2, '0')
  const preview = snippet ? truncateWords(snippet, 40) : null

  const show = () => {
    setHovered(true)
    lightWarp(sourceId, true)
  }
  const hide = () => {
    setHovered(false)
    lightWarp(sourceId, false)
  }

  return (
    <span className="relative inline-block">
      <button
        type="button"
        data-source-id={sourceId}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onClick={() => onOpen?.(sourceId)}
        title={preview ?? `Source ${index}`}
        // 11px is a deliberate exception to the 12px type-scale floor: this
        // is a micro citation numeral, not body/label text (see
        // scripts/check-type-scale.mjs, which excludes this file by name).
        className="mx-px inline-flex min-h-[16px] min-w-[20px] cursor-pointer items-center justify-center rounded-full bg-brass/12 px-[5px] py-px align-super font-mono text-[11px] font-semibold leading-none text-brass-ink transition-colors duration-[var(--dur-fast)] hover:bg-brass/22"
      >
        {index}
      </button>
      {preview && hovered ? (
        <span
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 w-64 -translate-x-1/2 rounded-[var(--radius-sm)] border border-rule-strong bg-ws-panel px-2.5 py-2 text-xs normal-case leading-snug text-ws-ink shadow-[var(--shadow-float)]"
        >
          {preview}
        </span>
      ) : null}
    </span>
  )
}
