import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { FileStack, PanelLeftOpen, PanelRightOpen, Sparkles } from 'lucide-react'
import { api } from '../api/client'
import { BuildView } from '../deck/BuildView'
import { useThemes, themeByName } from '../deck/useThemes'
import { SourcesPanel, type Source } from '../notebook/SourcesPanel'
import { ChatPanel } from '../notebook/ChatPanel'
import { ArtifactsPanel } from '../notebook/ArtifactsPanel'
import { SourceReader } from '../notebook/SourceReader'
import { IconButton } from '../ui'

interface ArtifactSummary {
  id: string
  kind: string
  title: string
  version: number
  status: 'building' | 'ready' | 'failed'
  updated: number
}
interface NotebookDetail {
  id: string
  name: string
  artifacts: ArtifactSummary[]
}

// Below this viewport width, sources + a full-size create panel would squeeze
// chat under ~500px even on a "big" 1366px laptop -- so a first-time visit to
// a notebook on a narrower screen opens with the create/reader rail
// collapsed. Once the user has an explicit preference for this notebook
// (from clicking a rail or a collapse button), that always wins over this
// default.
const AUTO_COLLAPSE_WIDTH = 1400

function railPref(notebookId: string | undefined, side: 'left' | 'right'): boolean | null {
  if (!notebookId) return null
  try {
    const raw = localStorage.getItem(`docloom.workspace.${notebookId}.${side}Collapsed`)
    return raw === '1' ? true : raw === '0' ? false : null
  } catch {
    return null
  }
}

function setRailPref(notebookId: string | undefined, side: 'left' | 'right', collapsed: boolean) {
  if (!notebookId) return
  try {
    localStorage.setItem(`docloom.workspace.${notebookId}.${side}Collapsed`, collapsed ? '1' : '0')
  } catch {
    // storage unavailable (private mode, quota) -- collapse state just won't persist
  }
}

/** A slim icon rail standing in for a collapsed side panel. */
function SideRail({
  label,
  icon: Icon,
  count,
  side,
  onExpand,
}: {
  label: string
  icon: typeof FileStack
  count?: number
  side: 'left' | 'right'
  onExpand: () => void
}) {
  return (
    <div
      className={`flex h-full w-11 shrink-0 flex-col items-center gap-2 bg-ws-panel py-3 ${
        side === 'left' ? 'border-r border-ws-line' : 'border-l border-ws-line'
      }`}
    >
      <IconButton label={`Show ${label.toLowerCase()}`} onClick={onExpand}>
        {side === 'left' ? <PanelLeftOpen size={16} /> : <PanelRightOpen size={16} />}
      </IconButton>
      <Icon size={15} className="mt-1 text-ws-muted" />
      {typeof count === 'number' && count > 0 && (
        <span className="rounded-full bg-ws-bg px-1.5 text-2xs text-ws-muted">{count}</span>
      )}
      <span
        className="mt-auto text-2xs tracking-wide text-ws-muted"
        style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}
      >
        {label}
      </span>
    </div>
  )
}

export function NotebookWorkspace() {
  const { notebookId } = useParams()
  const navigate = useNavigate()
  const [notebook, setNotebook] = useState<NotebookDetail | null>(null)
  const [job, setJob] = useState<{ jobId: string; kind: string; title: string } | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [reader, setReader] = useState<{ sourceId: string; highlight?: string } | null>(null)
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const themes = useThemes()

  // the warp: a source's position in this list is its stable index (01, 02,
  // …), the same number a citation shows anywhere it references that source
  const sourceIndex = useMemo(() => {
    const m = new Map<string, number>()
    sources.forEach((s, i) => m.set(s.id, i + 1))
    return m
  }, [sources])

  const load = () => api.get<NotebookDetail>(`/api/notebooks/${notebookId}`).then(setNotebook)
  useEffect(() => {
    load()
  }, [notebookId])

  // resolve collapse state per-notebook: an explicit stored choice always
  // wins; otherwise default the right rail closed on narrow viewports so
  // chat has room.
  useEffect(() => {
    const left = railPref(notebookId, 'left')
    const right = railPref(notebookId, 'right')
    const narrow = typeof window !== 'undefined' && window.innerWidth < AUTO_COLLAPSE_WIDTH
    setLeftCollapsed(left ?? false)
    setRightCollapsed(right ?? narrow)
  }, [notebookId])

  const toggleLeft = (collapsed: boolean) => {
    setLeftCollapsed(collapsed)
    setRailPref(notebookId, 'left', collapsed)
  }
  const toggleRight = (collapsed: boolean) => {
    setRightCollapsed(collapsed)
    setRailPref(notebookId, 'right', collapsed)
  }
  // opening a cited or clicked source should always surface it, even if the
  // right rail is currently collapsed
  const openReader = (r: { sourceId: string; highlight?: string }) => {
    setReader(r)
    toggleRight(false)
  }

  const generate = async (kind: string, prompt: string) => {
    const res = await api.post<{ job_id: string; artifact_id: string }>(
      `/api/notebooks/${notebookId}/artifacts`,
      { kind, prompt },
    )
    setJob({ jobId: res.job_id, kind, title: prompt })
  }

  if (job) {
    const theme = themeByName(themes, 'paper')
    if (!theme) return null
    return (
      <BuildView
        jobId={job.jobId}
        theme={theme}
        deckTitle={job.title}
        onDone={(artifactId) => navigate(`/n/${notebookId}/${job.kind}/${artifactId}`)}
      />
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-ws-line px-5 py-2.5">
        <button onClick={() => navigate('/')} className="text-xs text-ws-muted hover:text-ws-ink">
          ← Notebooks
        </button>
        <span className="font-display text-base font-semibold">{notebook?.name ?? 'Notebook'}</span>
      </div>
      <div className="flex min-h-0 flex-1">
        {notebookId && (
          leftCollapsed ? (
            <SideRail label="Sources" icon={FileStack} count={sources.length} side="left" onExpand={() => toggleLeft(false)} />
          ) : (
            <SourcesPanel
              notebookId={notebookId}
              activeSourceId={reader?.sourceId}
              onOpenSource={(sourceId) => openReader({ sourceId })}
              onSourcesChange={setSources}
              onCollapse={() => toggleLeft(true)}
            />
          )
        )}
        {notebookId && (
          <ChatPanel
            notebookId={notebookId}
            sourceIndex={sourceIndex}
            onCite={(e) => openReader({ sourceId: e.source_id, highlight: e.text })}
          />
        )}
        {notebookId && (
          rightCollapsed ? (
            <SideRail
              label={reader ? 'Reader' : 'Create'}
              icon={reader ? FileStack : Sparkles}
              count={reader ? undefined : notebook?.artifacts.length}
              side="right"
              onExpand={() => toggleRight(false)}
            />
          ) : reader ? (
            <SourceReader
              sourceId={reader.sourceId}
              highlight={reader.highlight}
              index={sourceIndex.get(reader.sourceId)}
              onClose={() => setReader(null)}
              onCollapse={() => toggleRight(true)}
            />
          ) : (
            <ArtifactsPanel
              artifacts={notebook?.artifacts ?? []}
              onGenerate={generate}
              onOpen={(a) => navigate(`/n/${notebookId}/${a.kind}/${a.id}`)}
              onDeleted={(id) =>
                setNotebook((nb) => (nb ? { ...nb, artifacts: nb.artifacts.filter((a) => a.id !== id) } : nb))
              }
              onCollapse={() => toggleRight(true)}
            />
          )
        )}
      </div>
    </div>
  )
}
