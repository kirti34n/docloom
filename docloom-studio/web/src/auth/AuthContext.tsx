import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import { api, setUnauthorizedHandler } from '../api/client'

export interface User {
  id: string
  email: string
}
export interface Workspace {
  id: string
  name: string
}

interface AuthState {
  user: User | null
  loading: boolean
  workspaces: Workspace[]
  currentWorkspace: Workspace | null
  setCurrentWorkspace: (w: Workspace) => void
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refreshWorkspaces: () => Promise<Workspace[]>
}

const Ctx = createContext<AuthState | null>(null)
const WS_KEY = 'ds.workspace'

export function useAuth(): AuthState {
  const c = useContext(Ctx)
  if (!c) throw new Error('useAuth must be used within <AuthProvider>')
  return c
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [currentWorkspace, setCurrent] = useState<Workspace | null>(null)

  const loadWorkspaces = useCallback(async () => {
    const ws = await api.get<Workspace[]>('/api/workspaces')
    setWorkspaces(ws)
    const savedId = localStorage.getItem(WS_KEY)
    setCurrent(ws.find((w) => w.id === savedId) ?? ws[0] ?? null)
    return ws
  }, [])

  const clear = useCallback(() => {
    setUser(null)
    setWorkspaces([])
    setCurrent(null)
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(clear)
    return () => setUnauthorizedHandler(null)
  }, [clear])

  useEffect(() => {
    ;(async () => {
      try {
        setUser(await api.get<User>('/api/auth/me'))
        await loadWorkspaces()
      } catch {
        clear()
      } finally {
        setLoading(false)
      }
    })()
  }, [loadWorkspaces, clear])

  const afterAuth = async (u: User) => {
    setUser(u)
    await loadWorkspaces()
  }

  const login = async (email: string, password: string) =>
    afterAuth(await api.post<User>('/api/auth/login', { email, password }))

  const register = async (email: string, password: string) =>
    afterAuth(await api.post<User>('/api/auth/register', { email, password }))

  const logout = async () => {
    await api.post('/api/auth/logout')
    clear()
  }

  const setCurrentWorkspace = (w: Workspace) => {
    localStorage.setItem(WS_KEY, w.id)
    setCurrent(w)
  }

  return (
    <Ctx.Provider
      value={{
        user,
        loading,
        workspaces,
        currentWorkspace,
        setCurrentWorkspace,
        login,
        register,
        logout,
        refreshWorkspaces: loadWorkspaces,
      }}
    >
      {children}
    </Ctx.Provider>
  )
}
