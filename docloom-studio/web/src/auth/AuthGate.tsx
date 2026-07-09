import { type ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import { useAuth } from './AuthContext'
import { LoginScreen } from './LoginScreen'

/** Renders children only when authenticated; otherwise the login screen. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center bg-ws-bg">
        <Loader2 className="animate-spin text-ws-muted" />
      </div>
    )
  }
  if (!user) return <LoginScreen />
  return <>{children}</>
}
