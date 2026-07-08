import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Copy, GripVertical, Plus, Trash2 } from 'lucide-react'
import type { StudioTheme } from './types'
import { ScaledSlide } from './DeckStage'
import { useDeck } from './deckStore'

function RailItem({ slideId, theme }: { slideId: string; theme: StudioTheme }) {
  const slide = useDeck((s) => s.slides[slideId])
  const selected = useDeck((s) => s.selected === slideId)
  const title = useDeck((s) => s.title)
  const sources = useDeck((s) => s.sources)
  const select = useDeck((s) => s.select)
  const duplicate = useDeck((s) => s.duplicateSlide)
  const remove = useDeck((s) => s.removeSlide)
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: slideId })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  if (!slide) return null
  return (
    <div ref={setNodeRef} style={style} className="rail-item group">
      <button
        onClick={() => select(slideId)}
        className={`block w-full overflow-hidden rounded-lg border-2 ${
          selected ? 'border-ws-accent' : 'border-transparent'
        }`}
      >
        <ScaledSlide slide={slide} doc={{ title, sources }} theme={theme} />
      </button>
      <div className="rail-tools">
        <span className="rail-grip" {...attributes} {...listeners}>
          <GripVertical size={13} />
        </span>
        <button title="Duplicate" onClick={() => duplicate(slideId)}>
          <Copy size={13} />
        </button>
        <button title="Delete" onClick={() => remove(slideId)}>
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  )
}

export function SlideRail({ theme }: { theme: StudioTheme }) {
  const order = useDeck((s) => s.order)
  const reorder = useDeck((s) => s.reorder)
  const insertSlide = useDeck((s) => s.insertSlide)
  const selected = useDeck((s) => s.selected)
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  const onDragEnd = (e: DragEndEvent) => {
    const { active, over } = e
    if (!over || active.id === over.id) return
    const from = order.indexOf(String(active.id))
    const to = order.indexOf(String(over.id))
    const next = [...order]
    next.splice(to, 0, next.splice(from, 1)[0])
    reorder(next)
  }

  return (
    <div className="flex w-52 shrink-0 flex-col border-r border-stage-line">
      <div className="flex-1 space-y-3 overflow-auto p-3">
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={order} strategy={verticalListSortingStrategy}>
            {order.map((id) => (
              <RailItem key={id} slideId={id} theme={theme} />
            ))}
          </SortableContext>
        </DndContext>
      </div>
      <button
        onClick={() => insertSlide(selected)}
        className="m-3 flex items-center justify-center gap-1.5 rounded-lg border border-stage-line py-2 text-[12px] text-stage-muted hover:text-white"
      >
        <Plus size={13} /> Add slide
      </button>
    </div>
  )
}
