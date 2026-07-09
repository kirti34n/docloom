import { useState, type FormEvent } from 'react'
import { Loader2 } from 'lucide-react'
import { useAuth } from './AuthContext'

export function LoginScreen() {
  const { login, register } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'login') await login(email, password)
      else await register(email, password)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
      setBusy(false)
    }
  }

  return (
    <div className="relative flex h-full items-center justify-center overflow-hidden bg-ws-bg px-6">
      {/* soft brand aura behind the card */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/3 h-[36rem] w-[36rem] -translate-x-1/2 -translate-y-1/2 rounded-full opacity-[0.07] blur-3xl"
        style={{ background: 'radial-gradient(circle, var(--ws-accent), transparent 70%)' }}
      />
      <div className="animate-fade-up w-full max-w-sm">
        <div className="mb-8 text-center">
          <span className="font-display text-2xl font-semibold tracking-tight">
            docloom<span className="text-ws-accent"> studio</span>
          </span>
          <p className="mt-2 text-[13px] text-ws-muted">
            {mode === 'login' ? 'Sign in to your workspace' : 'Create your account'}
          </p>
        </div>

        <form
          onSubmit={submit}
          className="ds-card rounded-2xl p-6 shadow-[var(--shadow-lg)]"
        >
          <label className="block text-[12px] font-medium text-ws-muted">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            className="mt-1 mb-4 w-full rounded-lg border border-ws-line bg-ws-bg px-3 py-2 text-[14px] outline-none focus:border-ws-accent"
          />
          <label className="block text-[12px] font-medium text-ws-muted">Password</label>
          <input
            type="password"
            required
            minLength={mode === 'register' ? 8 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            className="mt-1 w-full rounded-lg border border-ws-line bg-ws-bg px-3 py-2 text-[14px] outline-none focus:border-ws-accent"
          />
          {mode === 'register' && (
            <p className="mt-1.5 text-[11px] text-ws-muted">At least 8 characters.</p>
          )}

          {error && <p className="mt-3 text-[13px] text-ws-danger">{error}</p>}

          <button
            type="submit"
            disabled={busy}
            className="ds-btn ds-btn-primary mt-5 w-full py-2.5 text-[14px] disabled:opacity-50"
          >
            {busy && <Loader2 size={15} className="animate-spin" />}
            {mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
        </form>

        <p className="mt-4 text-center text-[13px] text-ws-muted">
          {mode === 'login' ? "Don't have an account? " : 'Already have an account? '}
          <button
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login')
              setError(null)
            }}
            className="font-medium text-ws-accent hover:underline"
          >
            {mode === 'login' ? 'Sign up' : 'Sign in'}
          </button>
        </p>
      </div>
    </div>
  )
}
