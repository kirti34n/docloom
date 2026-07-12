import type { ReactNode } from 'react'

/** A labeled form field: label, control, and one line of hint or error. */
export function Field({
  label,
  hint,
  error,
  htmlFor,
  children,
  className = '',
}: {
  label: string
  hint?: string
  error?: string
  htmlFor?: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={className}>
      <label htmlFor={htmlFor} className="block text-[12px] font-medium text-ws-muted">
        {label}
      </label>
      <div className="mt-1">{children}</div>
      {error ? (
        <p className="mt-1 text-[12px] text-madder">{error}</p>
      ) : hint ? (
        <p className="mt-1 text-[12px] text-ws-muted">{hint}</p>
      ) : null}
    </div>
  )
}
