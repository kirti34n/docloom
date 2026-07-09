import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { api } from '../api/client'
import { BuildView } from '../deck/BuildView'
import { useThemes, themeByName } from '../deck/useThemes'
import { SourcesPanel } from '../notebook/SourcesPanel'
import { ChatPanel } from '../notebook/ChatPanel'
import { ArtifactsPanel } from '../notebook/ArtifactsPanel'
import { SourceReader } from '../notebook/SourceReader'

interface ArtifactSummary {
  id: string
  kind: string
  title: string
  version: number
  updated: number
}
interface NotebookDetail {
  id: string
  name: string
  artifacts: ArtifactSummary[]
}

export function NotebookWorkspace() {
  const { notebookId } = useParams()
  const navigate = useNavigate()
  const [notebook, setNotebook] = useState<NotebookDetail | null>(null)
  const [job, setJob] = useState<{ jobId: string; kind: string; title: string } | null>(null)
  const [reader, setReader] = useState<{ sourceId: string; highlight: string } | null>(null)
  const themes = useThemes()

  const load = () => api.get<NotebookDetail>(`/api/notebooks/${notebookId}`).then(setNotebook)
  useEffect(() => {
    load()
  }, [notebookId])

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
        <button onClick={() => navigate('/')} className="text-[12px] text-ws-muted hover:text-ws-ink">
          ← Notebooks
        </button>
        <span className="font-display text-[14px] font-semibold">{notebook?.name ?? 'Notebook'}</span>
      </div>
      <div className="flex min-h-0 flex-1">
        {notebookId && <SourcesPanel notebookId={notebookId} />}
        {notebookId && (
          <ChatPanel
            notebookId={notebookId}
            onCite={(e) => setReader({ sourceId: e.source_id, highlight: e.text })}
          />
        )}
        {reader ? (
          <SourceReader
            sourceId={reader.sourceId}
            highlight={reader.highlight}
            onClose={() => setReader(null)}
          />
        ) : (
          <ArtifactsPanel
            artifacts={notebook?.artifacts ?? []}
            onGenerate={generate}
            onOpen={(a) => navigate(`/n/${notebookId}/${a.kind}/${a.id}`)}
          />
        )}
      </div>
    </div>
  )
}
