/** Pure helpers for BuildView/WeaveProgress: normalizing every generation
 *  pipeline's SSE event shapes into one generic progress model, and pulling
 *  the source ids a landed deck slide actually cited (for WeaveProgress's
 *  knots). Kept framework-free so they're unit-testable without a DOM.
 *
 *  The pipelines (docloom_studio/generate.py) do not share one outline
 *  shape: deck emits {deck_title, slides:[{title,layout}]}, doc emits
 *  {doc_title, sections:[heading,...]}, a split-workbook sheet emits
 *  {title, sheets:[name,...]}. diagram/infographic/podcast never emit an
 *  outline at all (single LLM call, no per-unit loop). */

import type { Block, ListItem, RichText, SlideT } from './types'

export type WeaveUnitStatus = 'pending' | 'running' | 'done' | 'skipped'

export interface WeaveSource {
  id: string
  index: number
  enabled: boolean
}

export interface WeaveUnit {
  key: number
  label: string
  status: WeaveUnitStatus
  citedSourceIds?: string[]
}

export interface NormalizedOutline {
  title: string
  items: string[]
  /** true only for the deck shape (slides:[{title,layout}]); doc/sheet
   *  outlines carry plain heading/name strings, not slide-shaped content, so
   *  BuildView knows not to expect a ScaledSlide preview for those units. */
  slideShaped: boolean
}

export function normalizeOutline(data: unknown): NormalizedOutline | null {
  if (!data || typeof data !== 'object') return null
  const d = data as Record<string, unknown>
  const title = [d.deck_title, d.doc_title, d.title].find((v) => typeof v === 'string') as
    | string
    | undefined

  if (Array.isArray(d.slides)) {
    const items = (d.slides as { title?: unknown }[]).map((s) =>
      typeof s?.title === 'string' ? s.title : '',
    )
    return { title: title ?? '', items, slideShaped: true }
  }
  if (Array.isArray(d.sections)) {
    return { title: title ?? '', items: d.sections.filter((s) => typeof s === 'string'), slideShaped: false }
  }
  if (Array.isArray(d.sheets)) {
    return { title: title ?? '', items: d.sheets.filter((s) => typeof s === 'string'), slideShaped: false }
  }
  if (title != null) return { title, items: [], slideShaped: false }
  return null
}

function citesInRichText(rt: RichText | undefined, ids: Set<string>): void {
  if (!rt || typeof rt === 'string') return
  for (const span of rt) if (span?.cite) ids.add(span.cite)
}

function citesInBlocks(blocks: Block[] | undefined, ids: Set<string>): void {
  for (const b of blocks ?? []) {
    citesInRichText(b.text, ids)
    for (const item of (b.items ?? []) as ListItem[]) citesInRichText(item?.text, ids)
    for (const h of b.header ?? []) citesInRichText(h, ids)
    for (const row of b.rows ?? []) for (const cell of row) citesInRichText(cell, ids)
  }
}

/** Every source id a slide's spans cite (blocks and the right column),
 *  deduped in first-seen order, for knotting WeaveProgress's weft at the
 *  warp threads a landed slide actually drew from. */
export function citesOf(slide: SlideT): string[] {
  const ids = new Set<string>()
  citesInBlocks(slide.blocks, ids)
  citesInBlocks(slide.right, ids)
  return [...ids]
}
