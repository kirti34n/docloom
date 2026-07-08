import { describe, expect, it } from 'vitest'
import {
  docToListItems,
  docToRichText,
  inlineToRichText,
  listItemsToDoc,
  richTextToDoc,
  richTextToInline,
} from './serialize'
import type { RichText } from './types'

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
