import { useState, type FormEvent } from 'react'
import { Loader2 } from 'lucide-react'
import { useAuth } from './AuthContext'
import { Button, Field } from '../ui'

/** The loom, drawn once: warp threads (woad) crossed by one weft line
 *  (brass), knotted at each crossing. The whole product in one small mark. */
function LoomMark() {
  const warp = [8, 20, 32, 44, 56, 68]
  return (
    <svg width="76" height="28" viewBox="0 0 76 28" aria-hidden className="mx-auto mb-6">
      {warp.map((x) => (
        <line key={x} x1={x} y1="1" x2={x} y2="27" stroke="var(--woad)" strokeWidth="1.5" opacity="0.5" />
      ))}
      <line x1="2" y1="14" x2="74" y2="14" stroke="var(--brass)" strokeWidth="1.5" />
      {warp.map((x) => (
        <circle key={`c${x}`} cx={x} cy="14" r="1.75" fill="var(--brass)" />
      ))}
    </svg>
  )
}

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
      setError(err instanceof Error ? err.message : 'Something went wrong. Try again.')
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center bg-ws-bg px-6">
      <div className="w-full max-w-sm">
        <div className="text-center">
          <LoomMark />
          <span className="font-display text-2xl font-semibold tracking-tight text-ws-ink">
            docloom<span className="text-woad"> studio</span>
          </span>
          <p className="mx-auto mt-3 max-w-[26rem] text-[13px] leading-relaxed text-ws-muted">
            Decks, docs, and reports woven from your own sources, cited line by line.
          </p>
        </div>

        <form onSubmit={submit} className="mt-8 rounded-[var(--radius)] border border-ws-line bg-ws-panel p-6">
          <Field label="Email" htmlFor="login-email">
            <input
              id="login-email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 text-[14px] text-ws-ink outline-none focus:border-woad"
            />
          </Field>
          <Field
            label="Password"
            htmlFor="login-password"
            hint={mode === 'register' ? 'At least 8 characters.' : undefined}
            className="mt-4"
          >
            <input
              id="login-password"
              type="password"
              required
              minLength={mode === 'register' ? 8 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 text-[14px] text-ws-ink outline-none focus:border-woad"
            />
          </Field>

          {error && (
            <p className="mt-4 rounded-[var(--radius-sm)] border border-madder/30 bg-madder/5 px-3 py-2 text-[13px] text-madder">
              {error}
            </p>
          )}

          <Button type="submit" variant="primary" disabled={busy} className="mt-5 w-full py-2.5">
            {busy && <Loader2 size={15} className="animate-spin" />}
            {mode === 'login' ? 'Sign in' : 'Create account'}
          </Button>
        </form>

        <p className="mt-4 text-center text-[13px] text-ws-muted">
          {mode === 'login' ? "Don't have an account? " : 'Already have an account? '}
          <button
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login')
              setError(null)
            }}
            className="font-medium text-woad hover:underline"
          >
            {mode === 'login' ? 'Sign up' : 'Sign in'}
          </button>
        </p>
      </div>
    </div>
  )
}
