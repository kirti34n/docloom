import type { ReactNode } from 'react'

/** An empty or failed state: an invitation, not a dead end. One heading, one
 *  line of body copy, and at most one action. */
export function Empty({
  title,
  body,
  action,
}: {
  title: string
  body: string
  action?: ReactNode
}) {
  return (
    <div className="rounded-[var(--radius)] border border-dashed border-ws-line px-8 py-12 text-center">
      <p className="font-display text-xl font-medium text-ws-ink">{title}</p>
      <p className="mx-auto mt-2 max-w-sm text-sm text-ws-muted">{body}</p>
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  )
}
