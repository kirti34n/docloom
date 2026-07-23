import {
  AlertCircle, BarChart3, Clock, FileSpreadsheet, FileText, GraduationCap, HelpCircle,
  Loader2, Mic, Network, Newspaper, PanelRightClose, Presentation, Share2, Sparkles,
} from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { Button, Eyebrow, IconButton } from '../ui'
import { toast } from '../ui/toast'

// NotebookLM-style one-click guides: each is a grounded generation with a
// preset prompt (docs) or an architecture/mind-map diagram.
const GUIDES = [
  { key: 'study', label: 'Study guide', icon: GraduationCap, kind: 'doc',
    prompt: 'Create a study guide from the sources: a short overview, the key concepts (each with a one-line explanation), review questions with answers, and a glossary of terms. Ground every point in the sources and cite them.' },
  { key: 'briefing', label: 'Briefing', icon: Newspaper, kind: 'doc',
    prompt: 'Write an executive briefing from the sources: a 3-4 sentence summary, the key themes as sections with supporting detail, notable facts or quotes, and open questions. Cite the sources.' },
  { key: 'faq', label: 'FAQ', icon: HelpCircle, kind: 'doc',
    prompt: 'Write an FAQ from the sources: 8-12 questions a reader would realistically ask, each with a concise answer grounded in and citing the sources.' },
  { key: 'timeline', label: 'Timeline', icon: Clock, kind: 'doc',
    prompt: 'Build a chronological timeline from the sources: events in order, each with a date and a one-line description, then a short "what it means" summary. Cite the sources.' },
  { key: 'mindmap', label: 'Mind map', icon: Network, kind: 'diagram',
    prompt: 'Create a mind map of the sources: a central topic connected to the main themes, and each theme connected to its key points. Keep the labels short.' },
] as const

// Per-kind starter prompts. Clicking one fills the prompt box (it does not
// generate immediately) so the user has a strong, well-structured base to
// edit into their own -- the fastest path to a good prompt. Square-bracketed
// slots ([system], [product]) are the parts a user swaps in. Deck/doc
// starters deliberately mention "as a diagram" so the newly-enabled inline
// architecture diagrams actually get used.
const STARTERS: Record<string, string[]> = {
  deck: [
    'A technical overview of [system]: what problem it solves, the architecture as a diagram, the main components, and the key design trade-offs. Cite the sources.',
    'A 10-slide investor pitch for [product]: the problem, the solution, how it works, market size as a chart, traction, business model, and the ask. Cite the sources.',
  ],
  doc: [
    'A design document for [system]: goals, the proposed architecture with a diagram, the data flow, alternatives considered, and risks. Cite the sources.',
    'A market analysis report: an executive summary, market size and growth as a chart, the competitive landscape as a table, and clear recommendations. Cite the sources.',
  ],
  sheet: [
    'A 12-month budget: monthly revenue and expense line items, category subtotals, and a running balance, using formulas.',
    'A project tracker: tasks with owner, start and end dates, status, and percent complete.',
  ],
  diagram: [
    'The architecture of a web application: client, CDN, API gateway, services, database, cache, and a message queue, with the request flow labeled.',
    'A CI/CD pipeline from commit to production: build, test, staging, approval, and deploy, showing what triggers each stage.',
  ],
  infographic: [
    'The 4 pillars of [topic] as a grid, each with a short label and a one-line description.',
    'A 5-step [process] as steps, each step with a punchy title and one supporting line.',
  ],
  podcast: [
    'A 2-host, 5-minute audio overview of the sources: the main takeaway, two supporting points, and a closing thought.',
    'A solo deep-dive explaining [topic] from the sources for a smart non-expert, in a warm, clear voice.',
  ],
}

interface ArtifactSummary {
  id: string
  kind: string
  title: string
  version: number
  status: 'building' | 'ready' | 'failed'
}

const KINDS = [
  { kind: 'deck', label: 'Presentation', icon: Presentation, hint: 'A 6-slide deck on…' },
  { kind: 'doc', label: 'Document', icon: FileText, hint: 'A report on…' },
  { kind: 'sheet', label: 'Spreadsheet', icon: FileSpreadsheet, hint: 'A budget for…' },
  { kind: 'diagram', label: 'Diagram', icon: Share2, hint: 'Architecture of a web app…' },
  { kind: 'infographic', label: 'Infographic', icon: BarChart3, hint: 'The 4 pillars of async work…' },
  { kind: 'podcast', label: 'Podcast', icon: Mic, hint: 'A 2-host audio overview of…' },
] as const

const KIND_ICON: Record<string, typeof Presentation> = {
  deck: Presentation,
  doc: FileText,
  sheet: FileSpreadsheet,
  diagram: Share2,
  infographic: BarChart3,
  podcast: Mic,
}

export function ArtifactsPanel({
  artifacts,
  onGenerate,
  onOpen,
  onDeleted,
  onCollapse,
}: {
  artifacts: ArtifactSummary[]
  onGenerate: (kind: string, prompt: string) => void
  onOpen: (a: ArtifactSummary) => void
  onDeleted: (id: string) => void
  onCollapse?: () => void
}) {
  const [kind, setKind] = useState<string>('deck')
  const [prompt, setPrompt] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const active = KINDS.find((k) => k.kind === kind)!

  const deleteArtifact = async (a: ArtifactSummary) => {
    setDeletingId(a.id)
    try {
      await api.del(`/api/artifacts/${a.id}`)
      onDeleted(a.id)
      toast.success('Artifact deleted')
    } catch (e) {
      toast.error(`Couldn't delete artifact: ${e instanceof Error ? e.message : e}`)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="flex h-full w-96 shrink-0 flex-col border-l border-ws-line bg-ws-panel">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-sm font-semibold">Create</h2>
        {onCollapse && (
          <IconButton label="Collapse create panel" onClick={onCollapse}>
            <PanelRightClose size={15} />
          </IconButton>
        )}
      </div>

      <div className="px-4">
        <div className="grid grid-cols-3 gap-1.5">
          {KINDS.map((k) => {
            const Icon = k.icon
            const selected = kind === k.kind
            return (
              <button
                key={k.kind}
                onClick={() => setKind(k.kind)}
                aria-pressed={selected}
                className={`ds-card flex flex-col items-center gap-1.5 py-2.5 text-2xs font-medium ${
                  selected
                    ? 'border-ws-accent text-ws-ink ring-1 ring-ws-accent'
                    : 'ds-card-hover text-ws-muted'
                }`}
              >
                <Icon size={16} className={selected ? 'text-ws-accent' : ''} />
                {k.label}
              </button>
            )
          })}
        </div>

        <div className="ds-card ds-focusable mt-3 p-3">
          <label className="flex items-center gap-1.5 text-xs font-medium">
            <Sparkles size={13} className="text-ws-accent" /> {active.label}
          </label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            placeholder={active.hint}
            className="mt-2 w-full resize-none rounded-lg border border-ws-line bg-ws-bg px-2.5 py-2 text-sm outline-none focus:border-ws-accent"
          />
          <Button
            variant="accent"
            onClick={() => prompt.trim() && onGenerate(kind, prompt)}
            disabled={!prompt.trim()}
            className="mt-2 w-full"
          >
            Generate {active.label.toLowerCase()}
          </Button>
          <p className="mt-2 text-2xs text-ws-muted">Grounds in your enabled sources and cites them.</p>

          {(STARTERS[kind] ?? []).length > 0 && (
            <div className="mt-2.5 border-t border-ws-line pt-2.5">
              <Eyebrow>Starter prompts</Eyebrow>
              <div className="mt-1.5 flex flex-col gap-1">
                {(STARTERS[kind] ?? []).map((s, i) => (
                  <button
                    key={i}
                    onClick={() => setPrompt(s)}
                    title="Use this as a starting point, then edit it"
                    className="ds-card ds-card-hover line-clamp-2 w-full px-2.5 py-1.5 text-left text-xs text-ws-ink"
                  >
                    {s}
                  </button>
                ))}
              </div>
              <p className="mt-1 text-2xs text-ws-muted">Click to fill the box, then tweak it.</p>
            </div>
          )}
        </div>

        <div className="mt-4">
          <Eyebrow>Guides</Eyebrow>
          <div className="mt-2 grid grid-cols-2 gap-1.5">
            {GUIDES.map((g) => {
              const Icon = g.icon
              return (
                <button
                  key={g.key}
                  onClick={() => onGenerate(g.kind, g.prompt)}
                  className="ds-card ds-card-hover flex items-center gap-1.5 px-2.5 py-2 text-xs font-medium text-ws-muted"
                >
                  <Icon size={14} className="text-ws-accent" /> {g.label}
                </button>
              )
            })}
          </div>
          <p className="mt-1.5 text-2xs text-ws-muted">One-click, grounded in your sources.</p>
        </div>
      </div>

      <div className="mt-5 flex-1 overflow-auto px-4 pb-4">
        {artifacts.length > 0 && (
          <>
            <Eyebrow>Artifacts</Eyebrow>
            <ul className="stagger mt-2 space-y-2">
              {artifacts.map((a, i) => {
                const Icon = KIND_ICON[a.kind] ?? FileText
                return (
                  <li key={a.id} style={{ ['--i' as string]: i }}>
                    {a.status === 'ready' ? (
                      <button
                        onClick={() => onOpen(a)}
                        className="ds-card ds-card-hover flex w-full items-center gap-2.5 px-3 py-2.5 text-left"
                      >
                        <Icon size={15} className="shrink-0 text-woad" />
                        <span className="min-w-0 flex-1 truncate text-sm">{a.title || 'Untitled'}</span>
                        <span className="shrink-0 text-2xs text-ws-muted">v{a.version}</span>
                      </button>
                    ) : (
                      <div
                        className={`ds-card flex w-full items-center gap-2.5 px-3 py-2.5 ${
                          a.status === 'failed' ? 'border-madder/40' : ''
                        }`}
                      >
                        {a.status === 'building' ? (
                          <Loader2 size={15} className="shrink-0 animate-spin text-ws-muted" />
                        ) : (
                          <AlertCircle size={15} className="shrink-0 text-madder" />
                        )}
                        <span className="min-w-0 flex-1 truncate text-sm text-ws-muted">
                          {a.status === 'building' ? 'Generating…' : a.title || 'Generation failed'}
                        </span>
                        {a.status === 'failed' && (
                          <button
                            onClick={() => deleteArtifact(a)}
                            disabled={deletingId === a.id}
                            className="shrink-0 rounded-[var(--radius-sm)] border border-ws-line px-2 py-1 text-2xs text-madder hover:bg-ws-bg disabled:opacity-50"
                          >
                            {deletingId === a.id ? 'Deleting…' : 'Delete'}
                          </button>
                        )}
                      </div>
                    )}
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
