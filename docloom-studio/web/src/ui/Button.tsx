import type { ButtonHTMLAttributes } from 'react'

export type ButtonVariant = 'primary' | 'accent' | 'quiet' | 'danger'
export type ButtonSize = 'sm' | 'default' | 'cta'

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  // ink: the default primary action
  primary:
    'border border-ws-ink bg-ws-ink text-ws-bg hover:opacity-90 active:opacity-100 active:bg-[color-mix(in_srgb,var(--ink)_85%,black)]',
  // brass: the signature. Scarce, one per screen.
  accent:
    'border border-brass bg-brass text-ws-bg hover:opacity-90 active:opacity-100 active:bg-[color-mix(in_srgb,var(--brass)_85%,black)]',
  // understated: secondary and tertiary actions. The hover/active tint is
  // ink-mixed rather than a fixed panel color, so it reads on paper AND
  // vellum instead of disappearing on whichever one matches --ws-panel.
  quiet:
    'border border-ws-line bg-transparent text-ws-ink hover:border-ink-mid hover:bg-[color-mix(in_srgb,var(--ink)_6%,transparent)] active:bg-[color-mix(in_srgb,var(--ink)_11%,transparent)]',
  // madder: destructive actions
  danger:
    'border border-madder bg-madder text-ws-bg hover:opacity-90 active:opacity-100 active:bg-[color-mix(in_srgb,var(--madder)_85%,black)]',
}

const SIZE_CLASS: Record<ButtonSize, string> = {
  // preserves the pre-bump footprint: dense rows, toolbars, table actions
  sm: 'px-3.5 py-2 text-sm',
  // the default control: 36px tall, matches <Input>/<Textarea>
  default: 'h-9 px-4 text-sm',
  // one emphasized action per screen
  cta: 'h-10 px-5 text-sm',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
}

export function Button({
  variant = 'primary',
  size = 'default',
  className = '',
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-[var(--radius-sm)] font-medium transition-[background-color,border-color,opacity,transform] duration-[var(--dur-fast)] active:translate-y-px disabled:pointer-events-none disabled:opacity-50 ${SIZE_CLASS[size]} ${VARIANT_CLASS[variant]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}
