/** Pure mapping between a docloom architecture-diagram IR (structure only:
 *  nodes/edges/groups, no coordinates -- see docloom.ir.Diagram) and the
 *  Excalidraw scene the in-app canvas edits.
 *
 *  Two directions, both pure (no DOM, no Excalidraw runtime import -- only
 *  `import type` from the package, which is erased at compile time):
 *
 *   - layoutReportToSkeletons: server-solved geometry (POST /diagram/layout)
 *     -> Excalidraw element *skeletons*, the shape `convertToExcalidrawElements`
 *     expects. The caller feeds the result straight into that function with
 *     `{ regenerateIds: false }` -- see DiagramIRCanvas.tsx. Positions come
 *     from the solver; Phase A never lets the canvas author coordinates.
 *
 *   - sceneToDiagramIR: a (possibly hand-edited) Excalidraw scene -> a clean
 *     Diagram IR. Structure only: x/y/width/height are read by the *layout*
 *     direction only and are deliberately discarded here (Phase A; auto-layout
 *     owns positions -- see docs/editor-design.md section 1). A node's
 *     `type` (service/client/store/queue/security/cloud/external) can't be
 *     recovered from its Excalidraw shape alone -- diamond covers both
 *     security and queue, ellipse covers both client and cloud -- so it is
 *     round-tripped through `customData.docloomType` on the element; shape
 *     is only a fallback for elements that never carried it (e.g. hand-drawn
 *     with the raw Excalidraw shape tool). Free-drawn or unbound elements
 *     (a doodle, an arrow not bound to two known nodes) are skipped rather
 *     than crashing or corrupting the IR.
 */
import type { ExcalidrawElementSkeleton } from '@excalidraw/excalidraw/data/transform'

export type DiagramNodeType =
  | 'service' | 'client' | 'store' | 'queue' | 'security' | 'cloud' | 'external'
export type DiagramEdgeStyle = 'solid' | 'dashed' | 'emphasis' | 'secure'
export type DiagramGroupKind = 'region' | 'security-group'

const NODE_TYPES: readonly DiagramNodeType[] =
  ['service', 'client', 'store', 'queue', 'security', 'cloud', 'external']
const EDGE_STYLES: readonly DiagramEdgeStyle[] = ['solid', 'dashed', 'emphasis', 'secure']
const GROUP_KINDS: readonly DiagramGroupKind[] = ['region', 'security-group']

function isNodeType(v: unknown): v is DiagramNodeType {
  return typeof v === 'string' && (NODE_TYPES as readonly string[]).includes(v)
}
function isEdgeStyle(v: unknown): v is DiagramEdgeStyle {
  return typeof v === 'string' && (EDGE_STYLES as readonly string[]).includes(v)
}
function isGroupKind(v: unknown): v is DiagramGroupKind {
  return typeof v === 'string' && (GROUP_KINDS as readonly string[]).includes(v)
}

export interface DiagramNodeIR {
  id: string
  label: string
  type: DiagramNodeType
  sublabel?: string | null
  tag?: string | null
  group?: string | null
}

export interface DiagramEdgeIR {
  source: string
  target: string
  label?: string | null
  style: DiagramEdgeStyle
}

export interface DiagramGroupIR {
  id: string
  label: string
  kind: DiagramGroupKind
}

/** docloom.ir.Diagram, as JSON. */
export interface DiagramIR {
  type: 'diagram'
  id?: string | null
  title?: string | null
  direction: 'LR' | 'TB'
  nodes: DiagramNodeIR[]
  edges: DiagramEdgeIR[]
  groups: DiagramGroupIR[]
  caption?: string | null
  alt?: string
}

export function emptyDiagramIR(direction: 'LR' | 'TB' = 'LR'): DiagramIR {
  return { type: 'diagram', direction, nodes: [], edges: [], groups: [] }
}

// ---- geometry the /diagram/layout route returns (diagram_svg.layout_report) ----

export interface LayoutNode {
  id: string
  type: string
  label: string
  sublabel?: string | null
  tag?: string | null
  group?: string | null
  x: number; y: number; w: number; h: number
}

export interface LayoutEdge {
  source: string
  target: string
  label?: string | null
  style: string
  pts: [number, number][]
  label_box?: [number, number, number, number] | null
}

export interface LayoutGroup {
  id: string
  kind: string
  label: string
  x: number; y: number; w: number; h: number
}

export interface LayoutReport {
  width: number
  height: number
  title?: string | null
  direction: string
  legend?: string[]
  legend_h: number
  nodes: LayoutNode[]
  edges: LayoutEdge[]
  groups: LayoutGroup[]
  warning?: string
}

/** service/store/external -> rectangle, security/queue -> diamond,
 *  client/cloud -> ellipse. The painter's own shape vocabulary
 *  (diagram_svg.py) collapses to Excalidraw's three container shapes; the
 *  finer distinction is preserved out-of-band in `customData.docloomType`. */
function shapeForType(type: string): 'rectangle' | 'diamond' | 'ellipse' {
  if (type === 'security' || type === 'queue') return 'diamond'
  if (type === 'client' || type === 'cloud') return 'ellipse'
  return 'rectangle'
}

/** The reverse of shapeForType, used only as a fallback default when an
 *  element carries no `customData.docloomType` (e.g. hand-drawn with the
 *  raw shape tool rather than "Add node"). Ambiguous shapes resolve to the
 *  more common of their two IR types. */
function defaultTypeForShape(shape: string): DiagramNodeType {
  if (shape === 'diamond') return 'security'
  if (shape === 'ellipse') return 'client'
  return 'service'
}

/** Map solved geometry to Excalidraw element skeletons: nodes first (so
 *  arrow/frame id bindings resolve against them), then groups as `frame`
 *  skeletons, then edges as `arrow` skeletons bound by node id. Positions
 *  and sizes come straight from the solver. */
export function layoutReportToSkeletons(report: LayoutReport): ExcalidrawElementSkeleton[] {
  const skeletons: ExcalidrawElementSkeleton[] = []

  for (const n of report.nodes) {
    skeletons.push({
      type: shapeForType(n.type),
      id: n.id,
      x: n.x,
      y: n.y,
      width: n.w,
      height: n.h,
      label: { text: n.label },
      customData: {
        docloomType: n.type,
        sublabel: n.sublabel ?? null,
        tag: n.tag ?? null,
      },
    } as ExcalidrawElementSkeleton)
  }

  for (const g of report.groups) {
    const children = report.nodes.filter((n) => n.group === g.id).map((n) => n.id)
    skeletons.push({
      type: 'frame',
      id: g.id,
      name: g.label,
      children,
      x: g.x,
      y: g.y,
      width: g.w,
      height: g.h,
      customData: { docloomKind: g.kind },
    } as ExcalidrawElementSkeleton)
  }

  for (const e of report.edges) {
    const [x0, y0] = e.pts[0] ?? [0, 0]
    skeletons.push({
      type: 'arrow',
      x: x0,
      y: y0,
      start: { id: e.source },
      end: { id: e.target },
      label: e.label ? { text: e.label } : undefined,
      strokeStyle: e.style === 'dashed' ? 'dashed' : 'solid',
      customData: { docloomStyle: e.style },
    } as ExcalidrawElementSkeleton)
  }

  return skeletons
}

// ---- reading an edited scene back into IR (structure only) ----

/** Everything sceneToDiagramIR reads off a real Excalidraw element. A real
 *  `ExcalidrawElement` has far more fields than this; TS structural typing
 *  lets the real (much wider) type satisfy this narrower one at the call
 *  site, so this module never imports the Excalidraw runtime and its tests
 *  need no DOM/canvas -- see the module docstring above. */
export interface SceneElementLike {
  id: string
  type: string
  isDeleted?: boolean
  frameId?: string | null
  name?: string | null
  customData?: Record<string, unknown> | null
  boundElements?: readonly { id: string; type: string }[] | null
  startBinding?: { elementId: string } | null
  endBinding?: { elementId: string } | null
  text?: string
  originalText?: string
}

function labelFor(el: SceneElementLike, all: readonly SceneElementLike[]): string {
  const bound = el.boundElements?.find((b) => b.type === 'text')
  if (!bound) return ''
  const textEl = all.find((e) => e.id === bound.id && !e.isDeleted)
  return (textEl?.text ?? textEl?.originalText ?? '').trim()
}

export interface DiagramMeta {
  id?: string | null
  title?: string | null
  direction?: 'LR' | 'TB'
  caption?: string | null
  alt?: string
}

/** Derive a structural Diagram IR from the current scene. Positions are
 *  intentionally not read (Phase A: the server solver owns layout). Free-
 *  drawn shapes and arrows not bound to two known nodes are skipped rather
 *  than raising -- an editor must never crash on a stray doodle. */
export function sceneToDiagramIR(
  elements: readonly SceneElementLike[],
  meta: DiagramMeta = {},
): DiagramIR {
  const live = elements.filter((e) => !e.isDeleted)
  const nodeEls = live.filter((e) => e.type === 'rectangle' || e.type === 'diamond' || e.type === 'ellipse')
  const frameEls = live.filter((e) => e.type === 'frame')
  const arrowEls = live.filter((e) => e.type === 'arrow')
  const nodeIds = new Set(nodeEls.map((e) => e.id))

  const nodes: DiagramNodeIR[] = nodeEls.map((e) => {
    const customType = e.customData?.docloomType
    const group = frameEls.find((f) => f.id === e.frameId)?.id ?? null
    const sublabel = e.customData?.sublabel
    const tag = e.customData?.tag
    return {
      id: e.id,
      label: labelFor(e, live) || e.id,
      type: isNodeType(customType) ? customType : defaultTypeForShape(e.type),
      sublabel: typeof sublabel === 'string' ? sublabel : null,
      tag: typeof tag === 'string' ? tag : null,
      group,
    }
  })

  const edges: DiagramEdgeIR[] = []
  for (const e of arrowEls) {
    const source = e.startBinding?.elementId
    const target = e.endBinding?.elementId
    // unbound (free-floating) arrow: not a structural edge, skip silently
    if (!source || !target || !nodeIds.has(source) || !nodeIds.has(target)) continue
    const style = e.customData?.docloomStyle
    edges.push({
      source,
      target,
      label: labelFor(e, live) || null,
      style: isEdgeStyle(style) ? style : 'solid',
    })
  }

  const groups: DiagramGroupIR[] = frameEls.map((f) => {
    const kind = f.customData?.docloomKind
    return {
      id: f.id,
      label: f.name ?? f.id,
      kind: isGroupKind(kind) ? kind : 'region',
    }
  })

  return {
    type: 'diagram',
    id: meta.id ?? null,
    title: meta.title ?? null,
    direction: meta.direction ?? 'LR',
    nodes,
    edges,
    groups,
    caption: meta.caption ?? null,
    alt: meta.alt ?? '',
  }
}
