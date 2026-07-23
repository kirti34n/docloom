import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'
import { Library, LogOut, Menu, NotebookPen, Plus, Settings2, X } from 'lucide-react'
import { Toaster, toast } from './ui/toast'
import { IconButton } from './ui'
import { api } from './api/client'
import { useAuth, type Workspace } from './auth/AuthContext'

const navItems = [
  { to: '/', label: 'Notebooks', icon: NotebookPen, end: true },
  { to: '/assets', label: 'Assets', icon: Library, end: false },
  { to: '/settings', label: 'Settings', icon: Settings2, end: false },
]

function WorkspaceSwitcher() {
  const { workspaces, currentWorkspace, setCurrentWorkspace, refreshWorkspaces } = useAuth()

  const create = async () => {
    try {
      const ws = await api.post<Workspace>('/api/workspaces', {
        name: `Workspace ${workspaces.length + 1}`,
      })
      const all = await refreshWorkspaces()
      const created = all.find((w) => w.id === ws.id)
      if (created) setCurrentWorkspace(created)
    } catch (e) {
      toast.error(`Couldn't create workspace: ${e instanceof Error ? e.message : e}`)
    }
  }

  if (!currentWorkspace) return null
  return (
    <div className="flex items-center gap-1.5 px-2">
      <select
        aria-label="Workspace"
        value={currentWorkspace.id}
        onChange={(e) => {
          const w = workspaces.find((x) => x.id === e.target.value)
          if (w) setCurrentWorkspace(w)
        }}
        className="min-w-0 flex-1 truncate rounded-[var(--radius-sm)] border border-ws-line bg-ws-bg px-2.5 py-1.5 text-sm font-medium outline-none focus:border-ws-accent"
      >
        {workspaces.map((w) => (
          <option key={w.id} value={w.id}>
            {w.name}
          </option>
        ))}
      </select>
      <IconButton
        onClick={create}
        label="New workspace"
        className="shrink-0 border border-ws-line"
      >
        <Plus size={15} />
      </IconButton>
    </div>
  )
}

export function Shell() {
  const { user, logout } = useAuth()
  const location = useLocation()
  const [navOpen, setNavOpen] = useState(false)
  // route the outlet through a keyed wrapper so each screen fades in
  const routeKey = location.pathname.split('/').slice(0, 3).join('/')
  // close the mobile drawer on navigation
  useEffect(() => setNavOpen(false), [routeKey])
  return (
    <div className="relative flex h-full">
      {/* mobile top bar with a menu toggle — only below md */}
      <div className="absolute inset-x-0 top-0 z-30 flex h-12 items-center gap-2 border-b border-ws-line bg-ws-panel px-3 md:hidden">
        <IconButton label="Open menu" onClick={() => setNavOpen(true)}>
          <Menu size={18} />
        </IconButton>
        <span className="font-display text-lg font-semibold tracking-tight text-ws-ink">
          docloom<span className="text-woad"> studio</span>
        </span>
      </div>
      {/* scrim behind the drawer */}
      {navOpen && (
        <div className="fixed inset-0 z-30 bg-black/30 md:hidden" onClick={() => setNavOpen(false)} />
      )}
      <aside className={`z-40 flex w-52 shrink-0 flex-col border-r border-ws-line bg-ws-panel
        max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:shadow-[var(--shadow-float)] max-md:transition-transform
        ${navOpen ? 'max-md:translate-x-0' : 'max-md:-translate-x-full'}`}>
        <div className="flex items-center justify-between px-5 pt-5 pb-3">
          <span className="font-display text-lg font-semibold tracking-tight text-ws-ink">
            docloom<span className="text-woad"> studio</span>
          </span>
          <IconButton label="Close menu" onClick={() => setNavOpen(false)} className="md:hidden">
            <X size={16} />
          </IconButton>
        </div>
        <div className="pb-3">
          <WorkspaceSwitcher />
        </div>
        <nav className="flex flex-col gap-0.5 px-2">
          {navItems.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `relative flex items-center gap-2.5 rounded-[var(--radius-sm)] px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-ws-bg text-ws-ink before:absolute before:left-0 before:top-1/2 before:h-4 before:w-0.5 before:-translate-y-1/2 before:rounded-full before:bg-woad'
                    : 'text-ws-muted hover:bg-ws-bg hover:text-ws-ink'
                }`
              }
            >
              <Icon size={15} strokeWidth={2} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto border-t border-ws-line px-3 py-3">
          <div className="truncate px-1 text-2xs text-ws-muted" title={user?.email}>
            {user?.email}
          </div>
          <button
            onClick={logout}
            className="mt-1 flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-1 py-1.5 text-xs text-ws-muted hover:text-ws-ink"
          >
            <LogOut size={13} /> Sign out
          </button>
        </div>
      </aside>
      <main key={routeKey} className="min-w-0 flex-1 overflow-auto animate-fade-in max-md:pt-12">
        <Outlet />
      </main>
      <Toaster />
    </div>
  )
}
