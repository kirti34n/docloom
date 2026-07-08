import { Plus } from 'lucide-react'
import { useState } from 'react'
import type { Block, DocumentT, RichText, SlideT } from './types'
import { plain } from './RichText'
import { EditableText } from './Editable'
import { EditableBlock } from './EditableBlock'
import { useDeck } from './deckStore'

const ADD_TYPES = [
  ['paragraph', 'Text'],
  ['heading', 'Heading'],
  ['bullets', 'Bullets'],
  ['quote', 'Quote'],
  ['callout', 'Callout'],
] as const

function citeMap(doc: Pick<DocumentT, 'sources'>): Map<string, number> {
  const m = new Map<string, number>()
  ;(doc.sources ?? []).forEach((s, i) => !m.has(s.id) && m.set(s.id, i + 1))
  return m
}

function asString(rt: RichText): string {
  return typeof rt === 'string' ? rt : plain(rt)
}

function AddBlock({ onAdd }: { onAdd: (type: string) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="add-block">
      {open ? (
        <div className="add-block-menu">
          {ADD_TYPES.map(([type, label]) => (
            <button key={type} onClick={() => { onAdd(type); setOpen(false) }}>
              {label}
            </button>
          ))}
        </div>
      ) : (
        <button className="add-block-btn" onClick={() => setOpen(true)}>
          <Plus size={14} /> Add block
        </button>
      )}
    </div>
  )
}

export function EditableSlide({ slideId }: { slideId: string }) {
  const slide = useDeck((s) => s.slides[slideId])
  const sources = useDeck((s) => s.sources)
  const updateSlide = useDeck((s) => s.updateSlide)
  const updateBlock = useDeck((s) => s.updateBlock)
  const addBlock = useDeck((s) => s.addBlock)
  const removeBlock = useDeck((s) => s.removeBlock)
  const cites = citeMap({ sources })

  if (!slide) return null
  const acc = slide.accent ? ({ '--slide-accent': slide.accent } as React.CSSProperties) : {}

  const titleField = (
    <EditableText
      className="edit-title"
      value={slide.title ?? ''}
      placeholder="Slide title"
      onChange={(rt) => updateSlide(slideId, { title: asString(rt) })}
    />
  )

  const blockList = (column: 'blocks' | 'right') => (
    <>
      {(slide[column] ?? []).map((b: Block) => (
        <EditableBlock
          key={b.id}
          block={b}
          citeNumbers={cites}
          onChange={(nb) => updateBlock(slideId, b.id!, nb)}
          onDelete={() => removeBlock(slideId, b.id!)}
        />
      ))}
      <AddBlock onAdd={(type) => addBlock(slideId, column, type)} />
    </>
  )

  switch (slide.layout) {
    case 'title':
      return (
        <div className="deck-stage layout-title" style={acc}>
          <div className="edge-bar" />
          <h1><EditableText className="edit-h1" value={slide.title ?? ''}
            onChange={(rt) => updateSlide(slideId, { title: asString(rt) })} /></h1>
          <div className="subtitle"><EditableText value={slide.subtitle ?? ''}
            placeholder="Subtitle"
            onChange={(rt) => updateSlide(slideId, { subtitle: asString(rt) })} /></div>
        </div>
      )
    case 'section':
      return (
        <div className="deck-stage layout-section" style={acc}>
          <h1><EditableText className="edit-h1" value={slide.title ?? ''}
            onChange={(rt) => updateSlide(slideId, { title: asString(rt) })} /></h1>
          <div className="subtitle"><EditableText value={slide.subtitle ?? ''}
            placeholder="Subtitle"
            onChange={(rt) => updateSlide(slideId, { subtitle: asString(rt) })} /></div>
        </div>
      )
    case 'two_column':
      return (
        <div className="deck-stage" style={acc}>
          <div className="slot title-band">{titleField}<div className="title-rule" /></div>
          <div className="slot body-flow">
            <div className="two-cols">
              <div className="edit-col">{blockList('blocks')}</div>
              <div className="edit-col">{blockList('right')}</div>
            </div>
          </div>
        </div>
      )
    case 'image_left':
    case 'image_right': {
      const side = slide.layout === 'image_left' ? 'left' : 'right'
      const inset = side === 'left'
        ? { left: 'calc(45% + 40px)', right: '56px' }
        : { left: '56px', right: 'calc(45% + 40px)' }
      return (
        <div className="deck-stage" style={acc}>
          <div className={`image-pane ${side}`}>
            <div className="slot-empty">{slide.image?.query
              ? `image: ${slide.image.query}` : 'image slot'}</div>
          </div>
          <div className="slot" style={{ top: 56, ...inset }}>
            <h2 className="blk-heading lvl-1">{titleField}</h2>
          </div>
          <div className="slot body-flow" style={{ top: 150, ...inset, bottom: 56 }}>
            {blockList('blocks')}
          </div>
        </div>
      )
    }
    default:
      return (
        <div className="deck-stage" style={acc}>
          <div className="slot title-band">{titleField}<div className="title-rule" /></div>
          <div className="slot body-flow">{blockList('blocks')}</div>
        </div>
      )
  }
}

/** For read-only preview of a slide we still use SlideView; EditableSlide is
 *  the interactive one. Consumers pick based on edit mode. */
export type { SlideT }
