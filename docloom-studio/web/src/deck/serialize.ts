/** Pure, testable RichText ↔ ProseMirror-JSON serializers.
 *  Independent of any editor instance — the Tiptap regions round-trip through
 *  these so the docloom IR stays the single source of truth. */

import type { ListItem, RichText, Span } from './types'

export interface PMMark {
  type: string
  attrs?: Record<string, unknown>
}
export interface PMText {
  type: 'text'
  text: string
  marks?: PMMark[]
}
export interface PMNode {
  type: string
  attrs?: Record<string, unknown>
  content?: PMNode[]
}

function spanToMarks(s: Span): PMMark[] {
  const marks: PMMark[] = []
  if (s.bold) marks.push({ type: 'bold' })
  if (s.italic) marks.push({ type: 'italic' })
  if (s.code) marks.push({ type: 'code' })
  if (s.link) marks.push({ type: 'link', attrs: { href: s.link } })
  if (s.cite) marks.push({ type: 'cite', attrs: { sourceId: s.cite } })
  return marks
}

function spansOf(rt: RichText): Span[] {
  if (typeof rt === 'string') return rt ? [{ text: rt }] : []
  return rt
}

/** RichText → inline PM nodes (empty array for empty text). An embedded
 *  '\n' (a Shift+Enter break read back by inlineToRichText) becomes a
 *  hardBreak node so the visual break survives a reload, not just the data. */
export function richTextToInline(rt: RichText): (PMText | PMNode)[] {
  const out: (PMText | PMNode)[] = []
  for (const s of spansOf(rt)) {
    const marks = spanToMarks(s)
    const lines = s.text.split('\n')
    lines.forEach((line, i) => {
      if (i > 0) out.push({ type: 'hardBreak' })
      if (line.length > 0) out.push(marks.length ? { type: 'text', text: line, marks } : { type: 'text', text: line })
    })
  }
  return out
}

/** RichText → a single-paragraph PM document (for a text region). */
export function richTextToDoc(rt: RichText): PMNode {
  const inline = richTextToInline(rt)
  return { type: 'doc', content: [{ type: 'paragraph', content: inline }] }
}

function markToSpanFields(marks: PMMark[] | undefined): Partial<Span> {
  const out: Partial<Span> = {}
  for (const m of marks ?? []) {
    if (m.type === 'bold') out.bold = true
    else if (m.type === 'italic') out.italic = true
    else if (m.type === 'code') out.code = true
    else if (m.type === 'link') out.link = String(m.attrs?.href ?? '')
    else if (m.type === 'cite') out.cite = String(m.attrs?.sourceId ?? '')
  }
  return out
}

function sameFormat(a: Span, b: Span): boolean {
  return (
    !!a.bold === !!b.bold &&
    !!a.italic === !!b.italic &&
    !!a.code === !!b.code &&
    (a.link ?? '') === (b.link ?? '') &&
    (a.cite ?? '') === (b.cite ?? '')
  )
}

/** Collect PM text nodes (from a paragraph or inline array) into canonical
 *  RichText: merge adjacent same-format spans, collapse a lone plain span to
 *  a string (matches how LLMs and hand-authored IR look). A hardBreak node
 *  (Shift+Enter) becomes a literal newline instead of being dropped, which
 *  would otherwise silently merge the words on either side of it. */
export function inlineToRichText(nodes: (PMText | PMNode)[]): RichText {
  const spans: Span[] = []
  const push = (text: string, marks?: PMMark[]) => {
    if (!text) return
    const span: Span = { text, ...markToSpanFields(marks) }
    const last = spans[spans.length - 1]
    if (last && sameFormat(last, span)) last.text += span.text
    else spans.push(span)
  }
  for (const node of nodes) {
    if (node.type === 'hardBreak') push('\n')
    else if (node.type === 'text') push((node as PMText).text, (node as PMText).marks)
  }
  if (spans.length === 0) return ''
  if (spans.length === 1) {
    const only = spans[0]
    const { text, ...fmt } = only
    if (Object.keys(fmt).length === 0) return text
  }
  return spans
}

/** PM document → RichText (reads the first paragraph's inline content). */
export function docToRichText(doc: PMNode): RichText {
  const para = (doc.content ?? []).find((n) => n.type === 'paragraph')
  return inlineToRichText((para?.content as PMText[]) ?? [])
}

/** List items → a PM bulletList/orderedList doc, nesting by `level`. */
export function listItemsToDoc(items: ListItem[], ordered: boolean): PMNode {
  const listType = ordered ? 'orderedList' : 'bulletList'
  // build nested lists using a stack keyed by clamped level
  const root: PMNode = { type: listType, content: [] }
  const stack: PMNode[] = [root]
  let prevLevel = 0
  for (const item of items) {
    const level = Math.min(Math.max(item.level ?? 0, 0), 4)
    const clamped = Math.min(level, prevLevel + 1)
    while (stack.length - 1 > clamped) stack.pop()
    while (stack.length - 1 < clamped) {
      const parentItems = stack[stack.length - 1].content!
      let host = parentItems[parentItems.length - 1]
      if (!host) {
        host = { type: 'listItem', content: [] }
        parentItems.push(host)
      }
      const sub: PMNode = { type: listType, content: [] }
      host.content!.push(sub)
      stack.push(sub)
    }
    stack[stack.length - 1].content!.push({
      type: 'listItem',
      content: [{ type: 'paragraph', content: richTextToInline(item.text) }],
    })
    prevLevel = clamped
  }
  return { type: 'doc', content: [root] }
}

/** PM list doc → list items with `level` from nesting depth. */
export function docToListItems(doc: PMNode): ListItem[] {
  const items: ListItem[] = []
  const walk = (list: PMNode, level: number) => {
    for (const li of list.content ?? []) {
      if (li.type !== 'listItem') continue
      const para = (li.content ?? []).find((n) => n.type === 'paragraph')
      items.push({
        text: inlineToRichText((para?.content as PMText[]) ?? []),
        level,
      })
      for (const child of li.content ?? []) {
        if (child.type === 'bulletList' || child.type === 'orderedList')
          walk(child, level + 1)
      }
    }
  }
  const root = (doc.content ?? [])[0]
  if (root) walk(root, 0)
  return items
}
