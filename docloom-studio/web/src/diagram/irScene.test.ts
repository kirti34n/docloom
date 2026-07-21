import { describe, expect, it } from 'vitest'
import {
  layoutReportToSkeletons,
  sceneToDiagramIR,
  type DiagramIR,
  type LayoutReport,
  type SceneElementLike,
} from './irScene'

// A small solved-geometry fixture standing in for a real POST /diagram/layout
// response: two nodes (one 'queue' -> diamond, one 'client' -> ellipse) in a
// group, connected by one edge.
function fixtureReport(): LayoutReport {
  return {
    width: 400,
    height: 200,
    title: null,
    direction: 'LR',
    legend: ['queue', 'client'],
    legend_h: 0,
    nodes: [
      {
        id: 'n1', type: 'queue', label: 'Ingest queue', sublabel: 'SQS',
        tag: 'v2', group: 'g1', x: 10, y: 10, w: 120, h: 60,
      },
      {
        id: 'n2', type: 'client', label: 'Mobile app', sublabel: null,
        tag: null, group: null, x: 220, y: 10, w: 120, h: 60,
      },
    ],
    edges: [
      { source: 'n1', target: 'n2', label: 'events', style: 'dashed', pts: [[130, 40], [220, 40]], label_box: null },
    ],
    groups: [
      { id: 'g1', kind: 'security-group', label: 'Edge boundary', x: 0, y: 0, w: 160, h: 100 },
    ],
  }
}

describe('layoutReportToSkeletons', () => {
  it('maps node shapes by type, keeping the finer type in customData', () => {
    const skeletons = layoutReportToSkeletons(fixtureReport())
    const n1 = skeletons.find((s) => 'id' in s && s.id === 'n1') as any
    const n2 = skeletons.find((s) => 'id' in s && s.id === 'n2') as any
    expect(n1.type).toBe('diamond') // queue -> diamond
    expect(n1.customData).toEqual({ docloomType: 'queue', sublabel: 'SQS', tag: 'v2' })
    expect(n1.label).toEqual({ text: 'Ingest queue' })
    expect(n1.x).toBe(10)
    expect(n1.y).toBe(10)
    expect(n1.width).toBe(120)
    expect(n1.height).toBe(60)

    expect(n2.type).toBe('ellipse') // client -> ellipse
    expect(n2.customData.docloomType).toBe('client')
  })

  it('maps a group to a frame skeleton whose children are its member node ids', () => {
    const skeletons = layoutReportToSkeletons(fixtureReport())
    const frame = skeletons.find((s) => s.type === 'frame') as any
    expect(frame.id).toBe('g1')
    expect(frame.name).toBe('Edge boundary')
    expect(frame.children).toEqual(['n1']) // only n1 is in g1
    expect(frame.customData).toEqual({ docloomKind: 'security-group' })
  })

  it('maps an edge to an arrow skeleton bound to source/target node ids', () => {
    const skeletons = layoutReportToSkeletons(fixtureReport())
    const arrow = skeletons.find((s) => s.type === 'arrow') as any
    expect(arrow.start).toEqual({ id: 'n1' })
    expect(arrow.end).toEqual({ id: 'n2' })
    expect(arrow.label).toEqual({ text: 'events' })
    expect(arrow.customData).toEqual({ docloomStyle: 'dashed' })
  })

  it('omits the label key entirely for an unlabeled edge', () => {
    const report = fixtureReport()
    report.edges[0].label = null
    const arrow = layoutReportToSkeletons(report).find((s) => s.type === 'arrow') as any
    expect(arrow.label).toBeUndefined()
  })
})

// Build the SceneElementLike[] a live Excalidraw scene would hold after the
// skeletons above were seeded in, PLUS a simulated user edit: the label text
// of n1 changed ("Ingest queue" -> "Renamed queue"), and one extra unbound
// (free-floating) arrow was drawn, which must be ignored on read-back.
function editedScene(): SceneElementLike[] {
  return [
    {
      id: 'n1', type: 'diamond', frameId: 'g1',
      customData: { docloomType: 'queue', sublabel: 'SQS', tag: 'v2' },
      boundElements: [{ id: 'n1-text', type: 'text' }],
    },
    { id: 'n1-text', type: 'text', text: 'Renamed queue' },
    {
      id: 'n2', type: 'ellipse', frameId: null,
      customData: { docloomType: 'client' },
      boundElements: [{ id: 'n2-text', type: 'text' }],
    },
    { id: 'n2-text', type: 'text', text: 'Mobile app' },
    { id: 'g1', type: 'frame', name: 'Edge boundary', customData: { docloomKind: 'security-group' } },
    {
      id: 'e1', type: 'arrow',
      startBinding: { elementId: 'n1' }, endBinding: { elementId: 'n2' },
      customData: { docloomStyle: 'dashed' },
      boundElements: [{ id: 'e1-text', type: 'text' }],
    },
    { id: 'e1-text', type: 'text', text: 'events' },
    // a free-drawn arrow with no bindings -- must be skipped, not crash
    { id: 'stray-arrow', type: 'arrow', startBinding: null, endBinding: null },
    // a deleted node -- must be excluded entirely
    { id: 'n3', type: 'rectangle', isDeleted: true, customData: { docloomType: 'service' } },
  ]
}

describe('sceneToDiagramIR', () => {
  it('round-trips a small IR through layoutReportToSkeletons and a simulated edit', () => {
    // sanity: the fixture's skeletons are what seeded the scene we're editing
    const skeletons = layoutReportToSkeletons(fixtureReport())
    expect(skeletons.length).toBe(4) // 2 nodes + 1 frame + 1 arrow

    const ir = sceneToDiagramIR(editedScene(), { direction: 'LR' })

    expect(ir.type).toBe('diagram')
    expect(ir.direction).toBe('LR')
    expect(ir.nodes).toEqual<DiagramIR['nodes']>([
      { id: 'n1', label: 'Renamed queue', type: 'queue', sublabel: 'SQS', tag: 'v2', group: 'g1' },
      { id: 'n2', label: 'Mobile app', type: 'client', sublabel: null, tag: null, group: null },
    ])
    expect(ir.edges).toEqual<DiagramIR['edges']>([
      { source: 'n1', target: 'n2', label: 'events', style: 'dashed' },
    ])
    expect(ir.groups).toEqual<DiagramIR['groups']>([
      { id: 'g1', label: 'Edge boundary', kind: 'security-group' },
    ])
  })

  it('falls back to a shape-derived type when customData.docloomType is absent', () => {
    const ir = sceneToDiagramIR([
      { id: 'a', type: 'rectangle' },
      { id: 'b', type: 'diamond' },
      { id: 'c', type: 'ellipse' },
    ])
    expect(ir.nodes.map((n) => n.type)).toEqual(['service', 'security', 'client'])
  })

  it('skips an arrow bound to an unknown or missing node id instead of throwing', () => {
    const ir = sceneToDiagramIR([
      { id: 'a', type: 'rectangle' },
      { id: 'e', type: 'arrow', startBinding: { elementId: 'a' }, endBinding: { elementId: 'ghost' } },
    ])
    expect(ir.edges).toEqual([])
  })

  it('ignores non-diagram elements (free text, freedraw) entirely', () => {
    const ir = sceneToDiagramIR([
      { id: 'a', type: 'rectangle' },
      { id: 'note', type: 'text', text: 'a floating sticky note' },
      { id: 'doodle', type: 'freedraw' },
    ])
    expect(ir.nodes).toEqual([{ id: 'a', label: 'a', type: 'service', sublabel: null, tag: null, group: null }])
  })

  it('defaults an unlabeled node to its own id', () => {
    const ir = sceneToDiagramIR([{ id: 'solo', type: 'rectangle' }])
    expect(ir.nodes[0].label).toBe('solo')
  })
})
