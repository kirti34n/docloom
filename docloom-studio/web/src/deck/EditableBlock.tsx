import { Trash2 } from 'lucide-react'
import type { Block, ListItem, RichText } from './types'
import { BlockView } from './blocks'
import { EditableList, EditableText } from './Editable'

const TEXT_BLOCKS = new Set(['heading', 'paragraph', 'quote', 'callout'])
const LIST_BLOCKS = new Set(['bullets', 'numbered'])

export function EditableBlock({
  block,
  citeNumbers,
  onChange,
  onDelete,
}: {
  block: Block
  citeNumbers?: Map<string, number>
  onChange: (block: Block) => void
  onDelete: () => void
}) {
  const editable = TEXT_BLOCKS.has(block.type) || LIST_BLOCKS.has(block.type)

  return (
    <div className="editable-block group relative">
      <button
        onClick={onDelete}
        title="Delete block"
        className="editable-del absolute -left-8 top-1 hidden rounded p-1 text-white/50 hover:text-white group-hover:block"
      >
        <Trash2 size={14} />
      </button>

      {block.type === 'heading' ? (
        <div className={`blk-heading lvl-${Math.min(block.level ?? 2, 4)}`}>
          <EditableText
            value={block.text ?? ''}
            onChange={(text: RichText) => onChange({ ...block, text })}
          />
        </div>
      ) : block.type === 'paragraph' ? (
        <div className="blk-para">
          <EditableText
            value={block.text ?? ''}
            onChange={(text: RichText) => onChange({ ...block, text })}
          />
        </div>
      ) : block.type === 'quote' ? (
        <div className="blk-quote">
          <EditableText
            value={block.text ?? ''}
            onChange={(text: RichText) => onChange({ ...block, text })}
          />
          {block.attribution && <cite>— {block.attribution}</cite>}
        </div>
      ) : block.type === 'callout' ? (
        <div className={`blk-callout style-${block.style ?? 'info'}`}>
          <EditableText
            value={block.text ?? ''}
            onChange={(text: RichText) => onChange({ ...block, text })}
          />
        </div>
      ) : LIST_BLOCKS.has(block.type) ? (
        <div className="blk-list">
          <EditableList
            items={(block.items ?? []) as ListItem[]}
            ordered={block.type === 'numbered'}
            onChange={(items: ListItem[]) => onChange({ ...block, items })}
          />
        </div>
      ) : (
        // non-text blocks render read-only in M2 (edit via inspector later)
        <BlockView block={block} citeNumbers={citeNumbers} />
      )}
      {!editable && <div className="editable-hint">read-only in this view</div>}
    </div>
  )
}
