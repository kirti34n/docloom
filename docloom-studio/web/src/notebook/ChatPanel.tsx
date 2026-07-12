import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Send } from 'lucide-react'
import { api, streamNdjson } from '../api/client'
import { Empty, Eyebrow, SourceMark } from '../ui'

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

// ---- a small hand-rolled markdown subset: paragraphs, headings, lists, code
// fences, bold, italic, inline code. [n] citations are split out first (see
// Inline below) so a citation is never swallowed by surrounding **bold**. ---

type Block =
  | { kind: 'p'; text: string }
  | { kind: 'h'; level: number; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'code'; text: string }

function parseBlocks(src: string): Block[] {
  const lines = src.split('\n')
  const blocks: Block[] = []
  let para: string[] = []
  let list: { ordered: boolean; items: string[] } | null = null

  const flushPara = () => {
    if (para.length) blocks.push({ kind: 'p', text: para.join('\n') })
    para = []
  }
  const flushList = () => {
    if (list) blocks.push({ kind: 'list', ordered: list.ordered, items: list.items })
    list = null
  }

  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (/^```/.test(line)) {
      flushPara()
      flushList()
      const code: string[] = []
      i++
      while (i < lines.length && !/^```/.test(lines[i])) {
        code.push(lines[i])
        i++
      }
      i++ // consume the closing fence, or the end of a still-streaming block
      blocks.push({ kind: 'code', text: code.join('\n') })
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.+)/)
    if (heading) {
      flushPara()
      flushList()
      blocks.push({ kind: 'h', level: heading[1].length, text: heading[2] })
      i++
      continue
    }
    const ul = line.match(/^\s*[-*+]\s+(.+)/)
    const ol = line.match(/^\s*\d+[.)]\s+(.+)/)
    if (ul || ol) {
      flushPara()
      const ordered = !!ol
      const item = (ul ?? ol)![1]
      if (list && list.ordered === ordered) list.items.push(item)
      else {
        flushList()
        list = { ordered, items: [item] }
      }
      i++
      continue
    }
    if (line.trim() === '') {
      flushPara()
      flushList()
      i++
      continue
    }
    para.push(line)
    i++
  }
  flushPara()
  flushList()
  return blocks
}

// bold/italic/code only: [n] citations are handled a level up, in Inline, so
// a citation split never lands inside one of these tokens.
const FORMAT_RE = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g

function Formatted({ text }: { text: string }) {
  return (
    <>
      {text.split(FORMAT_RE).map((part, i) => {
        if (!part) return null
        if (i % 2 === 0) return <span key={i}>{part}</span> // plain text between tokens
        if (part.startsWith('**')) return <strong key={i}>{part.slice(2, -2)}</strong>
        if (part.startsWith('`')) {
          return (
            <code key={i} className="rounded-[var(--radius-sm)] bg-ws-bg px-1 py-px font-mono text-[0.9em]">
              {part.slice(1, -1)}
            </code>
          )
        }
        return <em key={i}>{part.slice(1, -1)}</em>
      })}
    </>
  )
}

/** [n] citations, split out before bold/italic is applied so a citation next
 *  to a formatting marker is never absorbed by it. Renders as SourceMark: the
 *  numeral shown is the source's stable warp-rail index, not the message-
 *  local evidence ordinal, so a source reads as the same number everywhere. */
function Inline({
  text,
  evidence,
  sourceIndex,
  onCite,
}: {
  text: string
  evidence: Map<number, Evidence>
  sourceIndex: Map<string, number>
  onCite: (e: Evidence) => void
}) {
  return (
    <>
      {text.split(/(\[\d+\])/g).map((part, i) => {
        if (!part) return null
        if (i % 2 === 1) {
          const e = evidence.get(Number(part.slice(1, -1)))
          if (!e) return <span key={i}>{part}</span>
          return (
            <SourceMark key={i} n={sourceIndex.get(e.source_id) ?? e.n} sourceId={e.source_id} onOpen={() => onCite(e)} />
          )
        }
        return <Formatted key={i} text={part} />
      })}
    </>
  )
}

function Markdown({
  text,
  evidence,
  sourceIndex,
  onCite,
}: {
  text: string
  evidence?: Evidence[]
  sourceIndex: Map<string, number>
  onCite: (e: Evidence) => void
}) {
  const evidenceMap = useMemo(() => new Map((evidence ?? []).map((e) => [e.n, e])), [evidence])
  const blocks = useMemo(() => parseBlocks(text), [text])
  return (
    <>
      {blocks.map((b, i) => {
        if (b.kind === 'code') {
          return (
            <pre
              key={i}
              className="my-1.5 overflow-x-auto rounded-[var(--radius-sm)] bg-ws-bg px-2.5 py-2 font-mono text-[12.5px] leading-normal first:mt-0 last:mb-0"
            >
              <code>{b.text}</code>
            </pre>
          )
        }
        if (b.kind === 'h') {
          return (
            <p key={i} className={`mb-1 mt-3 font-semibold first:mt-0 ${b.level <= 2 ? 'text-[15px]' : 'text-[13.5px]'}`}>
              <Inline text={b.text} evidence={evidenceMap} sourceIndex={sourceIndex} onCite={onCite} />
            </p>
          )
        }
        if (b.kind === 'list') {
          const Tag = b.ordered ? 'ol' : 'ul'
          return (
            <Tag key={i} className={`my-1.5 space-y-1 pl-5 first:mt-0 last:mb-0 ${b.ordered ? 'list-decimal' : 'list-disc'}`}>
              {b.items.map((item, j) => (
                <li key={j}>
                  <Inline text={item} evidence={evidenceMap} sourceIndex={sourceIndex} onCite={onCite} />
                </li>
              ))}
            </Tag>
          )
        }
        return (
          <p key={i} className="my-1.5 first:mt-0 last:mb-0">
            {b.text.split('\n').map((line, j, arr) => (
              <span key={j}>
                <Inline text={line} evidence={evidenceMap} sourceIndex={sourceIndex} onCite={onCite} />
                {j < arr.length - 1 && <br />}
              </span>
            ))}
          </p>
        )
      })}
    </>
  )
}

const ASK_INVITE = 'Ask about your sources. Answers cite the evidence they came from.'

/** The chat empty state: NotebookLM-style suggested questions the sources can
 *  actually answer, one click to ask. Degrades to the plain invitation if the
 *  notebook has no sources yet to suggest from, or the call fails. */
function SuggestedQuestions({ notebookId, onAsk }: { notebookId: string; onAsk: (q: string) => void }) {
  const [questions, setQuestions] = useState<string[]>([])

  useEffect(() => {
    let live = true
    api
      .get<{ questions: string[] }>(`/api/notebooks/${notebookId}/suggested-questions`)
      .then((r) => live && setQuestions(r.questions ?? []))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [notebookId])

  if (questions.length === 0) {
    return (
      <div className="mt-16">
        <Empty title="Ask your sources" body={ASK_INVITE} />
      </div>
    )
  }
  return (
    <div className="mx-auto mt-16 max-w-md">
      <Eyebrow className="text-center">Ask your sources</Eyebrow>
      <p className="mt-1.5 text-center text-[13px] text-ws-muted">{ASK_INVITE}</p>
      <div className="mt-5 flex flex-col gap-2">
        {questions.map((q) => (
          <button
            key={q}
            onClick={() => onAsk(q)}
            className="rounded-[var(--radius)] border border-ws-line bg-ws-panel px-3.5 py-2.5 text-left text-[13px] text-ws-ink transition-colors hover:border-woad"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}

export function ChatPanel({
  notebookId,
  sourceIndex,
  onCite,
}: {
  notebookId: string
  sourceIndex: Map<string, number>
  onCite: (e: Evidence) => void
}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const scroll = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // load the persisted conversation so it survives reload / navigate-back
    setHistoryLoaded(false)
    api
      .get<Turn[]>(`/api/notebooks/${notebookId}/messages`)
      .then(setTurns)
      .catch(() => {})
      .finally(() => setHistoryLoaded(true))
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

  const send = (text?: string) => {
    const msg = (text ?? input).trim()
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
        {!historyLoaded ? null : turns.length === 0 ? (
          <SuggestedQuestions notebookId={notebookId} onAsk={send} />
        ) : (
          turns.map((turn, i) => (
            <div key={i} className={turn.role === 'user' ? 'flex justify-end' : ''}>
              <div
                className={`max-w-[80%] rounded-[var(--radius)] px-4 py-2.5 text-[14px] leading-relaxed ${
                  turn.role === 'user' ? 'bg-ws-ink text-ws-bg' : 'bg-ws-panel text-ws-ink'
                }`}
              >
                {turn.role === 'user' ? (
                  turn.text
                ) : turn.error ? (
                  <div className="text-[13px]">
                    <span className="text-madder">Couldn't get an answer: {turn.error}</span>
                    <button
                      onClick={() => retry(turns[i - 1]?.text ?? '')}
                      className="ml-2 rounded-[var(--radius-sm)] border border-ws-line px-2 py-0.5 text-[12px] text-ws-ink hover:bg-ws-bg"
                    >
                      Retry
                    </button>
                  </div>
                ) : !turn.text && busy ? (
                  <Loader2 size={15} className="animate-spin text-ws-muted" />
                ) : (
                  <Markdown text={turn.text} evidence={turn.evidence} sourceIndex={sourceIndex} onCite={onCite} />
                )}
              </div>
            </div>
          ))
        )}
      </div>
      <div className="border-t border-ws-line p-3">
        <div className="ds-focusable flex items-end gap-2 rounded-[var(--radius)] border border-ws-line bg-ws-panel px-3 py-2">
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
          <button
            onClick={() => send()}
            disabled={busy || !input.trim()}
            aria-label="Send message"
            className="rounded-[var(--radius-sm)] bg-ws-ink p-2 text-ws-bg disabled:opacity-40"
          >
            <Send size={15} />
          </button>
        </div>
      </div>
    </div>
  )
}
