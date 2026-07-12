/** A citation: a mono numeral in brass with a hairline underline, set
 *  superscript. Appears in chat answers, generated docs, slides, and the
 *  build view. Hovering or focusing it lights the matching thread in the
 *  warp rail: any element carrying a matching data-warp-id, wherever it
 *  renders (see the [data-warp-id] rule in index.css). Clicking it calls
 *  onOpen so the caller can scroll the source reader to the cited chunk. */
function lightWarp(sourceId: string, on: boolean) {
  document
    .querySelectorAll(`[data-warp-id="${CSS.escape(sourceId)}"]`)
    .forEach((el) => el.classList.toggle('is-lit', on))
}

export function SourceMark({
  n,
  sourceId,
  onOpen,
}: {
  n: number
  sourceId: string
  onOpen?: (id: string) => void
}) {
  const index = String(n).padStart(2, '0')
  return (
    <button
      type="button"
      data-source-id={sourceId}
      onMouseEnter={() => lightWarp(sourceId, true)}
      onMouseLeave={() => lightWarp(sourceId, false)}
      onFocus={() => lightWarp(sourceId, true)}
      onBlur={() => lightWarp(sourceId, false)}
      onClick={() => onOpen?.(sourceId)}
      title={`Source ${index}`}
      className="align-super mx-px cursor-pointer border-b border-brass px-px font-mono text-[0.68em] leading-none text-brass hover:bg-brass/10"
    >
      {index}
    </button>
  )
}
