import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Loader2, XCircle } from 'lucide-react'
import { api } from '../api/client'
import { toast } from '../ui/toast'
import { Button, Eyebrow, Field, Panel } from '../ui'
import { useThemes } from '../deck/useThemes'

interface ProviderConfig {
  kind: string
  base_url: string
  api_key: string
  model: string
  max_tokens?: number
}

interface TtsConfig {
  kind: string
  lang: string
  voice_a: string
  voice_b: string
}

interface AllSettings {
  'provider.generation': ProviderConfig
  'provider.embeddings': ProviderConfig
  'provider.tts': TtsConfig
  'research.tavily_key': string
  'assets.pexels_key': string
  'deck.theme': string
  [key: string]: unknown
}

const PROVIDER_PRESETS: Record<string, { label: string; base_url: string; hint: string; model?: string; embedModel?: string }> = {
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
  gemini: {
    label: 'Google Gemini API',
    base_url: 'https://generativelanguage.googleapis.com',
    hint: 'Requires a Google AI Studio API key.',
    model: 'gemini-2.5-flash',
    embedModel: 'gemini-embedding-001',
  },
}

function needsApiKey(kind: string): boolean {
  return kind === 'openai' || kind === 'anthropic' || kind === 'gemini'
}

function ProviderFields({
  cfg,
  models,
  onChange,
  isEmbeddings = false,
}: {
  cfg: ProviderConfig
  models: string[]
  onChange: (patch: Partial<ProviderConfig>) => void
  isEmbeddings?: boolean
}) {
  return (
    <>
      <Field label="Provider">
        <select
          value={cfg.kind}
          onChange={(e) => {
            const kind = e.target.value
            const preset = PROVIDER_PRESETS[kind]
            // the embeddings slot needs an embedding model, not the chat model
            // (e.g. gemini-embedding-001, not gemini-2.5-flash which 400s on embed)
            const model = isEmbeddings ? preset.embedModel ?? preset.model : preset.model
            onChange({ kind, base_url: preset.base_url, ...(model ? { model } : {}) })
          }}
          className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 text-[13px] text-ws-ink"
        >
          {Object.entries(PROVIDER_PRESETS).map(([kind, p]) => (
            <option key={kind} value={kind}>
              {p.label}
            </option>
          ))}
        </select>
      </Field>
      <p className="-mt-2 text-[12px] text-ws-muted">{PROVIDER_PRESETS[cfg.kind]?.hint}</p>

      <div className="grid grid-cols-2 gap-4">
        <Field label="Server URL">
          <input
            value={cfg.base_url}
            onChange={(e) => onChange({ base_url: e.target.value })}
            className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px] text-ws-ink"
          />
        </Field>
        <Field label="Model">
          {models.length > 0 ? (
            <select
              value={cfg.model}
              onChange={(e) => onChange({ model: e.target.value })}
              className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 text-[13px] text-ws-ink"
            >
              {!models.includes(cfg.model) && <option value={cfg.model}>{cfg.model}</option>}
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={cfg.model}
              onChange={(e) => onChange({ model: e.target.value })}
              className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px] text-ws-ink"
            />
          )}
        </Field>
      </div>

      {needsApiKey(cfg.kind) && (
        <Field label="API key">
          <input
            type="password"
            value={cfg.api_key}
            onChange={(e) => onChange({ api_key: e.target.value })}
            className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px] text-ws-ink"
          />
        </Field>
      )}
    </>
  )
}

export function Settings() {
  const [settings, setSettings] = useState<AllSettings | null>(null)
  const [genModels, setGenModels] = useState<string[]>([])
  const [embModels, setEmbModels] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [test, setTest] = useState<'idle' | 'running' | 'ok' | 'fail'>('idle')
  const [testDetail, setTestDetail] = useState('')
  const themes = useThemes()

  // /api/providers/models reads the SAVED provider config server-side, not
  // whatever's currently being edited, so only refresh it right after a load
  // or a successful save, never on every local edit (that would just show
  // stale models for the old config and look like it "isn't refreshing").
  const refreshModels = useCallback(async () => {
    const [gen, emb] = await Promise.all([
      api.get<{ models: string[] }>('/api/providers/models?slot=generation').catch(() => ({ models: [] })),
      api.get<{ models: string[] }>('/api/providers/models?slot=embeddings').catch(() => ({ models: [] })),
    ])
    setGenModels(gen.models)
    setEmbModels(emb.models)
  }, [])

  useEffect(() => {
    api.get<AllSettings>('/api/settings').then((s) => {
      setSettings(s)
      refreshModels()
    })
  }, [refreshModels])

  if (!settings) {
    return (
      <div className="flex h-full items-center justify-center text-ws-muted">
        <Loader2 className="animate-spin" />
      </div>
    )
  }

  const setGen = (patch: Partial<ProviderConfig>) =>
    setSettings({ ...settings, 'provider.generation': { ...settings['provider.generation'], ...patch } })
  const setEmb = (patch: Partial<ProviderConfig>) =>
    setSettings({ ...settings, 'provider.embeddings': { ...settings['provider.embeddings'], ...patch } })
  const setTts = (patch: Partial<TtsConfig>) =>
    setSettings({ ...settings, 'provider.tts': { ...settings['provider.tts'], ...patch } })
  const setStr = (key: 'research.tavily_key' | 'assets.pexels_key' | 'deck.theme', value: string) =>
    setSettings({ ...settings, [key]: value })

  const save = async () => {
    setSaving(true)
    try {
      const next = await api.put<AllSettings>('/api/settings', { values: settings })
      setSettings(next)
      await refreshModels()
      toast.success('Settings saved')
    } catch (e) {
      toast.error(`Couldn't save settings: ${e instanceof Error ? e.message : e}`)
      throw e
    } finally {
      setSaving(false)
    }
  }

  const runTest = async () => {
    setTest('running')
    setTestDetail('')
    try {
      await save()
      const result = await api.post<{ ok: boolean; raw?: string; error?: string }>('/api/providers/test')
      setTest(result.ok ? 'ok' : 'fail')
      setTestDetail(result.ok ? (result.raw ?? '') : (result.error ?? 'unknown error'))
    } catch (e) {
      setTest('fail')
      setTestDetail(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-8 py-10 pb-24">
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <Eyebrow>Configuration</Eyebrow>
          <h1 className="mt-1 font-display text-2xl font-semibold text-ws-ink">Settings</h1>
          <p className="mt-1 text-[13px] text-ws-muted">
            Everything runs on your machine by default. API keys are optional and stored locally.
          </p>
        </div>
        <Button variant="accent" onClick={save} disabled={saving}>
          {saving && <Loader2 size={14} className="animate-spin" />}
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>

      <Panel className="mt-8 p-6">
        <Eyebrow>Language model</Eyebrow>
        <h2 className="mt-1 font-display text-xl font-semibold text-ws-ink">Generation</h2>
        <p className="mt-1 text-[12.5px] text-ws-muted">Drafts documents, decks, and chat answers.</p>

        <div className="mt-4 grid gap-4">
          <ProviderFields cfg={settings['provider.generation']} models={genModels} onChange={setGen} />
          <div className="grid grid-cols-2 gap-4">
            <Field label="Max tokens" hint="Upper bound on tokens generated per call.">
              <input
                type="number"
                min={1}
                step={1}
                value={settings['provider.generation'].max_tokens ?? 8192}
                onChange={(e) => {
                  const v = e.target.valueAsNumber
                  // ProviderConfig.max_tokens is a pydantic int; coerce to a
                  // positive whole number so a fractional/blank entry cannot
                  // crash every generation call server-side
                  setGen({ max_tokens: Number.isNaN(v) ? 8192 : Math.max(1, Math.floor(v)) })
                }}
                className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px] text-ws-ink"
              />
            </Field>
          </div>
        </div>

        <div className="mt-5 flex items-center gap-3">
          <Button variant="quiet" onClick={runTest} disabled={test === 'running'}>
            {test === 'running' ? 'Testing…' : 'Test generation'}
          </Button>
          {test === 'ok' && <CheckCircle2 size={18} className="text-ws-ok" />}
          {test === 'fail' && <XCircle size={18} className="text-madder" />}
        </div>
        {testDetail && (
          <pre className="mt-3 overflow-x-auto rounded-[var(--radius-sm)] bg-ws-bg p-3 font-mono text-[11px] text-ws-muted">
            {testDetail}
          </pre>
        )}
      </Panel>

      <Panel className="mt-6 p-6">
        <Eyebrow>Language model</Eyebrow>
        <h2 className="mt-1 font-display text-xl font-semibold text-ws-ink">Embeddings</h2>
        <p className="mt-1 text-[12.5px] text-ws-muted">Turns your sources into searchable chunks for citations.</p>

        <div className="mt-4 grid gap-4">
          <ProviderFields cfg={settings['provider.embeddings']} models={embModels} onChange={setEmb} isEmbeddings />
        </div>
      </Panel>

      <Panel className="mt-6 p-6">
        <Eyebrow>Podcast</Eyebrow>
        <h2 className="mt-1 font-display text-xl font-semibold text-ws-ink">Narration voices</h2>
        <p className="mt-1 text-[12.5px] text-ws-muted">
          Kokoro, local and offline. Install with <code className="font-mono">pip install kokoro soundfile</code>.
        </p>

        <div className="mt-4 grid gap-4">
          <Field label="Language" hint="A language code, e.g. a for American English, b for British English.">
            <input
              value={settings['provider.tts'].lang}
              onChange={(e) => setTts({ lang: e.target.value })}
              className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px] text-ws-ink"
            />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Host voice" hint="e.g. af_heart">
              <input
                value={settings['provider.tts'].voice_a}
                onChange={(e) => setTts({ voice_a: e.target.value })}
                className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px] text-ws-ink"
              />
            </Field>
            <Field label="Guest voice" hint="e.g. am_michael">
              <input
                value={settings['provider.tts'].voice_b}
                onChange={(e) => setTts({ voice_b: e.target.value })}
                className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px] text-ws-ink"
              />
            </Field>
          </div>
        </div>
      </Panel>

      <Panel className="mt-6 p-6">
        <Eyebrow>Research and assets</Eyebrow>
        <h2 className="mt-1 font-display text-xl font-semibold text-ws-ink">API keys</h2>
        <p className="mt-1 text-[12.5px] text-ws-muted">Optional. Web research and asset search work without them.</p>

        <div className="mt-4 grid gap-4">
          <Field label="Tavily API key">
            <input
              type="password"
              value={settings['research.tavily_key']}
              onChange={(e) => setStr('research.tavily_key', e.target.value)}
              className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px] text-ws-ink"
            />
          </Field>
          <Field label="Pexels API key">
            <input
              type="password"
              value={settings['assets.pexels_key']}
              onChange={(e) => setStr('assets.pexels_key', e.target.value)}
              className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 font-mono text-[12px] text-ws-ink"
            />
          </Field>
        </div>
      </Panel>

      <Panel className="mt-6 p-6">
        <Eyebrow>Decks</Eyebrow>
        <h2 className="mt-1 font-display text-xl font-semibold text-ws-ink">Theme</h2>
        <p className="mt-1 text-[12.5px] text-ws-muted">The default theme for new decks.</p>

        <div className="mt-4">
          <Field label="Deck theme">
            <select
              value={settings['deck.theme']}
              onChange={(e) => setStr('deck.theme', e.target.value)}
              className="w-full rounded-[var(--radius)] border border-ws-line bg-ws-bg px-3 py-2 text-[13px] text-ws-ink"
            >
              {!themes.some((t) => t.name === settings['deck.theme']) && (
                <option value={settings['deck.theme']}>{settings['deck.theme']}</option>
              )}
              {themes.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.label ?? t.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Panel>
    </div>
  )
}
