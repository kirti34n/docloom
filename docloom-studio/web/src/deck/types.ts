/** TS mirror of docloom's IR (subset the UI touches). */

export interface Span {
  text: string
  bold?: boolean
  italic?: boolean
  code?: boolean
  link?: string | null
  cite?: string | null
}

export type RichText = string | Span[]

export interface ListItem {
  text: RichText
  level?: number
}

export interface Stat {
  label: string
  value: string
  delta?: string | null
}

export interface SeriesT {
  name?: string
  values: (number | null)[]
}

export interface Block {
  type: string
  id?: string | null
  // heading / paragraph / quote / callout
  text?: RichText
  level?: number
  attribution?: string | null
  style?: string
  // lists
  items?: ListItem[] | Stat[]
  // code
  code?: string
  language?: string | null
  // table
  header?: RichText[]
  rows?: RichText[][]
  caption?: string | null
  // image / artifact
  path?: string | null
  query?: string | null
  asset_id?: string | null
  alt?: string
  kind?: string
  artifact_id?: string | null
  // chart
  chart?: string
  title?: string | null
  labels?: string[]
  series?: SeriesT[]
}

export interface SlideT {
  layout: string
  id?: string | null
  title?: string | null
  subtitle?: string | null
  blocks?: Block[]
  right?: Block[]
  image?: Block | null
  accent?: string | null
  notes?: string | null
}

export interface SourceT {
  id: string
  title: string
  url?: string | null
  publisher?: string | null
  date?: string | null
}

export interface DocumentT {
  title: string
  subtitle?: string | null
  authors?: string[]
  date?: string | null
  slides?: SlideT[]
  blocks?: Block[]
  sources?: SourceT[]
}

export interface StudioTheme {
  name: string
  label?: string
  primary: string
  accent: string
  accent_2?: string
  background: string
  surface: string
  text: string
  muted: string
  font_heading: string
  font_body: string
  heading_weight?: number
  heading_tracking?: string
  radius?: number
  bg_style?: string
  divider_style?: string
}

export interface DeckPayload {
  ir: DocumentT
  theme_name: string
  brand_kit_id?: string | null
}

export interface ArtifactT {
  id: string
  notebook_id: string
  kind: string
  title: string
  version: number
  payload: DeckPayload
}

export interface Finding {
  rule: string
  severity: 'error' | 'warning' | 'info'
  where: string
  message: string
}

/** Theme JSON → CSS custom properties for the deck stage. */
export function themeVars(t: StudioTheme): Record<string, string> {
  return {
    '--primary': t.primary,
    '--accent': t.accent,
    '--accent-2': t.accent_2 ?? t.accent,
    '--bg': t.background,
    '--surface': t.surface,
    '--text': t.text,
    '--muted': t.muted,
    '--font-heading': `'${t.font_heading}', system-ui, sans-serif`,
    '--font-body': `'${t.font_body}', system-ui, sans-serif`,
    '--heading-weight': String(t.heading_weight ?? 650),
    '--heading-tracking': t.heading_tracking ?? '0',
    '--card-radius': `${t.radius ?? 8}px`,
  }
}
