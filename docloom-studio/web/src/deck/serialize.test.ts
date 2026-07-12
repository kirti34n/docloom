import { describe, expect, it, vi } from 'vitest'
import {
  docToListItems,
  docToRichText,
  inlineToRichText,
  listItemsToDoc,
  richTextToDoc,
  richTextToInline,
} from './serialize'
import type { PMNode, PMText } from './serialize'
import { padded } from './RichBlockEditor'
import { deckHistory, useDeck } from './deckStore'
import type { ArtifactT, RichText } from './types'

describe('richtext ↔ prosemirror', () => {
  it('plain string round-trips and collapses back to a string', () => {
    const doc = richTextToDoc('hello world')
    expect(docToRichText(doc)).toBe('hello world')
  })

  it('preserves bold/italic/code/link/cite marks', () => {
    const rt: RichText = [
      { text: 'a', bold: true },
      { text: 'b', italic: true, code: true },
      { text: 'c', link: 'https://x.com' },
      { text: 'd', cite: 'src-1' },
    ]
    expect(docToRichText(richTextToDoc(rt))).toEqual(rt)
  })

  it('merges adjacent spans with identical formatting', () => {
    const inline = [
      { type: 'text' as const, text: 'foo', marks: [{ type: 'bold' }] },
      { type: 'text' as const, text: 'bar', marks: [{ type: 'bold' }] },
    ]
    expect(inlineToRichText(inline)).toEqual([{ text: 'foobar', bold: true }])
  })

  it('drops empty text nodes', () => {
    expect(richTextToInline([{ text: '' }, { text: 'x' }])).toEqual([
      { type: 'text', text: 'x' },
    ])
    expect(docToRichText(richTextToDoc(''))).toBe('')
  })

  it('collapses a single unformatted span to a bare string', () => {
    expect(inlineToRichText([{ type: 'text', text: 'solo' }])).toBe('solo')
  })
})

describe('lists ↔ prosemirror', () => {
  it('round-trips flat bullet list', () => {
    const items = [{ text: 'one', level: 0 }, { text: 'two', level: 0 }]
    expect(docToListItems(listItemsToDoc(items, false))).toEqual(items)
  })

  it('round-trips nested levels', () => {
    const items = [
      { text: 'top', level: 0 },
      { text: 'child', level: 1 },
      { text: 'grandchild', level: 2 },
      { text: 'back', level: 0 },
    ]
    expect(docToListItems(listItemsToDoc(items, false))).toEqual(items)
  })

  it('clamps a level jump (0 → 3 becomes 0 → 1)', () => {
    const items = [{ text: 'a', level: 0 }, { text: 'b', level: 3 }]
    const out = docToListItems(listItemsToDoc(items, false))
    expect(out.map((i) => i.level)).toEqual([0, 1])
  })

  it('keeps marks inside list items', () => {
    const items = [{ text: [{ text: 'bold', bold: true }] as RichText, level: 0 }]
    expect(docToListItems(listItemsToDoc(items, true))).toEqual(items)
  })
})

describe('hardBreak <-> newline', () => {
  it('maps a hardBreak node to a newline instead of merging the surrounding text', () => {
    const inline: (PMText | PMNode)[] = [
      { type: 'text', text: 'foo' },
      { type: 'hardBreak' },
      { type: 'text', text: 'bar' },
    ]
    expect(inlineToRichText(inline)).toBe('foo\nbar')
  })

  it('splits an embedded newline into a hardBreak node', () => {
    expect(richTextToInline('foo\nbar')).toEqual([
      { type: 'text', text: 'foo' },
      { type: 'hardBreak' },
      { type: 'text', text: 'bar' },
    ])
  })

  it('round-trips a manual line break through richText <-> prosemirror', () => {
    expect(docToRichText(richTextToDoc('foo\nbar'))).toBe('foo\nbar')
  })
})

describe('table editor: ragged row padding', () => {
  it('pads a short row so a write past its end leaves no hole', () => {
    const row = padded(['a'], 4)
    row[3] = 'x'
    expect(row).toEqual(['a', '', '', 'x'])
    // a hole (sparse slot) serializes to null, which docloom rejects
    expect(JSON.stringify(row)).not.toContain('null')
  })

  it('is a no-op once the row already has n cells', () => {
    expect(padded(['a', 'b'], 2)).toEqual(['a', 'b'])
  })
})

describe('deck store: undo-after-load guard', () => {
  it('load() does not leave the pre-load empty state on the undo stack', () => {
    vi.useFakeTimers()
    try {
      const artifact: ArtifactT = {
        id: 'a1',
        notebook_id: 'n1',
        kind: 'deck',
        title: 'Test deck',
        version: 1,
        payload: {
          ir: { title: 'Loaded title', slides: [{ layout: 'title', title: 'Hello' }] },
          theme_name: 'paper',
        },
      }

      useDeck.getState().load(artifact)
      expect(deckHistory.canUndo()).toBe(false)
      expect(useDeck.getState().title).toBe('Loaded title')

      // a real edit after load should be undoable, and undo must land back
      // on the loaded content, not the pre-load empty state
      useDeck.getState().setMeta({ title: 'Edited title' })
      expect(deckHistory.canUndo()).toBe(true)

      deckHistory.undo()
      expect(useDeck.getState().title).toBe('Loaded title')
    } finally {
      vi.useRealTimers()
    }
  })
})
