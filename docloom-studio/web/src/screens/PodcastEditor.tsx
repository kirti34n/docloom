import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Loader2, Mic } from 'lucide-react'
import { api } from '../api/client'

interface Turn {
  speaker: 'A' | 'B'
  text: string
}
interface PodcastPayload {
  script: { title: string; turns: Turn[] }
  audio_path: string | null
  duration_s: number | null
}
interface Artifact {
  id: string
  kind: string
  title: string
  payload: PodcastPayload
}

export function PodcastEditor() {
  const { notebookId, artifactId } = useParams()
  const navigate = useNavigate()
  const [artifact, setArtifact] = useState<Artifact | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<Artifact>(`/api/artifacts/${artifactId}`)
      .then(setArtifact)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [artifactId])

  if (error) return <div className="p-8 text-[13px] text-ws-danger">{error}</div>
  if (!artifact)
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="animate-spin text-ws-muted" />
      </div>
    )

  const { script, audio_path, duration_s } = artifact.payload

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-ws-line px-5 py-2.5">
        <button
          onClick={() => navigate(`/n/${notebookId}`)}
          className="text-[12px] text-ws-muted hover:text-ws-ink"
        >
          ← Notebook
        </button>
        <Mic size={14} className="text-ws-accent" />
        <span className="font-display text-[14px] font-semibold">
          {script?.title ?? 'Podcast'}
        </span>
      </div>

      <div className="mx-auto w-full max-w-2xl flex-1 overflow-auto px-6 py-6">
        {audio_path ? (
          <>
            <audio controls src={`/api/artifacts/${artifactId}/audio.wav`} className="w-full">
              Your browser does not support audio playback.
            </audio>
            {duration_s ? (
              <div className="mt-1 text-[12px] text-ws-muted">{Math.round(duration_s)}s</div>
            ) : null}
          </>
        ) : (
          <div className="rounded-xl border border-dashed border-ws-line p-4 text-[13px] text-ws-muted">
            Audio wasn’t generated. Install local TTS with{' '}
            <code className="font-mono">pip install kokoro soundfile</code> and regenerate,
            or set a TTS provider in Settings. The transcript is ready below.
          </div>
        )}

        <ol className="mt-6 space-y-4">
          {(script?.turns ?? []).map((t, i) => (
            <li key={i} className="flex gap-3">
              <span
                title={t.speaker === 'A' ? 'Host' : 'Guest'}
                className={`mt-0.5 h-7 w-7 shrink-0 rounded-full text-center text-[12px] font-semibold leading-7 ${
                  t.speaker === 'A' ? 'bg-ws-accent text-white' : 'bg-ws-bg text-ws-ink'
                }`}
              >
                {t.speaker === 'A' ? 'H' : 'G'}
              </span>
              <p className="text-[14px] leading-relaxed">{t.text}</p>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}
