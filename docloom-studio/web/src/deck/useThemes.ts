import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { StudioTheme } from './types'

let cache: StudioTheme[] | null = null

// overlay the active brand accent so preview matches export
async function loadThemes(): Promise<StudioTheme[]> {
  const [themes, brand] = await Promise.all([
    api.get<StudioTheme[]>('/api/themes'),
    api.get<{ accent?: string | null }>('/api/brand-kit').catch(
      () => ({ accent: null }) as { accent?: string | null }),
  ])
  return brand.accent
    ? themes.map((t) => ({ ...t, primary: brand.accent!, accent: brand.accent! }))
    : themes
}

export function useThemes(): StudioTheme[] {
  const [themes, setThemes] = useState<StudioTheme[]>(cache ?? [])
  useEffect(() => {
    if (cache) return
    loadThemes().then((t) => {
      cache = t
      setThemes(t)
    })
  }, [])
  return themes
}

export function themeByName(themes: StudioTheme[], name: string): StudioTheme | undefined {
  return themes.find((t) => t.name === name) ?? themes[0]
}
