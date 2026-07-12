import { Trash2 } from 'lucide-react'
import type { Block, ListItem, RichText } from './types'
import { BlockView } from './blocks'
import { EditableList, EditableText } from './Editable'
import { RichBlockEditor, hasRichEditor } from './RichBlockEditor'

const TEXT_BLOCKS = new Set(['heading', 'paragraph', 'quote', 'callout'])
const LIST_BLOCKS = new Set(['bullets', 'numbered'])

export function EditableBlock({
  block,
  citeNumbers,
  extRev = 0,
  onChange,
  onDelete,
}: {
  block: Block
  citeNumbers?: Map<string, number>
  extRev?: number
  onChange: (block: Block) => void
  onDelete: () => void
}) {
  const editable =
    TEXT_BLOCKS.has(block.type) || LIST_BLOCKS.has(block.type) || hasRichEditor(block.type)

  return (
    <div className="editable-block group relative">
      <button
        onClick={onDelete}
        title="Delete block"
        aria-label="Delete block"
        className="editable-del absolute -left-8 top-1 hidden rounded p-1 text-white/50 hover:text-white group-hover:block"
      >
        <Trash2 size={14} />
      </button>

      {block.type === 'heading' ? (
        <div className={`blk-heading lvl-${Math.min(block.level ?? 2, 4)}`}>
          <EditableText
            value={block.text ?? ''}
            extRev={extRev}
            onChange={(text: RichText) => onChange({ ...block, text })}
          />
        </div>
      ) : block.type === 'paragraph' ? (
        <div className="blk-para">
          <EditableText
            value={block.text ?? ''}
            extRev={extRev}
            onChange={(text: RichText) => onChange({ ...block, text })}
          />
        </div>
      ) : block.type === 'quote' ? (
        <div className="blk-quote">
          <EditableText
            value={block.text ?? ''}
            extRev={extRev}
            onChange={(text: RichText) => onChange({ ...block, text })}
          />
          {block.attribution && <cite>— {block.attribution}</cite>}
        </div>
      ) : block.type === 'callout' ? (
        <div className={`blk-callout style-${block.style ?? 'info'}`}>
          <EditableText
            value={block.text ?? ''}
            extRev={extRev}
            onChange={(text: RichText) => onChange({ ...block, text })}
          />
        </div>
      ) : LIST_BLOCKS.has(block.type) ? (
        <div className="blk-list">
          <EditableList
            items={(block.items ?? []) as ListItem[]}
            ordered={block.type === 'numbered'}
            extRev={extRev}
            onChange={(items: ListItem[]) => onChange({ ...block, items })}
          />
        </div>
      ) : hasRichEditor(block.type) ? (
        <RichBlockEditor block={block} onChange={onChange} />
      ) : (
        // anything without a dedicated editor still renders read-only
        <BlockView block={block} citeNumbers={citeNumbers} />
      )}
      {!editable && <div className="editable-hint">read-only in this view</div>}
    </div>
  )
}
