import type { ReactNode } from 'react'

/** Uppercase mono label set above a heading or section. */
export function Eyebrow({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <p className={`font-mono text-[11px] uppercase leading-4 tracking-[0.08em] text-ws-muted ${className}`}>
      {children}
    </p>
  )
}
