import type { HTMLAttributes } from 'react'

/** A hairline-bordered surface. Separation comes from the rule, not a
 *  shadow: nothing floats. Pass padding/layout utilities via className. */
export function Panel({ className = '', ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`rounded-[var(--radius)] border border-ws-line bg-ws-panel ${className}`} {...rest} />
}
