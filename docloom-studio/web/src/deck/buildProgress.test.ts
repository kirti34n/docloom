import { describe, expect, it } from 'vitest'
import { citesOf, normalizeOutline } from './buildProgress'
import type { SlideT } from './types'

describe('normalizeOutline', () => {
  it('reads the deck shape (deck_title + slides[{title,layout}])', () => {
    const out = normalizeOutline({
      deck_title: 'Q3 review',
      slides: [
        { title: 'Intro', layout: 'section' },
        { title: 'Numbers', layout: 'content' },
      ],
    })
    expect(out).toEqual({ title: 'Q3 review', items: ['Intro', 'Numbers'], slideShaped: true })
  })

  it('reads the doc shape (doc_title + sections[string]) without crashing on it', () => {
    const out = normalizeOutline({ doc_title: 'Market memo', sections: ['Summary', 'Risks'] })
    expect(out).toEqual({ title: 'Market memo', items: ['Summary', 'Risks'], slideShaped: false })
  })

  it('reads the split-workbook sheet shape (title + sheets[string])', () => {
    const out = normalizeOutline({ title: 'Budget', sheets: ['Revenue', 'Costs'] })
    expect(out).toEqual({ title: 'Budget', items: ['Revenue', 'Costs'], slideShaped: false })
  })

  it('falls back to an empty item list for a title-only payload', () => {
    expect(normalizeOutline({ title: 'Diagram' })).toEqual({
      title: 'Diagram',
      items: [],
      slideShaped: false,
    })
  })

  it('returns null for a payload with neither a title nor an item list', () => {
    expect(normalizeOutline({ foo: 'bar' })).toBeNull()
    expect(normalizeOutline(null)).toBeNull()
    expect(normalizeOutline('nope')).toBeNull()
    expect(normalizeOutline(42)).toBeNull()
  })
})

describe('citesOf', () => {
  it('collects cite ids from paragraph and bullet spans, deduped in first-seen order', () => {
    const slide: SlideT = {
      layout: 'content',
      blocks: [
        { type: 'paragraph', text: [{ text: 'a', cite: 'src-1' }, { text: 'b', cite: 'src-2' }] },
        { type: 'bullets', items: [{ text: [{ text: 'c', cite: 'src-1' }] }] },
      ],
    }
    expect(citesOf(slide)).toEqual(['src-1', 'src-2'])
  })

  it('collects cites from table headers and cells, and the right column', () => {
    const slide: SlideT = {
      layout: 'two_column',
      blocks: [
        {
          type: 'table',
          header: [[{ text: 'h', cite: 'src-3' }]],
          rows: [[[{ text: 'r', cite: 'src-4' }]]],
        },
      ],
      right: [{ type: 'paragraph', text: [{ text: 'x', cite: 'src-5' }] }],
    }
    expect(citesOf(slide)).toEqual(['src-3', 'src-4', 'src-5'])
  })

  it('ignores stat items (no text field) and plain-string text without throwing', () => {
    const slide: SlideT = {
      layout: 'content',
      blocks: [
        { type: 'stats', items: [{ label: 'Revenue', value: '$1.2M' }] },
        { type: 'paragraph', text: 'no cites here, just a plain string' },
      ],
    }
    expect(citesOf(slide)).toEqual([])
  })

  it('returns nothing for a slide with no cites', () => {
    const slide: SlideT = { layout: 'title', title: 'Cover' }
    expect(citesOf(slide)).toEqual([])
  })
})
