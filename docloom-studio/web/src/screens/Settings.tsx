import { useEffect, useState } from 'react'
import { CheckCircle2, Loader2, XCircle } from 'lucide-react'
import { api } from '../api/client'

interface ProviderConfig {
  kind: string
  base_url: string
  api_key: string
  model: string
}

interface AllSettings {
  'provider.generation': ProviderConfig
  'provider.embeddings': ProviderConfig
  'research.tavily_key': string
  'assets.pexels_key': string
  [key: string]: unknown
}

const PROVIDER_PRESETS: Record<string, { label: string; base_url: string; hint: string }> = {
  'llama-server': {
    label: 'llama.cpp server (recommended)',
    base_url: 'http://localhost:8080',
    hint: 'True schema enforcement. Download the Windows CUDA build from github.com/ggml-org/llama.cpp/releases and run llama-server -m model.gguf',
  },
  ollama: {
    label: 'Ollama',
    base_url: 'http://localhost:11434',
    hint: 'Uses your installed Ollama models.',
  },
  lmstudio: {
    label: 'LM Studio',
    base_url: 'http://localhost:1234',
    hint: 'Enable the local server in LM Studio.',
  },
  openai: {
    label: 'OpenAI API',
    base_url: 'https://api.openai.com',
    hint: 'Requires an API key.',
  },
  anthropic: {
    label: 'Anthropic API',
    base_url: 'https://api.anthropic.com',
    hint: 'Requires an API key.',
  },
}

export function Settings() {
  const [settings, setSettings] = useState<AllSettings | null>(null)
  const [models, setModels] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [test, setTest] = useState<'idle' | 'running' | 'ok' | 'fail'>('idle')
  const [testDetail, setTestDetail] = useState('')

  useEffect(() => {
    api.get<AllSettings>('/api/settings').then(setSettings)
  }, [])

  useEffect(() => {
    if (!settings) return
    api
      .get<{ models: string[] }>('/api/providers/models?slot=generation')
      .then((r) => setModels(r.models))
      .catch(() => setModels([]))
  }, [settings?.['provider.generation'].kind, settings?.['provider.generation'].base_url])

  if (!settings) {
    return (
      <div className="flex h-full items-center justify-center text-ws-muted">
        <Loader2 className="animate-spin" />
      </div>
    )
  }

  const gen = settings['provider.generation']

  const update = (patch: Partial<ProviderConfig>) => {
    setSettings({ ...settings, 'provider.generation': { ...gen, ...patch } })
  }

  const save = async () => {
    setSaving(true)
    try {
      const next = await api.put<AllSettings>('/api/settings', { values: settings })
      setSettings(next)
    } finally {
      setSaving(false)
    }
  }

  const runTest = async () => {
    setTest('running')
    setTestDetail('')
    await save()
    const result = await api.post<{ ok: boolean; raw?: string; error?: string }>(
      '/api/providers/test',
    )
    setTest(result.ok ? 'ok' : 'fail')
    setTestDetail(result.ok ? (result.raw ?? '') : (result.error ?? 'unknown error'))
  }

  return (
    <div className="mx-auto max-w-2xl px-8 py-10">
      <h1 className="font-display text-xl font-semibold">Settings</h1>
      <p className="mt-1 text-[13px] text-ws-muted">
        Everything runs on your machine by default. API keys are optional and stored locally.
      </p>

      <section className="mt-8 rounded-xl border border-ws-line bg-ws-panel p-6 shadow-[var(--shadow-panel)]">
        <h2 className="text-[15px] font-semibold">Generation model</h2>

        <label className="mt-4 block text-[12px] font-medium text-ws-muted">Provider</label>
        <select
          value={gen.kind}
          onChange={(e) => {
            const kind = e.target.value
            update({ kind, base_url: PROVIDER_PRESETS[kind].base_url })
          }}
          className="mt-1 w-full rounded-lg border border-ws-line bg-ws-bg px-3 py-2 text-[13px]"
        >
          {Object.entries(PROVIDER_PRESETS).map(([kind, p]) => (
            <option key={kind} value={kind}>
              {p.label}
            </option>
          ))}
        </select>
        <p className="mt-1 text-[12px] text-ws-muted">{PROVIDER_PRESETS[gen.kind]?.hint}</p>

        <div className="mt-4 grid grid-cols-2 gap-4">
          <div>
            <label className="block text-[12px] font-medium text-ws-muted">Server URL</label>
            <input
              value={gen.base_url}
              onChange={(e) => update({ base_url: e.target.value })}
              className="mt-1 w-full rounded-lg border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px]"
            />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-ws-muted">Model</label>
            {models.length > 0 ? (
              <select
                value={gen.model}
                onChange={(e) => update({ model: e.target.value })}
                className="mt-1 w-full rounded-lg border border-ws-line bg-ws-bg px-3 py-2 text-[13px]"
              >
                {!models.includes(gen.model) && <option value={gen.model}>{gen.model}</option>}
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={gen.model}
                onChange={(e) => update({ model: e.target.value })}
                className="mt-1 w-full rounded-lg border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px]"
              />
            )}
          </div>
        </div>

        {(gen.kind === 'openai' || gen.kind === 'anthropic') && (
          <div className="mt-4">
            <label className="block text-[12px] font-medium text-ws-muted">API key</label>
            <input
              type="password"
              value={gen.api_key}
              onChange={(e) => update({ api_key: e.target.value })}
              className="mt-1 w-full rounded-lg border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px]"
            />
          </div>
        )}

        <div className="mt-6 flex items-center gap-3">
          <button
            onClick={save}
            disabled={saving}
            className="rounded-lg bg-ws-ink px-4 py-2 text-[13px] font-medium text-white disabled:opacity-50"
          >
            Save
          </button>
          <button
            onClick={runTest}
            disabled={test === 'running'}
            className="rounded-lg border border-ws-line bg-ws-panel px-4 py-2 text-[13px] font-medium"
          >
            {test === 'running' ? 'Testing…' : 'Test generation'}
          </button>
          {test === 'ok' && <CheckCircle2 size={18} className="text-ws-ok" />}
          {test === 'fail' && <XCircle size={18} className="text-ws-danger" />}
        </div>
        {testDetail && (
          <pre className="mt-3 overflow-x-auto rounded-lg bg-ws-bg p-3 font-mono text-[11px] text-ws-muted">
            {testDetail}
          </pre>
        )}
      </section>
    </div>
  )
}
