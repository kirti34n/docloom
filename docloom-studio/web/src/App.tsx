import { NavLink, Outlet } from 'react-router'
import { Library, NotebookPen, Settings2 } from 'lucide-react'

const navItems = [
  { to: '/', label: 'Notebooks', icon: NotebookPen, end: true },
  { to: '/assets', label: 'Assets', icon: Library, end: false },
  { to: '/settings', label: 'Settings', icon: Settings2, end: false },
]

export function Shell() {
  return (
    <div className="flex h-full">
      <aside className="flex w-52 shrink-0 flex-col border-r border-ws-line bg-ws-panel">
        <div className="px-5 pt-5 pb-4">
          <span className="font-display text-[17px] font-semibold tracking-tight">
            docloom<span className="text-ws-accent"> studio</span>
          </span>
        </div>
        <nav className="flex flex-col gap-0.5 px-2">
          {navItems.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors ${
                  isActive
                    ? 'bg-ws-bg text-ws-ink'
                    : 'text-ws-muted hover:bg-ws-bg hover:text-ws-ink'
                }`
              }
            >
              <Icon size={15} strokeWidth={2} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto px-5 py-4 text-[11px] text-ws-muted">
          local · private · free
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
