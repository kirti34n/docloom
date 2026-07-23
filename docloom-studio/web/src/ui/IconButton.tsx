import type { ButtonHTMLAttributes, ReactNode } from 'react'

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode
  /** Accessible name -- IconButton has no visible text, so this is required,
   *  not optional. Also shown as the native tooltip via title. */
  label: string
}

/** A bare icon control with a minimum 32x32 hit area (rail tools, dismiss
 *  buttons, toolbar glyphs). Pass a 16-18px lucide icon as children. */
export function IconButton({ children, label, className = '', ...rest }: IconButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-sm)] text-ws-muted transition-colors duration-[var(--dur-fast)] hover:bg-[color-mix(in_srgb,var(--ink)_8%,transparent)] hover:text-ws-ink active:bg-[color-mix(in_srgb,var(--ink)_13%,transparent)] disabled:pointer-events-none disabled:opacity-50 ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}
