/** Regression tests for the ultracode fix pass (web-screens area):
 *  1. Settings' initial /api/settings load had no .catch: a rejected fetch
 *     (500, network drop) produced an unhandled promise rejection and the
 *     screen was left showing a spinner forever, with no error and no way
 *     to retry. `loadSettings` is the extracted fetch/catch shape that
 *     `Settings()`'s `load` callback now drives; this file exercises it
 *     directly since the repo has no DOM-rendering test harness (no jsdom
 *     or @testing-library/react in node_modules, and adding them requires
 *     editing package.json, which is out of scope for this fix). */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { loadSettings } from './Settings'
import type { AllSettings } from './Settings'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Settings loadSettings: fetch failure is surfaced, not swallowed', () => {
  it('calls onError, not onSettings, when /api/settings rejects with an Error', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(new Error('boom'))
    const onSettings = vi.fn()
    const onError = vi.fn()

    await loadSettings(onSettings, onError)

    expect(onSettings).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledWith('boom')
  })

  it('stringifies a non-Error rejection instead of dropping it', async () => {
    vi.spyOn(api, 'get').mockRejectedValue('network down')
    const onError = vi.fn()

    await loadSettings(vi.fn(), onError)

    expect(onError).toHaveBeenCalledWith('network down')
  })

  it('calls onSettings, not onError, when /api/settings resolves', async () => {
    const settings = { 'deck.theme': 'paper' } as unknown as AllSettings
    vi.spyOn(api, 'get').mockResolvedValue(settings)
    const onSettings = vi.fn()
    const onError = vi.fn()

    await loadSettings(onSettings, onError)

    expect(onError).not.toHaveBeenCalled()
    expect(onSettings).toHaveBeenCalledWith(settings)
  })
})
