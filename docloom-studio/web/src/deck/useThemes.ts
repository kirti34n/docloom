import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { StudioTheme } from './types'

let cache: StudioTheme[] | null = null

type BrandColors = { primary?: string | null; accent?: string | null }

// overlay the active brand's primary/accent so preview matches export; each
// role only overrides if the user actually set it, never one for the other,
// or every theme collapses to the same flat color
async function loadThemes(): Promise<StudioTheme[]> {
  const [themes, brand] = await Promise.all([
    api.get<StudioTheme[]>('/api/themes'),
    api.get<BrandColors>('/api/brand-kit').catch(() => ({}) as BrandColors),
  ])
  if (!brand.primary && !brand.accent) return themes
  return themes.map((t) => ({
    ...t,
    ...(brand.primary ? { primary: brand.primary } : {}),
    ...(brand.accent ? { accent: brand.accent } : {}),
  }))
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
