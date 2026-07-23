#!/usr/bin/env node
/**
 * Type-scale floor gate.
 *
 * Rejects any Tailwind arbitrary text size (`text-[Npx]`) below the pinned
 * 12px floor (`text-2xs`, 0.75rem -- see the PINNED TYPE-SCALE CONTRACT in
 * src/index.css) anywhere under src/. The named utilities exist precisely so
 * nobody needs to drop to an arbitrary value below them; if a spot still
 * needs something under 12px, that is a design decision, not a one-off.
 *
 * Run manually: node scripts/check-type-scale.mjs
 * (Not wired into `npm run build`/`lint` -- add it there if you want it to
 * block CI; left manual here since this lane doesn't own package.json.)
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'

const SCRIPTS_DIR = dirname(fileURLToPath(import.meta.url))
const ROOT = join(SCRIPTS_DIR, '..', 'src')

// Documented exceptions ONLY -- a component that has an explicit, reviewed
// design reason to render text under 12px (a micro-badge, not body/label/
// control text). Do not add to this list without also documenting why,
// right here.
const EXEMPT = [
  // ui/SourceMark.tsx: 11px mono citation numeral, a micro-badge rather
  // than body text -- see the comment at its className.
  join('ui', 'SourceMark.tsx'),
]

const PATTERN = /text-\[(\d+(?:\.\d+)?)px\]/g

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    const st = statSync(full)
    if (st.isDirectory()) yield* walk(full)
    else if (/\.(tsx?|css)$/.test(name)) yield full
  }
}

let failed = false
for (const file of walk(ROOT)) {
  const rel = relative(ROOT, file)
  if (EXEMPT.some((e) => rel === e)) continue
  const text = readFileSync(file, 'utf8')
  let match
  while ((match = PATTERN.exec(text))) {
    const px = Number(match[1])
    if (px < 12) {
      const line = text.slice(0, match.index).split('\n').length
      console.error(`${rel}:${line}: text-[${match[1]}px] is below the 12px floor -- use text-2xs or a larger named size`)
      failed = true
    }
  }
}

if (failed) {
  console.error('\nType-scale floor gate failed. See the PINNED TYPE-SCALE CONTRACT in src/index.css.')
  process.exit(1)
} else {
  console.log('Type-scale floor gate passed: no text-[Npx] below 12px.')
}
