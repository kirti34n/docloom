import type { ReactNode } from 'react'

export type EyebrowTone = 'default' | 'stage'

const TONE_CLASS: Record<EyebrowTone, string> = {
  default: 'text-ws-muted',
  // for use on dark editor panels (e.g. the --stage deck canvas), where
  // text-ws-muted's light-ground contrast (~2.9:1) fails against --stage
  stage: 'text-stage-muted',
}

/** Uppercase mono label set above a heading or section. */
export function Eyebrow({
  children,
  className = '',
  tone = 'default',
}: {
  children: ReactNode
  className?: string
  tone?: EyebrowTone
}) {
  return (
    <p
      className={`font-mono text-2xs font-semibold uppercase leading-4 tracking-[0.05em] ${TONE_CLASS[tone]} ${className}`}
    >
      {children}
    </p>
  )
}
