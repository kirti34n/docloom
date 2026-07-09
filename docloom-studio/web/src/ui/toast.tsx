/** Lightweight imperative toasts. Call `toast.error(msg)` from anywhere;
 *  mount <Toaster/> once at the app shell. No context/provider plumbing. */
import { useEffect, useState } from 'react'
import { X } from 'lucide-react'

export type ToastKind = 'error' | 'info' | 'success'
export interface Toast {
  id: number
  kind: ToastKind
  message: string
}

let counter = 0
let items: Toast[] = []
const listeners = new Set<(t: Toast[]) => void>()

function emit() {
  for (const l of listeners) l(items)
}

export function dismiss(id: number) {
  items = items.filter((t) => t.id !== id)
  emit()
}

function push(kind: ToastKind, message: string, ttl: number) {
  const id = ++counter
  items = [...items, { id, kind, message }]
  emit()
  if (ttl) setTimeout(() => dismiss(id), ttl)
}

export const toast = {
  error: (m: string) => push('error', m, 9000),
  info: (m: string) => push('info', m, 5000),
  success: (m: string) => push('success', m, 4000),
}

const DOT: Record<ToastKind, string> = {
  error: 'bg-ws-danger',
  info: 'bg-ws-accent',
  success: 'bg-ws-ok',
}

export function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>(items)
  useEffect(() => {
    const l = (t: Toast[]) => setToasts(t)
    listeners.add(l)
    return () => {
      listeners.delete(l)
    }
  }, [])
  if (toasts.length === 0) return null
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className="pointer-events-auto flex items-start gap-2.5 rounded-lg border border-ws-line bg-ws-panel px-3.5 py-2.5 text-[13px] text-ws-ink shadow-[var(--shadow-panel)]"
        >
          <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${DOT[t.kind]}`} />
          <span className="min-w-0 flex-1 break-words leading-snug">{t.message}</span>
          <button
            onClick={() => dismiss(t.id)}
            aria-label="Dismiss"
            className="mt-0.5 text-ws-muted transition-colors hover:text-ws-ink"
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}
