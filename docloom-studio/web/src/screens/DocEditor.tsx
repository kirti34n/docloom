import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Check, Download, GripVertical, Loader2, Plus, Redo2, Undo2 } from 'lucide-react'
import { api } from '../api/client'
import { toast } from '../ui/toast'
import type { ArtifactT } from '../deck/types'
import { themeVars } from '../deck/types'
import { EditableBlock } from '../deck/EditableBlock'
import { useThemes, themeByName } from '../deck/useThemes'
import { useDoc, docHistory } from '../doc/docStore'
import '../deck/stage.css'
import '../deck/editor.css'
import './doc.css'

const EXPORTS = ['docx', 'pdf', 'html', 'md'] as const
const ADD = [['paragraph', 'Text'], ['heading', 'Heading'], ['bullets', 'Bullets'],
  ['numbered', 'Numbered'], ['quote', 'Quote'], ['callout', 'Callout']] as const

const SEVERITY_CLASS: Record<string, string> = {
  error: 'text-madder',
  warning: 'text-ws-warn',
  info: 'text-ws-muted',
}

// The export route 422s with detail: { findings: [...] } when the document
// has blocking lint errors. api/client.ts only knows how to flatten a string
// or array `detail`, so that object shape falls through to the RAW response
// text: without unpacking it here, the user sees the literal JSON body.
function exportErrorMessage(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e)
  try {
    const parsed = JSON.parse(raw) as { detail?: { findings?: { message: string }[] } }
    const findings = parsed.detail?.findings
    if (findings?.length) {
      const n = findings.length
      return `Export failed: fix ${n} issue${n > 1 ? 's' : ''} first: ${findings.map((f) => f.message).join('; ')}`
    }
  } catch {
    // not a findings payload: raw is already a clean message from the API client
  }
  return `Export failed: ${raw}`
}

function SortableBlock({ id }: { id: string }) {
  const block = useDoc((s) => s.blocks[id])
  const update = useDoc((s) => s.updateBlock)
  const remove = useDoc((s) => s.removeBlock)
  const editorRev = useDoc((s) => s.editorRev)
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id })
  if (!block) return null
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.5 : 1 }}
      className="doc-block group"
    >
      <span className="doc-grip" {...attributes} {...listeners}>
        <GripVertical size={15} />
      </span>
      <div className="min-w-0 flex-1">
        <EditableBlock block={block} extRev={editorRev} onChange={(b) => update(id, b)} onDelete={() => remove(id)} />
      </div>
    </div>
  )
}

export function DocEditor() {
  const { artifactId, notebookId } = useParams()
  const navigate = useNavigate()
  const themes = useThemes()
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [exporting, setExporting] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const load = useDoc((s) => s.load)
  const title = useDoc((s) => s.title)
  const setTitle = useDoc((s) => s.setTitle)
  const order = useDoc((s) => s.order)
  const reorder = useDoc((s) => s.reorder)
  const addBlock = useDoc((s) => s.addBlock)
  const themeName = useDoc((s) => s.themeName)
  const saving = useDoc((s) => s.saving)
  const dirty = useDoc((s) => s.dirty)
  const findings = useDoc((s) => s.findings)
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  useEffect(() => {
    if (!artifactId) return
    api.get<ArtifactT>(`/api/artifacts/${artifactId}`).then((a) => {
      load(a)
      setLoaded(true)
    }).catch((e) => setLoadError(e instanceof Error ? e.message : String(e)))
  }, [artifactId, load])

  if (loadError)
    return <div className="flex h-full items-center justify-center text-madder text-[13px]">{loadError}</div>

  if (!loaded || themes.length === 0)
    return <div className="flex h-full items-center justify-center text-ws-muted"><Loader2 className="animate-spin" /></div>

  const theme = themeByName(themes, themeName === 'slate' || themeName === 'pulse' ? 'paper' : themeName)!
  const vars = themeVars(theme) as React.CSSProperties
  const errorCount = findings.filter((f) => f.severity === 'error').length

  const onDragEnd = (e: DragEndEvent) => {
    const { active, over } = e
    if (!over || active.id === over.id) return
    const next = [...order]
    next.splice(order.indexOf(String(over.id)), 0, next.splice(order.indexOf(String(active.id)), 1)[0])
    reorder(next)
  }

  const exportAs = async (fmt: string) => {
    setExporting(fmt)
    try {
      const res = await api.post<{ url: string; filename: string }>(`/api/artifacts/${artifactId}/export`, { format: fmt })
      const a = document.createElement('a')
      a.href = res.url
      a.download = res.filename
      a.click()
    } catch (e) {
      toast.error(exportErrorMessage(e))
    } finally {
      setExporting(null)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-ws-line px-5 py-2.5">
        <button onClick={() => navigate(`/n/${notebookId}`)} className="text-[12px] text-ws-muted hover:text-ws-ink">← Notebook</button>
        <div className="flex items-center gap-1">
          <button onClick={docHistory.undo} className="rounded-[var(--radius-sm)] p-1.5 text-ws-muted hover:text-ws-ink" title="Undo"><Undo2 size={15} /></button>
          <button onClick={docHistory.redo} className="rounded-[var(--radius-sm)] p-1.5 text-ws-muted hover:text-ws-ink" title="Redo"><Redo2 size={15} /></button>
        </div>
        <span className="text-[12px] text-ws-muted">
          {saving ? <span className="flex items-center gap-1"><Loader2 size={12} className="animate-spin" /> Saving…</span>
            : dirty ? 'Unsaved' : <span className="flex items-center gap-1"><Check size={12} /> Saved</span>}
        </span>
        {errorCount > 0 && (
          <span className="rounded-[var(--radius-sm)] bg-madder/10 px-2 py-0.5 text-[11px] font-medium text-madder">
            {errorCount} issue{errorCount > 1 ? 's' : ''}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {EXPORTS.map((fmt) => (
            <button key={fmt} onClick={() => exportAs(fmt)} disabled={exporting !== null}
              className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-ws-line px-2.5 py-1.5 text-[12px] text-ws-muted hover:text-ws-ink disabled:opacity-40">
              {exporting === fmt ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
              {fmt.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {findings.length > 0 && (
        <ul className="flex flex-col gap-1 border-b border-ws-line bg-ws-panel px-5 py-2">
          {findings.map((f, i) => (
            <li key={i} className={`text-[12px] ${SEVERITY_CLASS[f.severity] ?? 'text-ws-muted'}`}>
              {f.message}
            </li>
          ))}
        </ul>
      )}

      <div className="flex-1 overflow-auto bg-ws-bg py-10">
        <div className="mx-auto max-w-3xl rounded-[var(--radius)] bg-white px-14 py-12 shadow-[var(--shadow-panel)]" style={vars}>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Untitled document"
            className="mb-6 w-full border-none bg-transparent font-[var(--font-heading)] text-3xl font-bold text-[var(--text)] outline-none"
          />
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
            <SortableContext items={order} strategy={verticalListSortingStrategy}>
              <div className="flex flex-col gap-3">
                {order.map((id) => <SortableBlock key={id} id={id} />)}
              </div>
            </SortableContext>
          </DndContext>

          <div className="mt-4">
            {addOpen ? (
              <div className="add-block-menu">
                {ADD.map(([type, label]) => (
                  <button key={type} onClick={() => { addBlock(order[order.length - 1] ?? null, type); setAddOpen(false) }}>{label}</button>
                ))}
              </div>
            ) : (
              <button className="add-block-btn" onClick={() => setAddOpen(true)}><Plus size={14} /> Add block</button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
