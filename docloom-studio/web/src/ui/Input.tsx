import type { InputHTMLAttributes, TextareaHTMLAttributes } from 'react'

// shared so <Input>/<Textarea> and any composite control built from them
// (e.g. an input with a trailing icon) stay pixel-identical
export const FIELD_CLASS =
  'w-full rounded-[var(--radius-sm)] border border-border-control bg-transparent px-3 py-2 text-sm text-ws-ink placeholder:text-ws-muted transition-colors duration-[var(--dur-fast)] focus:border-woad focus:outline-none disabled:cursor-not-allowed disabled:opacity-50'

export function Input({ className = '', ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${FIELD_CLASS} ${className}`} {...rest} />
}

export function Textarea({ className = '', ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`${FIELD_CLASS} resize-y ${className}`} {...rest} />
}
