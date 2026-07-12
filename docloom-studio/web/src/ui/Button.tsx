import type { ButtonHTMLAttributes } from 'react'

export type ButtonVariant = 'primary' | 'accent' | 'quiet' | 'danger'

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  // ink: the default primary action
  primary: 'border border-ws-ink bg-ws-ink text-ws-bg hover:opacity-90',
  // brass: the signature. Scarce, one per screen.
  accent: 'border border-brass bg-brass text-ws-bg hover:opacity-90',
  // understated: secondary and tertiary actions
  quiet: 'border border-ws-line bg-transparent text-ws-ink hover:bg-ws-panel',
  // madder: destructive actions
  danger: 'border border-madder bg-madder text-ws-bg hover:opacity-90',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
}

export function Button({ variant = 'primary', className = '', children, ...rest }: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-[var(--radius-sm)] px-3.5 py-2 text-[13px] font-medium transition-opacity duration-[var(--dur-fast)] disabled:pointer-events-none disabled:opacity-50 ${VARIANT_CLASS[variant]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}
