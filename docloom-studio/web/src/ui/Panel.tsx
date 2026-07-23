import type { HTMLAttributes } from 'react'

/** A resting surface: hairline edge + the one calibrated resting shadow
 *  (--shadow-card). Background stays vellum -- never a white card. Pass
 *  padding/layout utilities via className. */
export function Panel({ className = '', ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`rounded-[var(--radius)] border border-rule-strong bg-ws-panel shadow-[var(--shadow-card)] ${className}`}
      {...rest}
    />
  )
}
