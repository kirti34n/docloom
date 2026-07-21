/** Regression tests for the ultracode fix pass (web-deck-ui area):
 *  2. TableEditor precomputed header/rows via asText → plain(), flattening
 *     every RichText cell to a bare string, so structural table edits
 *     (add row, add column, etc.) stripped bold/italic/code/link/citation
 *     marks from cells the user never touched. */

import { describe, expect, it } from 'vitest'
import { tableAddColumn, tableAddRow } from './RichBlockEditor'
import type { Block } from './types'

function richTable(): Block {
  return {
    type: 'table',
    header: [[{ text: 'GDP', cite: 'src-1' }], [{ text: 'Rev', bold: true }]],
    rows: [[[{ text: '42', italic: true }], 'plain']],
  } as Block
}

describe('table editor: structural edits preserve untouched cell marks', () => {
  it('adding a row keeps existing cells and their marks intact', () => {
    const block = richTable()
    const after = tableAddRow(block)
    expect(after.rows![0][0]).toEqual([{ text: '42', italic: true }])
    expect(after.rows![0][1]).toBe('plain')
    expect(after.rows![1]).toEqual(['', ''])
  })

  it('adding a column keeps the header cite and body marks intact', () => {
    const block = richTable()
    const after = tableAddColumn(block)
    expect(after.header![0]).toEqual([{ text: 'GDP', cite: 'src-1' }])
    expect(after.header![1]).toEqual([{ text: 'Rev', bold: true }])
    expect(after.header![2]).toBe('Column 3')
    expect(after.rows![0][0]).toEqual([{ text: '42', italic: true }])
    expect(after.rows![0][2]).toBe('')
  })
})
