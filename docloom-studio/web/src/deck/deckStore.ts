/** The deck editing store: normalized slides, structural mutations, debounced
 *  autosave, and structural undo (zundo). Tiptap owns keystroke-level undo per
 *  region; this store owns block/slide structure. */

import { create } from 'zustand'
import { temporal } from 'zundo'
import { api } from '../api/client'
import type { ArtifactT, Block, DeckPayload, DocumentT, Finding, SlideT, SourceT } from './types'

let uid = 0
const key = () => `b${Date.now().toString(36)}${(uid++).toString(36)}`

interface DeckState {
  artifactId: string | null
  title: string
  subtitle: string | null
  authors: string[]
  date: string | null
  slides: Record<string, SlideT>
  order: string[]
  sources: SourceT[]
  themeName: string
  rev: number
  editorRev: number // bumped on external content changes (load/undo/redo) so Tiptap regions resync
  findings: Finding[]
  selected: string | null
  saving: boolean
  dirty: boolean

  load: (a: ArtifactT) => void
  select: (slideId: string | null) => void
  setMeta: (patch: Partial<Pick<DeckState, 'title' | 'subtitle' | 'authors' | 'date'>>) => void
  setTheme: (name: string) => void
  updateSlide: (slideId: string, patch: Partial<SlideT>) => void
  updateBlock: (slideId: string, blockId: string, block: Block) => void
  setBlocks: (slideId: string, column: 'blocks' | 'right', blocks: Block[]) => void
  addBlock: (slideId: string, column: 'blocks' | 'right', type: string) => void
  removeBlock: (slideId: string, blockId: string) => void
  insertSlide: (afterId: string | null, layout?: string) => string
  duplicateSlide: (slideId: string) => void
  removeSlide: (slideId: string) => void
  reorder: (order: string[]) => void
  toDocument: () => DocumentT
}

export function newBlock(type: string): Block {
  const id = key()
  switch (type) {
    case 'heading':
      return { type: 'heading', id, level: 2, text: 'Heading' }
    case 'bullets':
      return { type: 'bullets', id, items: [{ text: 'New point' }] }
    case 'numbered':
      return { type: 'numbered', id, items: [{ text: 'First step' }] }
    case 'quote':
      return { type: 'quote', id, text: 'A memorable line.' }
    case 'callout':
      return { type: 'callout', id, style: 'info', text: 'Note.' }
    case 'table':
      return { type: 'table', id, header: ['', ''], rows: [['', '']] }
    case 'stats':
      return { type: 'stats', id, items: [{ label: 'Metric', value: '0' }] }
    case 'chart':
      return {
        type: 'chart', id, chart: 'column',
        labels: ['P1', 'P2'], series: [{ name: 'Series 1', values: [0, 0] }],
      }
    case 'image':
      return { type: 'image', id }
    case 'code':
      return { type: 'code', id, code: '' }
    default:
      return { type: 'paragraph', id, text: 'New paragraph.' }
  }
}

function withIds(slide: SlideT): SlideT {
  const s = { ...slide, id: slide.id ?? key() }
  const tag = (b: Block): Block => ({ ...b, id: b.id ?? key() })
  s.blocks = (s.blocks ?? []).map(tag)
  s.right = (s.right ?? []).map(tag)
  return s
}

export const useDeck = create<DeckState>()(
  temporal(
    (set, get) => ({
      artifactId: null,
      title: '',
      subtitle: null,
      authors: [],
      date: null,
      slides: {},
      order: [],
      sources: [],
      themeName: 'paper',
      rev: 0,
      editorRev: 0,
      findings: [],
      selected: null,
      saving: false,
      dirty: false,

      load: (a) => {
        const doc = a.payload.ir
        const slides: Record<string, SlideT> = {}
        const order: string[] = []
        for (const raw of doc.slides ?? []) {
          const s = withIds(raw)
          slides[s.id!] = s
          order.push(s.id!)
        }
        // load() is not a user edit: pause temporal recording so the pre-load
        // state (title '', slides {}) never lands on pastStates, then clear
        // any history left over from a previously loaded artifact. The
        // try/finally keeps recording from getting stuck paused if set()
        // ever throws on malformed data.
        const hist = useDeck.temporal.getState()
        hist.pause()
        try {
          set({
            artifactId: a.id,
            title: doc.title,
            subtitle: doc.subtitle ?? null,
            authors: doc.authors ?? [],
            date: doc.date ?? null,
            slides,
            order,
            sources: doc.sources ?? [],
            themeName: a.payload.theme_name,
            rev: a.version,
            editorRev: get().editorRev + 1,
            selected: order[0] ?? null,
            dirty: false,
          })
          hist.clear()
        } finally {
          hist.resume()
        }
      },

      select: (slideId) => set({ selected: slideId }),

      setMeta: (patch) => {
        set({ ...patch, dirty: true })
        void save()
      },

      setTheme: (name) => {
        set({ themeName: name, dirty: true })
        void save()
      },

      updateSlide: (slideId, patch) => {
        const slide = get().slides[slideId]
        if (!slide) return
        set({ slides: { ...get().slides, [slideId]: { ...slide, ...patch } }, dirty: true })
        void save()
      },

      updateBlock: (slideId, blockId, block) => {
        const slide = get().slides[slideId]
        if (!slide) return
        const replace = (list: Block[] | undefined) =>
          (list ?? []).map((b) => (b.id === blockId ? { ...block, id: blockId } : b))
        set({
          slides: {
            ...get().slides,
            [slideId]: { ...slide, blocks: replace(slide.blocks), right: replace(slide.right) },
          },
          dirty: true,
        })
        void save()
      },

      setBlocks: (slideId, column, blocks) => {
        const slide = get().slides[slideId]
        if (!slide) return
        set({ slides: { ...get().slides, [slideId]: { ...slide, [column]: blocks } }, dirty: true })
        void save()
      },

      addBlock: (slideId, column, type) => {
        const slide = get().slides[slideId]
        if (!slide) return
        const block = newBlock(type)
        const blocks = [...(slide[column] ?? []), block]
        set({ slides: { ...get().slides, [slideId]: { ...slide, [column]: blocks } }, dirty: true })
        void save()
      },

      removeBlock: (slideId, blockId) => {
        const slide = get().slides[slideId]
        if (!slide) return
        const drop = (list?: Block[]) => (list ?? []).filter((b) => b.id !== blockId)
        set({
          slides: {
            ...get().slides,
            [slideId]: { ...slide, blocks: drop(slide.blocks), right: drop(slide.right) },
          },
          dirty: true,
        })
        void save()
      },

      insertSlide: (afterId, layout = 'content') => {
        const id = key()
        const slide: SlideT = { id, layout, title: 'New slide', blocks: [] }
        const order = [...get().order]
        const at = afterId ? order.indexOf(afterId) + 1 : order.length
        order.splice(at, 0, id)
        set({ slides: { ...get().slides, [id]: slide }, order, selected: id, dirty: true })
        void save()
        return id
      },

      duplicateSlide: (slideId) => {
        const slide = get().slides[slideId]
        if (!slide) return
        const copy = withIds({ ...slide, id: undefined })
        const order = [...get().order]
        order.splice(order.indexOf(slideId) + 1, 0, copy.id!)
        set({ slides: { ...get().slides, [copy.id!]: copy }, order, selected: copy.id!, dirty: true })
        void save()
      },

      removeSlide: (slideId) => {
        const order = get().order.filter((id) => id !== slideId)
        const slides = { ...get().slides }
        delete slides[slideId]
        const idx = get().order.indexOf(slideId)
        set({
          slides,
          order,
          selected: order[Math.min(idx, order.length - 1)] ?? null,
          dirty: true,
        })
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
          subtitle: s.subtitle,
          authors: s.authors,
          date: s.date,
          slides: s.order.map((id) => s.slides[id]),
          sources: s.sources,
        }
      },
    }),
    {
      limit: 100,
      // only structural/content state is undoable (not selection/saving flags)
      partialize: (s) => ({
        title: s.title,
        subtitle: s.subtitle,
        authors: s.authors,
        date: s.date,
        slides: s.slides,
        order: s.order,
        themeName: s.themeName,
      }),
      // without this, zundo pushes a history entry on every set() even when
      // the partialized slice did not change (select(), the autosave
      // callback), filling the undo stack with no-op steps
      equality: (a, b) => JSON.stringify(a) === JSON.stringify(b),
    },
  ),
)

// ---- debounced autosave (full-IR PUT; the deck is small) ------------------

let saveTimer: ReturnType<typeof setTimeout> | null = null
let saveSeq = 0

function save(): void {
  const mySeq = ++saveSeq
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(async () => {
    const s = useDeck.getState()
    if (!s.artifactId || !s.dirty) return
    useDeck.setState({ saving: true })
    try {
      const payload: DeckPayload = {
        ir: s.toDocument(),
        theme_name: s.themeName,
        brand_kit_id: null,
      }
      const res = await api.put<{ version: number; findings: Finding[] }>(
        `/api/artifacts/${s.artifactId}/ir`,
        { payload },
      )
      // another save() ran while this PUT was in flight (saveSeq moved on):
      // its edits are not in this payload, so leave dirty alone and let its
      // own already-scheduled timer send them
      const clean = saveSeq === mySeq
      useDeck.setState({
        rev: res.version,
        findings: res.findings,
        saving: false,
        ...(clean ? { dirty: false } : {}),
      })
    } catch {
      useDeck.setState({ saving: false })
    }
  }, 700)
}

/** Undo/redo helpers (structural). Persist the restored state. */
export const deckHistory = {
  undo: () => {
    useDeck.temporal.getState().undo()
    useDeck.setState({ dirty: true, editorRev: useDeck.getState().editorRev + 1 })
    save()
  },
  redo: () => {
    useDeck.temporal.getState().redo()
    useDeck.setState({ dirty: true, editorRev: useDeck.getState().editorRev + 1 })
    save()
  },
  canUndo: () => useDeck.temporal.getState().pastStates.length > 0,
  canRedo: () => useDeck.temporal.getState().futureStates.length > 0,
}
