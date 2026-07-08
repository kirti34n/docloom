import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { Plus } from 'lucide-react'
import { api } from '../api/client'

interface Notebook {
  id: string
  name: string
  updated: number
}

export function NotebooksList() {
  const [notebooks, setNotebooks] = useState<Notebook[] | null>(null)
  const navigate = useNavigate()

  const load = () =>
    api
      .get<Notebook[]>('/api/notebooks')
      .then(setNotebooks)
      .catch(() => setNotebooks([]))

  useEffect(() => {
    load()
  }, [])

  const create = async () => {
    const nb = await api.post<Notebook>('/api/notebooks', { name: 'Untitled notebook' })
    navigate(`/n/${nb.id}`)
  }

  return (
    <div className="mx-auto max-w-3xl px-8 py-10">
      <div className="flex items-baseline justify-between">
        <h1 className="font-display text-xl font-semibold">Notebooks</h1>
        <button
          onClick={create}
          className="flex items-center gap-1.5 rounded-lg bg-ws-ink px-3.5 py-2 text-[13px] font-medium text-white"
        >
          <Plus size={14} /> New notebook
        </button>
      </div>

      {notebooks === null ? null : notebooks.length === 0 ? (
        <div className="mt-16 rounded-xl border border-dashed border-ws-line p-12 text-center">
          <p className="font-display text-[15px] font-medium">Start your first notebook</p>
          <p className="mx-auto mt-2 max-w-sm text-[13px] text-ws-muted">
            Add your documents or let the agent research a topic, then generate decks, reports,
            diagrams, and infographics you can edit.
          </p>
        </div>
      ) : (
        <ul className="mt-6 grid grid-cols-2 gap-3">
          {notebooks.map((nb) => (
            <li key={nb.id}>
              <button
                onClick={() => navigate(`/n/${nb.id}`)}
                className="w-full rounded-xl border border-ws-line bg-ws-panel p-5 text-left shadow-[var(--shadow-panel)] transition-transform hover:-translate-y-0.5"
              >
                <span className="block truncate text-[14px] font-medium">{nb.name}</span>
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
