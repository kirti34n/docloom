/** Minimal API client: JSON fetch + SSE job streams. */

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** Called whenever any request 401s, so the auth layer can drop to login. */
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn
}

/** FastAPI's error body is {"detail": "message"} or, for a 422, {"detail":
 *  [{loc, msg, ...}, ...]}. Flatten either shape into one readable sentence
 *  so it can go straight on screen instead of raw JSON. */
function detailMessage(body: unknown): string | null {
  if (!body || typeof body !== 'object' || !('detail' in body)) return null
  const detail = (body as { detail: unknown }).detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => {
      if (d && typeof d === 'object') {
        const loc = Array.isArray((d as { loc?: unknown[] }).loc)
          ? (d as { loc: unknown[] }).loc.filter((p) => p !== 'body' && p !== 'query').join('.')
          : ''
        const msg = (d as { msg?: unknown }).msg ?? JSON.stringify(d)
        return loc ? `${loc}: ${msg}` : String(msg)
      }
      return String(d)
    })
    return parts.length ? parts.join(', ') : null
  }
  return null
}

async function errorMessage(res: Response): Promise<string> {
  const text = await res.text().catch(() => '')
  if (!text) return res.statusText
  try {
    return detailMessage(JSON.parse(text)) ?? text
  } catch {
    return text
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  if (!res.ok) {
    if (res.status === 401) onUnauthorized?.()
    throw new ApiError(res.status, await errorMessage(res))
  }
  return (await res.json()) as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}), ...init }),
  put: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body ?? {}), ...init }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body ?? {}) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

/** POST and consume an NDJSON stream (one JSON object per line). */
export async function streamNdjson(
  path: string,
  body: unknown,
  onLine: (obj: Record<string, unknown>) => void,
): Promise<void> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok || !res.body) {
    if (res.status === 401) onUnauthorized?.()
    throw new ApiError(res.status, await errorMessage(res))
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() ?? ''
    for (const line of lines) {
      if (line.trim()) onLine(JSON.parse(line))
    }
  }
  if (buf.trim()) onLine(JSON.parse(buf))
}

export interface JobEvent {
  stage: string
  status: string
  detail: string
  data: unknown
  t: number
}

const JOB_EVENTS_MAX_ERRORS = 5

/** Subscribe to a job's SSE stream. Returns an unsubscribe function.
 *
 *  EventSource reconnects on its own after a drop, and the server replays
 *  stored events from where the stream left off, so a transient error must
 *  not close the connection: that would defeat the built-in retry. Only give
 *  up (and surface a failure) after repeated consecutive errors, or right
 *  away if the browser itself already gave up (readyState CLOSED, e.g. the
 *  job id is gone for good). */
export function jobEvents(
  jobId: string,
  onEvent: (e: JobEvent) => void,
  onEnd?: () => void,
): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/events`)
  let errors = 0
  source.onmessage = (msg) => {
    errors = 0
    const event = JSON.parse(msg.data) as JobEvent
    onEvent(event)
    if (event.stage === 'job' && event.status !== 'running') {
      source.close()
      onEnd?.()
    }
  }
  source.onerror = () => {
    errors += 1
    if (source.readyState === EventSource.CLOSED || errors >= JOB_EVENTS_MAX_ERRORS) {
      source.close()
      onEnd?.()
    }
  }
  return () => source.close()
}
