import type { RichText as RichTextT, Span } from './types'

export function spansOf(rt: RichTextT | undefined): Span[] {
  if (!rt) return []
  return typeof rt === 'string' ? [{ text: rt }] : rt
}

export function plain(rt: RichTextT | undefined): string {
  return spansOf(rt)
    .map((s) => s.text)
    .join('')
}

export function RichText({
  value,
  citeNumbers,
}: {
  value: RichTextT | undefined
  citeNumbers?: Map<string, number>
}) {
  const spans = spansOf(value)
  return (
    <>
      {spans.map((s, i) => {
        let node: React.ReactNode = s.text
        if (s.code) node = <code className="rt-code">{node}</code>
        if (s.bold) node = <strong>{node}</strong>
        if (s.italic) node = <em>{node}</em>
        if (s.link) {
          const safe = /^(https?:|mailto:)/i.test(s.link)
          node = safe ? (
            <a href={s.link} target="_blank" rel="noreferrer" className="rt-link">
              {node}
            </a>
          ) : (
            node
          )
        }
        return (
          <span key={i}>
            {node}
            {s.cite && (
              <sup className="rt-cite" data-known={citeNumbers?.has(s.cite) ?? false}>
                {citeNumbers?.get(s.cite) ?? '?'}
              </sup>
            )}
          </span>
        )
      })}
    </>
  )
}
