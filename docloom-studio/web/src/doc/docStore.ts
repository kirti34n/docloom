/** Document editing store: a flat block list over docloom Document.blocks,
 *  with debounced full-IR autosave. Simpler sibling of the deck store. */

import { create } from 'zustand'
import { temporal } from 'zundo'
import { api } from '../api/client'
import type { ArtifactT, Block, DocumentT, Finding, SourceT } from '../deck/types'
import { newBlock } from '../deck/deckStore'

let uid = 0
const key = () => `d${Date.now().toString(36)}${(uid++).toString(36)}`

interface DocState {
  artifactId: string | null
  title: string
  blocks: Record<string, Block>
  order: string[]
  sheets: unknown[]
  sources: SourceT[]
  themeName: string
  rev: number
  // bumped on load/undo/redo so the Tiptap block editors resync their content
  // from the store (they only re-read external content when this changes)
  editorRev: number
  findings: Finding[]
  saving: boolean
  dirty: boolean

  load: (a: ArtifactT) => void
  setTitle: (title: string) => void
  updateBlock: (id: string, block: Block) => void
  addBlock: (afterId: string | null, type: string) => void
  removeBlock: (id: string) => void
  reorder: (order: string[]) => void
  toDocument: () => DocumentT
}

export const useDoc = create<DocState>()(
  temporal(
    (set, get) => ({
      artifactId: null,
      title: '',
      blocks: {},
      order: [],
      sheets: [],
      sources: [],
      themeName: 'paper',
      rev: 0,
      editorRev: 0,
      findings: [],
      saving: false,
      dirty: false,

      load: (a) => {
        const doc = a.payload.ir
        const blocks: Record<string, Block> = {}
        const order: string[] = []
        for (const raw of doc.blocks ?? []) {
          const b = { ...raw, id: raw.id ?? key() }
          blocks[b.id!] = b
          order.push(b.id!)
        }
        set({
          artifactId: a.id,
          title: doc.title,
          blocks,
          order,
          sheets: (doc as { sheets?: unknown[] }).sheets ?? [],
          sources: doc.sources ?? [],
          themeName: a.payload.theme_name,
          rev: a.version,
          editorRev: get().editorRev + 1,
          dirty: false,
        })
        // load() is itself a recorded set(): whatever was on the store before
        // (an empty store, or a previously opened artifact) would otherwise
        // sit on pastStates and one Ctrl+Z would wipe or cross-contaminate
        // this document. Wipe the history so undo only ever sees edits made
        // to THIS artifact.
        useDoc.temporal.getState().clear()
      },

      setTitle: (title) => {
        set({ title, dirty: true })
        void save()
      },

      updateBlock: (id, block) => {
        set({ blocks: { ...get().blocks, [id]: { ...block, id } }, dirty: true })
        void save()
      },

      addBlock: (afterId, type) => {
        const b = newBlock(type)
        b.id = key()
        const order = [...get().order]
        order.splice(afterId ? order.indexOf(afterId) + 1 : order.length, 0, b.id!)
        set({ blocks: { ...get().blocks, [b.id!]: b }, order, dirty: true })
        void save()
      },

      removeBlock: (id) => {
        const blocks = { ...get().blocks }
        delete blocks[id]
        set({ blocks, order: get().order.filter((x) => x !== id), dirty: true })
        void save()
      },

      reorder: (order) => {
        set({ order, dirty: true })
        void save()
      },

      toDocument: () => {
        const s = get()
        return {
          title: s.title,
          blocks: s.order.map((id) => s.blocks[id]),
          sheets: s.sheets as never,
          sources: s.sources,
        }
      },
    }),
    {
      limit: 100,
      partialize: (s) => ({ title: s.title, blocks: s.blocks, order: s.order }),
      // without this, every autosave setState (saving/rev/findings, none
      // partialized) pushes a duplicate history entry, so Undo appears to do
      // nothing until it has walked past all the no-ops.
      equality: (a, b) => JSON.stringify(a) === JSON.stringify(b),
    },
  ),
)

let saveTimer: ReturnType<typeof setTimeout> | null = null

function save(): void {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(async () => {
    const s = useDoc.getState()
    if (!s.artifactId || !s.dirty) return
    useDoc.setState({ saving: true })
    try {
      const res = await api.put<{ version: number; findings: Finding[] }>(
        `/api/artifacts/${s.artifactId}/ir`,
        { payload: { ir: s.toDocument(), theme_name: s.themeName, brand_kit_id: null } },
      )
      useDoc.setState({ rev: res.version, findings: res.findings, dirty: false, saving: false })
    } catch {
      useDoc.setState({ saving: false })
    }
  }, 700)
}

export const docHistory = {
  undo: () => {
    useDoc.temporal.getState().undo()
    useDoc.setState((s) => ({ dirty: true, editorRev: s.editorRev + 1 }))
    save()
  },
  redo: () => {
    useDoc.temporal.getState().redo()
    useDoc.setState((s) => ({ dirty: true, editorRev: s.editorRev + 1 }))
    save()
  },
}
