/** Unit test for the draw.io postMessage bridge's one load-bearing reply:
 *  the `export` event answering our `{action:'export', format:'xmlsvg'}`
 *  request. Extracted as `applyDrawioExport` (a plain async function, no
 *  DOM/iframe) precisely so it's testable here -- this repo has no
 *  jsdom/testing-library (see screens/ultracode.test.ts). */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { applyDrawioExport } from './DrawioCanvas'

afterEach(() => {
  vi.restoreAllMocks()
})

// A minimal but valid `<svg>...</svg>` document, base64-encoded exactly the
// way draw.io's own `xmlsvg` export always encodes it.
const SVG_TEXT = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
const DATA_URL = `data:image/svg+xml;base64,${btoa(SVG_TEXT)}`
const XML_TEXT = '<mxfile><diagram><mxGraphModel/></diagram></mxfile>'

describe('applyDrawioExport', () => {
  it('PUTs the payload with the canonical mxGraph XML, then POSTs the decoded SVG to /renders', async () => {
    const put = vi.spyOn(api, 'put').mockResolvedValue(undefined)
    const post = vi.spyOn(api, 'post').mockResolvedValue(undefined)

    const result = await applyDrawioExport('art-1', 'paper', { xml: XML_TEXT, data: DATA_URL })

    expect(put).toHaveBeenCalledTimes(1)
    expect(put).toHaveBeenCalledWith('/api/artifacts/art-1/payload', {
      payload: { type: 'diagram_drawio', drawio_xml: XML_TEXT, theme_name: 'paper', render: 'svg' },
    })

    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('/api/artifacts/art-1/renders', { svg: SVG_TEXT })

    expect(result.svg).toBe(SVG_TEXT)
  })

  it('still saves the payload but skips the /renders call when the data URL cannot be decoded', async () => {
    const put = vi.spyOn(api, 'put').mockResolvedValue(undefined)
    const post = vi.spyOn(api, 'post').mockResolvedValue(undefined)

    const result = await applyDrawioExport('art-1', 'paper', { xml: XML_TEXT, data: 'not-a-data-url' })

    expect(put).toHaveBeenCalledTimes(1)
    expect(post).not.toHaveBeenCalled()
    expect(result.svg).toBeNull()
  })

  it('propagates a PUT failure without calling /renders (the export is not silently dropped)', async () => {
    vi.spyOn(api, 'put').mockRejectedValue(new Error('save failed'))
    const post = vi.spyOn(api, 'post').mockResolvedValue(undefined)

    await expect(applyDrawioExport('art-1', 'paper', { xml: XML_TEXT, data: DATA_URL }))
      .rejects.toThrow('save failed')
    expect(post).not.toHaveBeenCalled()
  })
})
