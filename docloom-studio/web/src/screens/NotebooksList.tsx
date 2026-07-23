import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { MoreVertical, Plus } from 'lucide-react'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { toast } from '../ui/toast'
import { Button, Empty, Eyebrow, IconButton } from '../ui'

interface Notebook {
  id: string
  name: string
  updated: number
}

function NotebookCard({
  nb,
  onNavigate,
  onRenamed,
  onDeleted,
}: {
  nb: Notebook
  onNavigate: () => void
  onRenamed: (name: string) => void
  onDeleted: () => void
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState(nb.name)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
        setConfirmingDelete(false)
      }
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [menuOpen])

  const saveRename = async () => {
    const name = draft.trim()
    setRenaming(false)
    if (!name || name === nb.name) {
      setDraft(nb.name)
      return
    }
    try {
      await api.patch(`/api/notebooks/${nb.id}`, { name })
      onRenamed(name)
    } catch (e) {
      setDraft(nb.name)
      toast.error(`Couldn't rename notebook: ${e instanceof Error ? e.message : e}`)
    }
  }

  const del = async () => {
    try {
      await api.del(`/api/notebooks/${nb.id}`)
      onDeleted()
      toast.success('Notebook deleted')
    } catch (e) {
      toast.error(`Couldn't delete notebook: ${e instanceof Error ? e.message : e}`)
    }
  }

  return (
    <div className="group relative rounded-[var(--radius)] border border-ws-line bg-ws-panel p-5 transition-colors hover:border-woad">
      {renaming ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={saveRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
            if (e.key === 'Escape') {
              setDraft(nb.name)
              setRenaming(false)
            }
          }}
          className="block w-full truncate rounded-[var(--radius-sm)] border border-woad bg-ws-bg px-2 py-1 pr-6 text-base font-semibold text-ws-ink outline-none"
        />
      ) : (
        <button onClick={onNavigate} className="block w-full text-left">
          <span className="block truncate pr-6 text-base font-semibold text-ws-ink">{nb.name}</span>
          <span className="mt-1 block text-xs text-ws-muted">
            {new Date(nb.updated * 1000).toLocaleString()}
          </span>
        </button>
      )}

      <div ref={menuRef} className="absolute right-3 top-3">
        <IconButton
          label="Notebook options"
          onClick={() => {
            setMenuOpen((v) => !v)
            setConfirmingDelete(false)
          }}
          className={`transition-opacity duration-[var(--dur-fast)] group-hover:opacity-100 focus-visible:opacity-100 ${menuOpen ? 'opacity-100' : 'opacity-40'}`}
        >
          <MoreVertical size={15} />
        </IconButton>
        {menuOpen && (
          <div className="absolute right-0 top-full z-10 mt-1 w-40 rounded-[var(--radius)] border border-ws-line bg-ws-panel py-1 shadow-[var(--shadow-float)]">
            {!confirmingDelete ? (
              <>
                <button
                  onClick={() => {
                    setMenuOpen(false)
                    setRenaming(true)
                  }}
                  className="block w-full px-3 py-1.5 text-left text-sm text-ws-ink hover:bg-ws-bg"
                >
                  Rename
                </button>
                <button
                  onClick={() => setConfirmingDelete(true)}
                  className="block w-full px-3 py-1.5 text-left text-sm text-madder hover:bg-ws-bg"
                >
                  Delete
                </button>
              </>
            ) : (
              <div className="px-3 py-1.5">
                <p className="text-xs text-ws-muted">Delete this notebook?</p>
                <div className="mt-1.5 flex gap-1.5">
                  <button
                    onClick={() => {
                      setMenuOpen(false)
                      setConfirmingDelete(false)
                      del()
                    }}
                    className="rounded-[var(--radius-sm)] bg-madder px-2 py-1 text-xs font-medium text-ws-bg"
                  >
                    Delete
                  </button>
                  <button
                    onClick={() => setConfirmingDelete(false)}
                    className="rounded-[var(--radius-sm)] border border-ws-line px-2 py-1 text-xs text-ws-ink"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export function NotebooksList() {
  const { currentWorkspace } = useAuth()
  const [notebooks, setNotebooks] = useState<Notebook[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()
  const workspaceId = currentWorkspace?.id
  const reqRef = useRef(0)

  const load = useCallback(() => {
    if (!workspaceId) return
    const req = ++reqRef.current
    setError(null)
    setNotebooks(null)
    api
      .get<Notebook[]>(`/api/notebooks?workspace_id=${workspaceId}`)
      .then((nbs) => {
        if (req === reqRef.current) setNotebooks(nbs)
      })
      .catch((e) => {
        if (req === reqRef.current) setError(e instanceof Error ? e.message : String(e))
      })
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
        <div>
          <Eyebrow>Workbench</Eyebrow>
          <h1 className="mt-1 font-display text-2xl font-semibold text-ws-ink">Notebooks</h1>
        </div>
        <Button variant="primary" onClick={create}>
          <Plus size={14} /> New notebook
        </Button>
      </div>

      {error ? (
        <div className="mt-16">
          <Empty title="Couldn't load notebooks" body={error} action={<Button variant="quiet" onClick={load}>Retry</Button>} />
        </div>
      ) : notebooks === null ? (
        <ul className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <li key={i} className="ds-skeleton h-[86px]" />
          ))}
        </ul>
      ) : notebooks.length === 0 ? (
        <div className="mt-16">
          <Empty
            title="Start your first notebook"
            body="Add your documents or let the agent research a topic, then generate decks, reports, diagrams, and infographics you can edit."
            action={
              <Button variant="quiet" onClick={create}>
                <Plus size={14} /> New notebook
              </Button>
            }
          />
        </div>
      ) : (
        <ul className="stagger mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {notebooks.map((nb, i) => (
            <li key={nb.id} style={{ ['--i' as string]: i }}>
              <NotebookCard
                nb={nb}
                onNavigate={() => navigate(`/n/${nb.id}`)}
                onRenamed={(name) =>
                  setNotebooks((list) => list?.map((n) => (n.id === nb.id ? { ...n, name } : n)) ?? null)
                }
                onDeleted={() => setNotebooks((list) => list?.filter((n) => n.id !== nb.id) ?? null)}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
