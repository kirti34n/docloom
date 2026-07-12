import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Check, Download, Loader2, Mic, RefreshCw, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import { toast } from '../ui/toast'
import { Button, Empty, Eyebrow } from '../ui'

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
  const [loadError, setLoadError] = useState<string | null>(null)
  const [scriptTitle, setScriptTitle] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [audioPath, setAudioPath] = useState<string | null>(null)
  const [durationS, setDurationS] = useState<number | null>(null)
  const [state, setState] = useState<'saved' | 'dirty' | 'saving'>('saved')
  const [regenerating, setRegenerating] = useState(false)
  // the audio file's URL doesn't change on regenerate (same path), so bump
  // this to bust the browser's cache and force the <audio> element to reload
  const [audioVersion, setAudioVersion] = useState(0)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // always holds the latest turns so a failed regenerate re-saves any edit the
  // user made mid-synthesis, instead of the stale array its closure captured
  const turnsRef = useRef<Turn[]>([])
  useEffect(() => {
    turnsRef.current = turns
  }, [turns])

  useEffect(() => {
    api
      .get<Artifact>(`/api/artifacts/${artifactId}`)
      .then((a) => {
        setArtifact(a)
        setScriptTitle(a.payload.script?.title ?? '')
        setTurns(a.payload.script?.turns ?? [])
        setAudioPath(a.payload.audio_path)
        setDurationS(a.payload.duration_s)
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : String(e)))
  }, [artifactId])

  const persist = (next: Turn[]) => {
    setTurns(next)
    setState('dirty')
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      setState('saving')
      try {
        await api.put(`/api/artifacts/${artifactId}/payload`, {
          payload: {
            script: { title: scriptTitle, turns: next },
            audio_path: audioPath,
            duration_s: durationS,
          },
        })
        setState('saved')
      } catch (e) {
        setState('dirty')
        toast.error(`Save failed: ${e instanceof Error ? e.message : e}`)
      }
    }, 700)
  }

  const editTurn = (i: number, text: string) =>
    persist(turns.map((t, j) => (j === i ? { ...t, text } : t)))
  const toggleSpeaker = (i: number) =>
    persist(turns.map((t, j) => (j === i ? { ...t, speaker: t.speaker === 'A' ? 'B' : 'A' } : t)))
  const deleteTurn = (i: number) => persist(turns.filter((_, j) => j !== i))

  const audioUrl = `/api/artifacts/${artifactId}/audio.wav?v=${audioVersion}`

  const downloadAudio = () => {
    const a = document.createElement('a')
    a.href = audioUrl
    a.download = `${scriptTitle || 'podcast'}.wav`
    a.click()
  }

  // Re-synthesizes audio from the current (possibly just-edited) script.
  // Sends the script directly rather than relying on the debounced autosave
  // above having already landed, so this is correct regardless of timing.
  const regenerate = async () => {
    if (timer.current) clearTimeout(timer.current)
    setRegenerating(true)
    try {
      const res = await api.post<{ audio_path: string | null; duration_s: number | null }>(
        `/api/artifacts/${artifactId}/audio`,
        { script: { title: scriptTitle, turns } },
      )
      setAudioPath(res.audio_path)
      setDurationS(res.duration_s)
      setAudioVersion((v) => v + 1)
      setState('saved')
      toast.success('Audio regenerated')
    } catch (e) {
      // We cancelled the pending autosave above, but the POST failed before
      // the server persisted the script, so re-arm the save to avoid silently
      // losing the transcript edits. Use the latest turns (turnsRef), not the
      // closure's stale copy, so an edit made during synthesis is kept.
      persist(turnsRef.current)
      toast.error(`Regenerate failed: ${e instanceof Error ? e.message : e}`)
    } finally {
      setRegenerating(false)
    }
  }

  if (loadError) return <div className="p-8 text-[13px] text-madder">{loadError}</div>
  if (!artifact)
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="animate-spin text-ws-muted" />
      </div>
    )

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-ws-line px-5 py-2.5">
        <button
          onClick={() => navigate(`/n/${notebookId}`)}
          className="text-[12px] text-ws-muted hover:text-ws-ink"
        >
          ← Notebook
        </button>
        <Mic size={14} className="text-woad" />
        <span className="font-display text-[14px] font-semibold">
          {scriptTitle || 'Podcast'}
        </span>
        <span className="text-[12px] text-ws-muted">
          {state === 'saving' ? <span className="flex items-center gap-1"><Loader2 size={12} className="animate-spin" /> Saving…</span>
            : state === 'dirty' ? 'Unsaved' : <span className="flex items-center gap-1"><Check size={12} /> Saved</span>}
        </span>
      </div>

      <div className="mx-auto w-full max-w-2xl flex-1 overflow-auto px-6 py-6">
        {audioPath ? (
          <>
            <audio key={audioVersion} controls src={audioUrl} className="w-full">
              Your browser does not support audio playback.
            </audio>
            <div className="mt-2 flex items-center gap-2">
              {durationS ? <span className="text-[12px] text-ws-muted">{Math.round(durationS)}s</span> : null}
              <div className="ml-auto flex items-center gap-1.5">
                <button
                  onClick={downloadAudio}
                  className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-ws-line px-2.5 py-1.5 text-[12px] text-ws-muted hover:text-ws-ink"
                >
                  <Download size={12} /> Download
                </button>
                <Button variant="quiet" onClick={regenerate} disabled={regenerating} className="text-[12px]">
                  {regenerating ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Regenerate
                </Button>
              </div>
            </div>
          </>
        ) : (
          <Empty
            title="No audio yet"
            body="Install local TTS with pip install kokoro soundfile, or set a TTS provider in Settings, then regenerate. The transcript is ready below."
            action={
              <div className="flex items-center gap-2">
                <Button variant="accent" onClick={regenerate} disabled={regenerating}>
                  {regenerating ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Regenerate audio
                </Button>
                <Button variant="quiet" onClick={() => navigate('/settings')}>Settings</Button>
              </div>
            }
          />
        )}

        <Eyebrow className="mt-6">Transcript</Eyebrow>
        <ol className="mt-2 space-y-4">
          {turns.map((t, i) => (
            <li key={i} className="group flex gap-3">
              <button
                onClick={() => toggleSpeaker(i)}
                title={t.speaker === 'A' ? 'Host, click to change to guest' : 'Guest, click to change to host'}
                className={`mt-0.5 h-7 w-7 shrink-0 rounded-full border border-ws-line text-center text-[12px] font-semibold leading-7 ${
                  t.speaker === 'A' ? 'border-woad bg-woad text-white' : 'bg-ws-panel text-ws-ink'
                }`}
              >
                {t.speaker === 'A' ? 'H' : 'G'}
              </button>
              <textarea
                value={t.text}
                onChange={(e) => editTurn(i, e.target.value)}
                rows={2}
                className="min-w-0 flex-1 resize-y rounded-[var(--radius)] border border-transparent bg-transparent px-2 py-1 text-[14px] leading-relaxed outline-none hover:border-ws-line focus:border-woad focus:bg-ws-panel"
              />
              <button
                onClick={() => deleteTurn(i)}
                aria-label="Delete turn"
                title="Delete turn"
                className="mt-0.5 shrink-0 text-ws-muted opacity-0 hover:text-madder group-hover:opacity-100"
              >
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}
