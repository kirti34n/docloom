import { useEffect, useRef, useState } from 'react'
import { Loader2, Send } from 'lucide-react'
import { api, streamNdjson } from '../api/client'

export interface Evidence {
  n: number
  source_id: string
  source_title: string
  page?: number | null
  text: string
}
interface Turn {
  role: 'user' | 'assistant'
  text: string
  evidence?: Evidence[]
  error?: string
}

/** Render an answer, turning [n] markers into clickable citation chips that
 *  open the cited source passage in the reader. */
function Answer({
  text,
  evidence,
  onCite,
}: {
  text: string
  evidence?: Evidence[]
  onCite: (e: Evidence) => void
}) {
  const map = new Map((evidence ?? []).map((e) => [e.n, e]))
  const parts = text.split(/(\[\d+\])/g)
  return (
    <>
      {parts.map((p, i) => {
        const m = p.match(/^\[(\d+)\]$/)
        if (m) {
          const e = map.get(Number(m[1]))
          if (!e) return <span key={i}>{p}</span>
          return (
            <button
              key={i}
              type="button"
              className="chat-cite"
              title={`${e.source_title}${e.page ? `, p.${e.page}` : ''}: ${e.text}`}
              onClick={() => onCite(e)}
            >
              {m[1]}
            </button>
          )
        }
        return <span key={i}>{p}</span>
      })}
    </>
  )
}

export function ChatPanel({
  notebookId,
  onCite,
}: {
  notebookId: string
  onCite: (e: Evidence) => void
}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const scroll = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // load the persisted conversation so it survives reload / navigate-back
    api.get<Turn[]>(`/api/notebooks/${notebookId}/messages`).then(setTurns).catch(() => {})
  }, [notebookId])

  const runChat = async (msg: string) => {
    setBusy(true)
    try {
      await streamNdjson(`/api/notebooks/${notebookId}/chat`, { message: msg }, (obj) => {
        setTurns((t) => {
          const next = [...t]
          const last = next[next.length - 1]
          if (obj.type === 'evidence') last.evidence = obj.items as Evidence[]
          else if (obj.type === 'token') last.text += obj.text as string
          return next
        })
        scroll.current?.scrollTo({ top: scroll.current.scrollHeight })
      })
    } catch (e) {
      setTurns((t) => {
        const next = [...t]
        const last = next[next.length - 1]
        if (last && last.role === 'assistant') {
          last.error = e instanceof Error ? e.message : String(e)
        }
        return next
      })
    } finally {
      setBusy(false)
    }
  }

  const send = () => {
    const msg = input.trim()
    if (!msg || busy) return
    setInput('')
    setTurns((t) => [...t, { role: 'user', text: msg }, { role: 'assistant', text: '' }])
    runChat(msg)
  }

  const retry = (msg: string) => {
    if (!msg || busy) return
    setTurns((t) => {
      const next = [...t]
      next[next.length - 1] = { role: 'assistant', text: '' }
      return next
    })
    runChat(msg)
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div ref={scroll} className="flex-1 space-y-4 overflow-auto px-6 py-5">
        {turns.length === 0 ? (
          <div className="mt-20 text-center text-[13px] text-ws-muted">
            Ask about your sources. Answers cite the evidence they came from.
          </div>
        ) : (
          turns.map((turn, i) => (
            <div key={i} className={turn.role === 'user' ? 'flex justify-end' : ''}>
              <div
                className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-[14px] leading-relaxed ${
                  turn.role === 'user'
                    ? 'bg-ws-ink text-white'
                    : 'bg-ws-panel text-ws-ink'
                }`}
              >
                {turn.role === 'user' ? (
                  turn.text
                ) : turn.error ? (
                  <div className="text-[13px]">
                    <span className="text-ws-danger">Couldn’t get an answer: {turn.error}</span>
                    <button
                      onClick={() => retry(turns[i - 1]?.text ?? '')}
                      className="ml-2 rounded-md border border-ws-line px-2 py-0.5 text-[12px] text-ws-ink hover:bg-ws-bg"
                    >
                      Retry
                    </button>
                  </div>
                ) : !turn.text && busy ? (
                  <Loader2 size={15} className="animate-spin text-ws-muted" />
                ) : (
                  <Answer text={turn.text} evidence={turn.evidence} onCite={onCite} />
                )}
              </div>
            </div>
          ))
        )}
      </div>
      <div className="border-t border-ws-line p-3">
        <div className="flex items-end gap-2 rounded-xl border border-ws-line bg-ws-panel px-3 py-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            rows={1}
            placeholder="Ask your sources…"
            className="max-h-32 flex-1 resize-none bg-transparent text-[14px] outline-none"
          />
          <button onClick={send} disabled={busy || !input.trim()}
            aria-label="Send message"
            className="rounded-lg bg-ws-ink p-2 text-white disabled:opacity-40">
            <Send size={15} />
          </button>
        </div>
      </div>
    </div>
  )
}
