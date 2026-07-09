import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { Plus } from 'lucide-react'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { toast } from '../ui/toast'

interface Notebook {
  id: string
  name: string
  updated: number
}

export function NotebooksList() {
  const { currentWorkspace } = useAuth()
  const [notebooks, setNotebooks] = useState<Notebook[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()
  const workspaceId = currentWorkspace?.id

  const load = useCallback(() => {
    if (!workspaceId) return
    setError(null)
    setNotebooks(null)
    api
      .get<Notebook[]>(`/api/notebooks?workspace_id=${workspaceId}`)
      .then(setNotebooks)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [workspaceId])

  useEffect(() => {
    load()
  }, [load])

  const create = async () => {
    if (!workspaceId) return
    try {
      const nb = await api.post<Notebook>('/api/notebooks', {
        name: 'Untitled notebook',
        workspace_id: workspaceId,
      })
      navigate(`/n/${nb.id}`)
    } catch (e) {
      toast.error(`Couldn't create notebook: ${e instanceof Error ? e.message : e}`)
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-8 py-10">
      <div className="flex items-baseline justify-between">
        <h1 className="font-display text-xl font-semibold">Notebooks</h1>
        <button
          onClick={create}
          className="ds-btn ds-btn-primary px-3.5 py-2 text-[13px]"
        >
          <Plus size={14} /> New notebook
        </button>
      </div>

      {error ? (
        <div className="mt-16 rounded-xl border border-ws-danger/30 bg-ws-panel p-8 text-center">
          <p className="text-[14px] font-medium text-ws-ink">Couldn’t load notebooks</p>
          <p className="mx-auto mt-1 max-w-sm text-[13px] text-ws-muted">{error}</p>
          <button
            onClick={load}
            className="mt-4 rounded-lg border border-ws-line px-3 py-1.5 text-[13px] hover:bg-ws-bg"
          >
            Retry
          </button>
        </div>
      ) : notebooks === null ? (
        <ul className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <li key={i} className="ds-skeleton h-[86px]" />
          ))}
        </ul>
      ) : notebooks.length === 0 ? (
        <div className="mt-16 rounded-xl border border-dashed border-ws-line p-12 text-center">
          <p className="font-display text-[15px] font-medium">Start your first notebook</p>
          <p className="mx-auto mt-2 max-w-sm text-[13px] text-ws-muted">
            Add your documents or let the agent research a topic, then generate decks, reports,
            diagrams, and infographics you can edit.
          </p>
        </div>
      ) : (
        <ul className="stagger mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {notebooks.map((nb, i) => (
            <li key={nb.id} style={{ ['--i' as string]: i }}>
              <button
                onClick={() => navigate(`/n/${nb.id}`)}
                className="ds-card ds-card-hover w-full p-5 text-left"
              >
                <span className="block truncate font-display text-[14px] font-medium">{nb.name}</span>
                <span className="mt-1 block text-[12px] text-ws-muted">
                  {new Date(nb.updated * 1000).toLocaleString()}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
