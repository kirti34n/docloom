<!-- krt -->
# docloom studio, design direction

The working name for this direction is **the loom**, and it is not decoration: it is the product's
actual structure. Sources are the **warp**, fixed and indexed, running through everything. A
generation (an answer, a deck, a report) is the **weft**, one pass that crosses those threads.
A citation is a **crossing**. Every visual device below encodes that and nothing else.

The app must read as an instrument on a workbench, not an admin panel.

## Tokens

Defined once in `src/tokens.css`, consumed everywhere. Never hardcode a hex in a component.

| Token | Value | Role |
| --- | --- | --- |
| `--paper` | `#FBFAF7` | app ground. A cool bond white, not cream. |
| `--vellum` | `#F2F1EC` | panels, rails, inset surfaces |
| `--rule` | `#E3E1DA` | every hairline. 1px, never 2. |
| `--ink` | `#14161A` | body text, headings, primary buttons |
| `--ink-soft` | `#6A6E76` | secondary text, labels |
| `--woad` | `#1F3D63` | structure: active nav, links, focus rings, warp threads |
| `--brass` | `#A9722C` | the signature. Citations, the weft, one primary action per screen. |
| `--madder` | `#9E3A26` | lint errors, destructive actions, failures |
| `--stage` | `#101319` | dark editor canvas |
| `--stage-rule` | `#262B34` | hairlines on the dark canvas |

Brass carries the design and must stay scarce: if more than about one twentieth of a screen is
brass, cut it back. Woad does the structural work. Ink does the reading.

## Type

Three faces, all bundled through `@fontsource` so the app stays offline.

- **Fraunces** (display): screen titles, the wordmark, empty-state headings. Restraint: nothing
  under 20px is Fraunces.
- **IBM Plex Sans** (body/UI): everything you read or click. Replaces Inter.
- **IBM Plex Mono** (utility): eyebrows, source indices, versions, D2 source, numeric cells.
  Eyebrows are uppercase, 11px, `letter-spacing: 0.08em`, `--ink-soft`.

Scale: display 28/34, h2 20/28, body 14/22, small 12.5/18, mono-label 11/16.

## Geometry

`--radius: 6px` on cards and inputs, `--radius-sm: 4px` on controls. Nothing is a pill.
Separation comes from hairlines, not shadows. One shadow only, on genuinely floating things
(menus, toasts, the command palette).

## The signature: warp and crossings

Three components carry the whole identity. Build them well and keep the rest quiet.

**1. `<WarpRail>`, the sources panel.** Each source has a stable index rendered in mono (`01`,
`02`) and a 2px vertical thread on its left edge, woad when enabled, `--rule` when excluded.
The rail is the warp: the same indices appear anywhere a source is referenced, app-wide.

**2. `<SourceMark n>`, a citation.** A small mono numeral in brass with a hairline underline,
set as a superscript. It appears in chat answers, in generated docs, on slides, and in the build
view. Hovering it lights the matching thread in the warp rail. Clicking it opens the source
reader scrolled to the cited chunk. A citation the reader cannot follow is not a citation.

**3. `<WeaveProgress>`, the build view.** This replaces the stage rail while a job runs. Draw one
vertical warp line per enabled source. As each slide or section lands, draw a weft line across
them, knotted at the sources that unit cited. The document is visibly woven out of the user's own
material while they watch. Draw with `stroke-dashoffset` over `--dur-slow`; respect
`prefers-reduced-motion` by rendering the final state without the draw.

## Writing

Sentence case everywhere. An action keeps its name through the whole flow: the button that says
Generate produces a toast that says Generated. Errors say what happened and what to do next, in
the interface's voice, and never apologize. Empty screens are invitations with a single action.
Do not use em dashes or en dashes; use commas, colons, or periods.

## Quality floor

Responsive to 640px. Visible keyboard focus on every interactive element (`--woad` ring, 2px,
2px offset). Reduced motion respected. No raw JSON ever reaches a user's eye.
