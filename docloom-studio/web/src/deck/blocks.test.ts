/** Regression test for the deck-preview parity fix: an `artifact` block with
 *  an `artifact_id` must resolve its picture from the engine-rendered
 *  render.svg (fetched by artifact_id) instead of waiting for `block.path`,
 *  which stays null until export's bake() sets it. See editor_design.md
 *  section 5 and blocks.tsx's ArtifactImage/fetchArtifactSvg.
 *
 *  Note: this project's vitest runs in the default Node environment (no
 *  jsdom/@testing-library/react installed), so these tests exercise the
 *  extracted, exported pure logic (the cached fetch and the SVG scaling
 *  string-surgery) rather than mounting <ArtifactImage> itself. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { _resetArtifactSvgCacheForTests, fetchArtifactSvg, scaleSvgMarkup } from './blocks'

describe('fetchArtifactSvg', () => {
  beforeEach(() => {
    _resetArtifactSvgCacheForTests()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('fetches render.svg by artifact_id and resolves its text', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: () => Promise.resolve('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"></svg>'),
    })
    vi.stubGlobal('fetch', fetchMock)

    const svg = await fetchArtifactSvg('art-1')

    expect(fetchMock).toHaveBeenCalledWith('/api/artifacts/art-1/render.svg')
    expect(svg).toBe('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"></svg>')
  })

  it('resolves to null (not a rejection) when the artifact has no render yet (404)', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, text: () => Promise.resolve('') })
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchArtifactSvg('art-2')).resolves.toBeNull()
  })

  it('resolves to null on a network failure instead of throwing', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error('network down'))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchArtifactSvg('art-3')).resolves.toBeNull()
  })

  it('caches per artifact_id: a second call for the same id does not refetch', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, text: () => Promise.resolve('<svg xmlns="ns"></svg>') })
    vi.stubGlobal('fetch', fetchMock)

    const first = await fetchArtifactSvg('art-4')
    const second = await fetchArtifactSvg('art-4')

    expect(first).toBe(second)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('scaleSvgMarkup', () => {
  it('injects a scaling style attribute right after the root <svg> tag', () => {
    const raw = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect/></svg>'
    const out = scaleSvgMarkup(raw)
    expect(out).toBe(
      '<svg style="max-width:100%;height:auto;display:block" xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect/></svg>',
    )
  })

  it('leaves non-SVG input untouched rather than throwing', () => {
    expect(scaleSvgMarkup('<div>not an svg</div>')).toBe('<div>not an svg</div>')
  })
})
