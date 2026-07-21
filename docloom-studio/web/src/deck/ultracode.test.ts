/** Regression tests for the ultracode fix pass (web-deck-store area):
 *  1. docToListItems dropped every list item after a Shift-Tab lift moved a
 *     bullet to the top level of the doc.
 *  2. Autosave could run two PUTs concurrently, and an out-of-order response
 *     clobbered rev/findings with stale data.
 *  3. listItemsToDoc's synthesized empty host (for a first item with
 *     level>=1) read back as a phantom empty top-level bullet. */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { docToListItems, listItemsToDoc } from './serialize'
import type { PMNode } from './serialize'
import { useDeck } from './deckStore'
import { useDoc } from '../doc/docStore'
import type { ArtifactT, Finding } from './types'

function pmListItem(text: string): PMNode {
  return { type: 'listItem', content: [{ type: 'paragraph', content: [{ type: 'text', text }] }] }
}

function pmParagraph(text: string): PMNode {
  return { type: 'paragraph', content: [{ type: 'text', text }] }
}

function finding(message: string): Finding {
  return { rule: 'r', severity: 'info', where: 'w', message }
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('docToListItems: top-level lift no longer drops siblings', () => {
  it('recovers all items after lifting the first bullet out of the list', () => {
    // shape produced by ProseMirror's liftListItem on item 0 of [alpha, beta, gamma]
    const doc: PMNode = {
      type: 'doc',
      content: [
        pmParagraph('alpha'),
        { type: 'bulletList', content: [pmListItem('beta'), pmListItem('gamma')] },
      ],
    }
    expect(docToListItems(doc)).toEqual([
      { text: 'alpha', level: 0 },
      { text: 'beta', level: 0 },
      { text: 'gamma', level: 0 },
    ])
  })

  it('recovers all items after lifting a middle bullet', () => {
    // shape produced by lifting item 1 of [alpha, beta, gamma]
    const doc: PMNode = {
      type: 'doc',
      content: [
        { type: 'bulletList', content: [pmListItem('alpha')] },
        pmParagraph('beta'),
        { type: 'bulletList', content: [pmListItem('gamma')] },
      ],
    }
    expect(docToListItems(doc)).toEqual([
      { text: 'alpha', level: 0 },
      { text: 'beta', level: 0 },
      { text: 'gamma', level: 0 },
    ])
  })
})

describe('listItemsToDoc <-> docToListItems: synthesized host is not a phantom item', () => {
  it('round-trips an indented-first list without an extra empty leading bullet', () => {
    const items = [
      { text: 'indented first', level: 1 },
      { text: 'second', level: 1 },
    ]
    expect(docToListItems(listItemsToDoc(items, false))).toEqual(items)
  })
})

describe('deckStore autosave: single in-flight PUT, coalesced trailing flush', () => {
  it('never overlaps two PUTs and applies edits made mid-save on top of the stale response', async () => {
    vi.useFakeTimers()
    const payloads: string[] = []
    const resolvers: Array<(v: { version: number; findings: Finding[] }) => void> = []
    vi.spyOn(api, 'put').mockImplementation((_path: string, body?: unknown) => {
      const payload = (body as { payload: { ir: { title: string } } }).payload
      payloads.push(payload.ir.title)
      return new Promise((resolve) => resolvers.push(resolve))
    })

    const artifact: ArtifactT = {
      id: 'a1',
      notebook_id: 'n1',
      kind: 'deck',
      title: 'Test deck',
      version: 1,
      payload: { ir: { title: 'Loaded', slides: [] }, theme_name: 'paper' },
    }
    useDeck.getState().load(artifact)

    useDeck.getState().setMeta({ title: 'A' })
    await vi.advanceTimersByTimeAsync(700)
    expect(api.put).toHaveBeenCalledTimes(1)

    useDeck.getState().setMeta({ title: 'AB' })
    await vi.advanceTimersByTimeAsync(700)
    // the second debounce timer fired while the first PUT is still in
    // flight: it must NOT start a concurrent second PUT
    expect(api.put).toHaveBeenCalledTimes(1)

    // resolve the stale (first) PUT
    resolvers[0]({ version: 2, findings: [finding('from A only')] })
    await vi.advanceTimersByTimeAsync(0)
    // its response landed while a newer edit existed, so it must trigger
    // exactly one trailing re-flush carrying the latest state
    expect(api.put).toHaveBeenCalledTimes(2)
    expect(payloads[1]).toBe('AB')

    resolvers[1]({ version: 3, findings: [finding('from AB')] })
    await vi.advanceTimersByTimeAsync(0)
    expect(useDeck.getState().rev).toBe(3)
    expect(useDeck.getState().findings[0].message).toBe('from AB')
    expect(useDeck.getState().dirty).toBe(false)
  })
})

describe('docStore autosave: single in-flight PUT, coalesced trailing flush', () => {
  it('never overlaps two PUTs and applies edits made mid-save on top of the stale response', async () => {
    vi.useFakeTimers()
    const payloads: string[] = []
    const resolvers: Array<(v: { version: number; findings: Finding[] }) => void> = []
    vi.spyOn(api, 'put').mockImplementation((_path: string, body?: unknown) => {
      const payload = (body as { payload: { ir: { title: string } } }).payload
      payloads.push(payload.ir.title)
      return new Promise((resolve) => resolvers.push(resolve))
    })

    const artifact: ArtifactT = {
      id: 'd1',
      notebook_id: 'n1',
      kind: 'doc',
      title: 'Test doc',
      version: 1,
      payload: { ir: { title: 'Loaded', blocks: [] }, theme_name: 'paper' },
    }
    useDoc.getState().load(artifact)

    useDoc.getState().setTitle('A')
    await vi.advanceTimersByTimeAsync(700)
    expect(api.put).toHaveBeenCalledTimes(1)

    useDoc.getState().setTitle('AB')
    await vi.advanceTimersByTimeAsync(700)
    expect(api.put).toHaveBeenCalledTimes(1)

    resolvers[0]({ version: 2, findings: [finding('from A only')] })
    await vi.advanceTimersByTimeAsync(0)
    expect(api.put).toHaveBeenCalledTimes(2)
    expect(payloads[1]).toBe('AB')

    resolvers[1]({ version: 3, findings: [finding('from AB')] })
    await vi.advanceTimersByTimeAsync(0)
    expect(useDoc.getState().rev).toBe(3)
    expect(useDoc.getState().findings[0].message).toBe('from AB')
    expect(useDoc.getState().dirty).toBe(false)
  })
})
