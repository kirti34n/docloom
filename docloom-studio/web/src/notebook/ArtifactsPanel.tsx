import { BarChart3, FileSpreadsheet, FileText, Presentation, Share2, Sparkles } from 'lucide-react'
import { useState } from 'react'

interface ArtifactSummary {
  id: string
  kind: string
  title: string
  version: number
}

const KINDS = [
  { kind: 'deck', label: 'Presentation', icon: Presentation, hint: 'A 6-slide deck on…' },
  { kind: 'doc', label: 'Document', icon: FileText, hint: 'A report on…' },
  { kind: 'sheet', label: 'Spreadsheet', icon: FileSpreadsheet, hint: 'A budget for…' },
  { kind: 'diagram', label: 'Diagram', icon: Share2, hint: 'Architecture of a web app…' },
  { kind: 'infographic', label: 'Infographic', icon: BarChart3, hint: 'The 4 pillars of async work…' },
] as const

const KIND_ICON: Record<string, typeof Presentation> = {
  deck: Presentation,
  doc: FileText,
  sheet: FileSpreadsheet,
  diagram: Share2,
  infographic: BarChart3,
}

export function ArtifactsPanel({
  artifacts,
  onGenerate,
  onOpen,
}: {
  artifacts: ArtifactSummary[]
  onGenerate: (kind: string, prompt: string) => void
  onOpen: (a: ArtifactSummary) => void
}) {
  const [kind, setKind] = useState<string>('deck')
  const [prompt, setPrompt] = useState('')
  const active = KINDS.find((k) => k.kind === kind)!

  return (
    <div className="flex h-full w-80 shrink-0 flex-col border-l border-ws-line bg-ws-panel">
      <div className="px-4 py-3">
        <h2 className="text-[13px] font-semibold">Create</h2>
      </div>

      <div className="px-4">
        <div className="flex gap-1">
          {KINDS.map((k) => {
            const Icon = k.icon
            return (
              <button
                key={k.kind}
                onClick={() => setKind(k.kind)}
                className={`flex flex-1 flex-col items-center gap-1 rounded-lg border py-2 text-[11px] ${
                  kind === k.kind ? 'border-ws-accent text-ws-ink' : 'border-ws-line text-ws-muted'
                }`}
              >
                <Icon size={16} />
                {k.label}
              </button>
            )
          })}
        </div>

        <div className="mt-3 rounded-xl border border-ws-line p-3">
          <label className="flex items-center gap-1.5 text-[12px] font-medium">
            <Sparkles size={13} className="text-ws-accent" /> {active.label}
          </label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            placeholder={active.hint}
            className="mt-2 w-full resize-none rounded-lg border border-ws-line bg-ws-bg px-2.5 py-2 text-[13px] outline-none focus:border-ws-accent"
          />
          <button
            onClick={() => prompt.trim() && onGenerate(kind, prompt)}
            disabled={!prompt.trim()}
            className="mt-2 w-full rounded-lg bg-ws-ink py-2 text-[12.5px] font-medium text-white disabled:opacity-40"
          >
            Generate {active.label.toLowerCase()}
          </button>
          <p className="mt-2 text-[11px] text-ws-muted">Grounds in your enabled sources and cites them.</p>
        </div>
      </div>

      <div className="mt-5 flex-1 overflow-auto px-4 pb-4">
        {artifacts.length > 0 && (
          <>
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-ws-muted">Artifacts</h3>
            <ul className="mt-2 space-y-2">
              {artifacts.map((a) => {
                const Icon = KIND_ICON[a.kind] ?? FileText
                return (
                  <li key={a.id}>
                    <button
                      onClick={() => onOpen(a)}
                      className="flex w-full items-center gap-2.5 rounded-lg border border-ws-line px-3 py-2.5 text-left hover:border-ws-accent"
                    >
                      <Icon size={15} className="text-ws-accent" />
                      <span className="flex-1 truncate text-[13px]">{a.title || 'Untitled'}</span>
                      <span className="text-[11px] text-ws-muted">v{a.version}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}
