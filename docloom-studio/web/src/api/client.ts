/** Minimal API client: JSON fetch + SSE job streams. */

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new ApiError(res.status, text || res.statusText)
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
  if (!res.ok || !res.body) throw new ApiError(res.status, await res.text())
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

/** Subscribe to a job's SSE stream. Returns an unsubscribe function. */
export function jobEvents(
  jobId: string,
  onEvent: (e: JobEvent) => void,
  onEnd?: () => void,
): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/events`)
  source.onmessage = (msg) => {
    const event = JSON.parse(msg.data) as JobEvent
    onEvent(event)
    if (event.stage === 'job' && event.status !== 'running') {
      source.close()
      onEnd?.()
    }
  }
  source.onerror = () => {
    source.close()
    onEnd?.()
  }
  return () => source.close()
}
