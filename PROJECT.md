# docloom — Project Overview

docloom is two things that fit together. The first is **docloom**, an engine that turns
large-language-model output into finished documents: an LLM emits a schema-validated JSON
document, deterministic renderers consume it, and six real file formats come out the other end.
The second is **docloom-studio**, a free, local-first application built on that engine —
"NotebookLM crossed with Gamma" — where you add sources to a notebook, chat with cited answers,
and generate presentations, documents, spreadsheets, diagrams, infographics, and podcast audio.
The whole studio can run fully offline against a local model with no third-party account.

The engine is a Python library (package version 0.2.0) reachable through a CLI, a JSON API, and an
MCP server. The studio is a single FastAPI process that serves both a REST API and a built
single-page app on one port. They share the engine's rendering path but are otherwise distinct
codebases with distinct concerns.

---

## The Engine (docloom)

### The core idea

An LLM emits a **schema-validated JSON Document** (a Pydantic intermediate representation, the
"IR"). Deterministic renderers consume that IR. A deterministic **linter** checks layout,
citations, and contrast *before* any render happens, and its findings can loop back to the model
for self-correction. The pipeline is: LLM structured output → Document JSON → lint → deterministic
renderers, with findings feeding back.

### The IR

The IR is deliberately **non-recursive** and uses **plain tagged unions** — a `Literal` `type`
tag plus a `Union` that serializes to JSON-Schema `anyOf`. It is *not* built from Pydantic
discriminated unions, and it never emits `oneOf`. This is a hard design constraint, not a stylistic
one: Anthropic structured outputs reject recursive schemas, and both OpenAI strict mode and
Anthropic reject `oneOf`. For the same reason, lists carry a flat `level` integer for indentation
rather than nested children.

There are **14 block types** in the `Block` union:

- `heading`, `paragraph`, `bullets` (BulletList), `numbered` (NumberedList), `quote`, `code`,
  `table`, `image`, `callout`, `divider`, `chart` (Chart), `stats` (StatRow),
  `artifact` (Artifact), `diagram` (Diagram).

**The document model.** A single root `Document` carries three optional bodies, and may carry any
mix of them:

- `blocks` → reports, which render to DOCX / PDF / HTML / Markdown
- `slides` → decks, which render to PPTX
- `sheets` → workbooks, which render to XLSX

plus `title`, `subtitle`, `authors`, `date`, `logo`, and a `sources` list.

**Slides are layout intent, not geometry.** A `Slide` names one of **8 layouts** — `title`,
`section`, `content`, `two_column`, `quote`, `hero`, `image_left`, `image_right` — and carries
blocks, an optional right column, an optional image slot, an accent-color override, and speaker
notes. The renderer owns all coordinates; the IR never specifies pixel positions.

**RichText and citations.** A rich-text value is either a plain `str` or a list of `Span`. Each
`Span` carries `text` plus optional `bold` / `italic` / `code` / `link` / `cite`. Citations are
first-class: `Span.cite` references a `Source.id`, and helper methods `cited_ids()` and
`source_numbers()` produce stable, deterministic citation numbering.

**SafeStr.** At the IR boundary an `AfterValidator` strips C0 control characters, lone UTF-16
surrogates, and U+FFFE / U+FFFF. Those characters are forbidden in OOXML/XML and would crash or
corrupt the renderers, so they are removed before they can ever reach one.

### Rendering: six formats, one contract

Formats are dispatched from a `FORMATS` table:

| Key    | Module              | Output                          |
|--------|---------------------|---------------------------------|
| `pptx` | `render/pptx.py`    | PowerPoint                      |
| `docx` | `render/docx.py`    | Word                            |
| `xlsx` | `render/xlsx.py`    | Excel                           |
| `pdf`  | `render/typst.py`   | PDF (compiled in-process)       |
| `html` | `render/html.py`    | HTML                            |
| `md`   | `render/markdown.py`| Markdown                        |
| `typ`  | `render/typst.py`   | Typst source (7th key)          |

Every renderer module exposes the same contract: `render(doc, theme, out_path) -> Path`. PDF and
`.typ` both come from the Typst module — PDF is compiled fully **in-process via the Typst wheel**,
with no LaTeX, no headless browser, and no external binary. `to_typst(doc, theme)` returns the
`.typ` source, which the `typ` format key also writes directly.

### The linter

`lint(doc, theme)` returns machine-readable **Findings**, each with a `rule`, `severity`, `where`,
and `message`. There are three severities, and only `error` severity blocks a render (`has_errors`
is true when any finding is an error). Rules cover, among others:

- deck overflow (both a character budget and a physical-height budget)
- bullet count and length, title length, weak or verbless titles
- oversized tables, empty slides, block-variety, heading-level skips
- citation integrity, placeholder text, image-file existence
- sheet empty-formula cells
- WCAG-AA theme contrast
- 10 diagram-specific rules

The physical-height budget mirrors `render/pptx.py`'s fixed-size block geometry (`CHART_H_IN`,
`IMAGE_H_IN`, and friends) as **duplicated literal constants** rather than imports, so the linter
stays import-light. A drift guard in `test_reaudit_lint.py` imports the real constants from
`pptx.py` and asserts equality, so the mirrored copies cannot silently diverge. The linter also
imports `estimate_depth` from `render/diagram_svg.py` as the single source of truth for the
painter's layering algorithm; a former lint-local duplicate was removed, and the import guard now
raises loudly rather than falling back to a shadow copy.

### Theme

A `Theme` is **8 semantic tokens** — `primary`, `accent`, `background`, `surface`, `text`,
`muted`, `font_heading`, `font_body` — plus optional local font-file paths. Hex colors are
validated to `#RRGGBB`. Renderers map these tokens to each format's native color mechanism and must
never hard-code literal colors. `contrast_ratio()` implements the WCAG 2.x luminance formula.

### Interfaces: CLI and MCP

The **CLI** (program name `docloom`) has subcommands `render`, `lint`, `schema`, `guide`, and
`theme`. `render` takes `-f`/formats, `-o`/out, `--theme`, `--no-lint`, and `--diagram-sources`;
it refuses to write when there are error-severity findings unless `--no-lint` is passed (exiting
with code 2). `lint --json` exits 1 on errors. The `--diagram-sources` flag writes a `.drawio`
sidecar per Diagram into a `{stem}.diagrams/` directory; filenames are hardened against path
traversal — LLM-authored ids run through a `slug()` (word characters only, no separators or drive
letters), are de-duplicated, and a post-resolve containment assertion refuses to write outside the
target directory.

The **MCP server** (entry point `docloom-mcp`, stdio transport) exposes three tools:
`get_document_schema`, `lint_document`, and `render_document`. `render_document` refuses to render
on error findings unless `no_lint=True`, and resolves its output directory to an absolute path.

### Preparing schemas for LLMs

`llm_schema()` prepares the JSON Schema for structured output. It closes every object with
`additionalProperties: false`; strips numeric and string constraint keys (`min`/`max`/`minLength`/
`maxLength`/`pattern`, which Anthropic rejects but which Pydantic still enforces on parse); removes
editor-bookkeeping fields (`id`, `asset_id`, `artifact_id`) so the model cannot invent them; and
forces each `Literal` `type` tag into the required set.

`parse_llm_output()` is a lenient parser tuned for messy or local models. It strips markdown
fences, unwraps `{"document": ...}` envelopes, normalizes block-tag aliases
(`bulletlist` → `bullets`, `kpi` → `stats`, and others), collects every fenced JSON candidate and
returns the richest one that validates, and reduces plain-union `ValidationError` cascades down to
the errors of the tag-matching union member.

### Quality machinery (PPTX)

- **Measured autofit** (`render/textfit.py`) measures real wrapped-text extents against the real
  font files (via Pillow's `FreeTypeFont`, resolving faces through `pptx.text.fonts.FontFiles` at
  200pt) and bakes a fitted size into the runs. This is necessary because python-pptx's
  `normAutofit` is only recomputed when desktop PowerPoint opens the file. Every failure path (no
  Pillow, an unresolvable family, a corrupt font) degrades to a deliberately small size estimate
  and never raises. It **is** wired into `pptx.py`.

- **Geometric QA** (`render/qa.py`) is a reference-free, AutoPresent / SlidesBench-style audit over
  a *built* python-pptx `Presentation`: `qa/off-slide`, `qa/shape-overlap` (with containment
  filtering), `qa/font-family-sprawl`, `qa/palette-sprawl`, and `qa/low-contrast` (WCAG 2, not
  APCA). Every QA finding is severity `warning` by design, and it reuses the linter's `Finding`
  shape. Note: `qa.py` is a standalone auditing module — it is imported only by tests, never by the
  shipped render path.

- **DOCX table hardening** (`render/docx.py`): tables use fixed layout (`autofit=False`) with
  content-weighted column widths that are capped at a maximum, renormalized to the frame width, and
  water-fill-pinned to a minimum; the width is written on every cell (not just the column) so Word
  honors it; the header row repeats (`w:tblHeader`) and rows use `w:cantSplit`. If no split can
  honor the floor, it falls back to Word autofit.

### Optional rasterizer

An optional `[diagrams]` extra installs an SVG rasterizer (**resvg**, via `resvg-py`) behind
`render/raster.py`, so charts and SVG diagrams can embed as real pictures in PPTX and DOCX
(python-pptx and python-docx have no SVG decoder). Without it, everything still renders: a chart
falls back to a titled data table and an SVG diagram is skipped or shown as a placeholder.
`raster.svg_to_png` returns `None` rather than raising when the extra is absent. Pillow (used by
autofit) and resvg are **both optional** — a core install renders all six formats without either.

### Public API

Package version is **0.2.0**. `docloom/__init__.py` re-exports the public surface: `Document` and
all block models; `lint` / `has_errors` / `Finding`; `render` / `render_diagram` / `FORMATS` /
`RenderError`; `Theme` / `DEFAULT`; `llm_schema` / `parse_llm_output` / `AUTHORING_GUIDE`;
`diagram_hash` / `ensure_ids`.

---

## The Studio (docloom-studio)

docloom-studio is a free, local-first AI document studio: add sources to a notebook, chat with
cited answers, and generate artifacts, all runnable fully offline with a local model and no
third-party account.

### The user flow

Create a notebook and add sources — a file, a URL, or pasted text — or let an agent do free web
research (it plans searches, fetches pages, and keeps them as cited sources, with no API key
required). Grounded chat then retrieves relevant chunks via embeddings and ranking, and every
answer cites where it came from.

**Five one-click "guides"** are preset grounded generations: Study guide, Briefing, FAQ, and
Timeline (all `kind='doc'`), plus Mind map (`kind='diagram'`, a Diagram-IR diagram).

### Six artifact kinds

The studio generates six kinds of artifact through six pipelines: **decks** (presentations),
**documents**, **spreadsheets**, **diagrams** (the engine's coordinate-free Diagram IR, laid out
and rendered server-side, edited in the embedded draw.io canvas), **infographics**, and
**two-host podcast** audio overviews. Only the three Document-IR kinds — **decks** (PPTX),
**documents** (DOCX / PDF / HTML / MD), and **spreadsheets** (XLSX) — export through docloom into
the six file formats; a diagram is also embeddable inline in a deck or document (rendered as native
PPTX shapes or a vector SVG). **Infographics** render client-side to SVG/PNG and are carried into
those formats when embedded as Artifact blocks; podcast **audio** is synthesized straight to `.wav`
and never touches docloom's renderers.

A **brand kit** (logo, accent color, fonts) is applied to every generation and every export.

### Server and auth

The server is a single FastAPI process that mounts the built SPA and the API on one port. Routers
included are `auth`, `notebooks`, `sources`, `assets`, and `artifacts`, plus inline
settings / providers / themes / layout endpoints. Host and port default to **127.0.0.1:8899**,
overridable via `DOCLOOM_STUDIO_HOST` / `DOCLOOM_STUDIO_PORT`; startup opens a browser unless
`DOCLOOM_STUDIO_NO_BROWSER` is set.

Authentication is email + password. Passwords are hashed with stdlib **scrypt**
(n=2¹⁴, r=8, p=1, dklen=32) and stored as `scrypt$salt$hash`; verification uses
`hmac.compare_digest`. Sessions are opaque `secrets.token_urlsafe(32)` tokens, and only their
SHA-256 is persisted in `auth_sessions`, so a database leak cannot be replayed. The `ds_session`
cookie is httponly, `samesite=lax`, with a 30-day TTL.

On first visit the user registers a **local** account, and everything they create lives in a
workspace scoped to that login.

### Multi-tenancy and authorization

The tenancy model is **users → workspaces → notebooks → sources / artifacts**. Every new user gets
a "My workspace" on registration. Authorization walks the ownership chain and returns **404, not
403,** on cross-tenant access, so IDs cannot be used to probe whether another tenant's records
exist.

### Storage

Storage is driver-agnostic over **SQLite** (default, at `data_dir()/studio.db`) and **Postgres**
(`DOCLOOM_DB_URL=postgres://...`). The same `?`-placeholder SQL and a single `MIGRATIONS` list
drive both through a translation layer; SQLite runs with WAL and `foreign_keys ON`. The schema has
**8 migrations**, and core tables include `notebooks`, `sources`, `artifacts`,
`artifact_versions`, `jobs`, `assets`, `brand_kits`, `settings`, `users`, `workspaces`,
`auth_sessions`, `user_settings`, `chat_messages`, `health_probe`, and `job_events`.

---

## Architecture & Data Flow

The path from raw material to finished file: **source → ingest → embeddings → retrieval → grounded
generation → validated IR → deterministic render → export.**

### Sources and ingestion

Source kinds are `file` | `url` | `text` | `research`. File uploads are validated against an
allowlist (pdf, docx, pptx, xlsx/xlsm, csv, html/htm, epub, txt, md/markdown, rst, text, json, log)
with a **50 MB streaming cap** and filename / path-traversal guards.

Ingestion (`ingest.py`) parses with lightweight parsers — pdfplumber / pypdf, python-docx
(including tables), python-pptx (including speaker notes), openpyxl, ebooklib for EPUB, trafilatura
for HTML, and the stdlib CSV reader — sanitizes control / bidi / zero-width characters, then chunks
at roughly **1000 characters with 150 overlap**.

Two guards protect the ingest path:

- **URL SSRF guard.** URL ingestion accepts only http(s) and rejects hosts that resolve to
  private, loopback, link-local, or reserved addresses. It re-validates every redirect hop by hand
  and re-checks the actual peer address to defeat DNS-rebinding TOCTOU. YouTube links take a
  transcript path only when the URL host is genuinely YouTube.
- **Zip-bomb guard.** ZIP-container formats (docx, pptx, xlsx, epub) are checked for entry count,
  total inflated size (200 MB), and compression ratio before parsing.

### Context mode

Each source has a `context_mode` of `full` | `insights` | `excluded`. `insights` feeds an
LLM-generated 3–5 sentence standing summary (best-effort, embedded as `summary.npy`) instead of
every chunk at retrieval time; `excluded` drops the source entirely.

### Embeddings and retrieval

Embeddings come from the configured provider (default Ollama `nomic-embed-text`) and are stored as
one `<name>.npy` array per source, batched at 64 to respect provider input caps. Retrieval
(`embeddings.py`) is **hybrid and backend-agnostic**: dense cosine similarity over the embeddings
is fused with a pure-Python **BM25** lexical rank via **Reciprocal Rank Fusion** (k=60), followed
by near-duplicate dedup and a depth-then-breadth per-source coverage floor. It is brute-force —
described in-code as "instant to ~100k chunks; add hnswlib past that."

Sources whose stored vector count or embedding dimension no longer matches their chunks — for
example after switching embedding models — are flagged `status='stale'` so the UI can prompt a
re-ingest, rather than silently returning nothing.

### Grounded chat

Grounded chat retrieves **k=12** evidence chunks, streams **NDJSON** frames (one `evidence` frame,
then `token` frames, then `done`), replays the last 6 turns of persisted history to the model, and
folds recent user turns into the retrieval query. Answers and their evidence are persisted in
`chat_messages`. Chat evidence surfaces chunk-level detail (`n`, `source_id`, `source_title`,
`page`, `section`, and text truncated to 400 chars) for citation hovercards, but **generation
grounding cites source-level ids**: `generation_context` builds one docloom `Source` per distinct
source id, and the model sets `Span.cite` to those ids.

### Generation pipelines

The six pipelines live in `generate.py`. The deck, doc, and sheet pipelines run
**outline-then-per-unit**: one LLM outline call, then one small-schema LLM call per slide / section
/ sheet, each with independent retries, per-unit events, and per-unit targeted retrieval. A single
flaky call degrades to a skeleton or placeholder unit rather than sinking the whole job.

Every schema-shaped generation goes through `generate_validated`: complete → lenient parse →
optional lint → feed findings back → retry, for a maximum of 3 rounds, with per-round temperature
escalation.

A deterministic **citation gate** strips any `Span.cite` the model invented that is not a real
source id before saving, so docloom's cite/unknown-source lint does not flag the artifact and no
hallucinated references ship.

### The other three pipelines

- **Diagram** (studio): generates docloom's coordinate-free **Diagram IR** (`DiagramGen`: nodes /
  edges / groups / direction), lint-validates it by actually running the engine's `solve()` layout
  (rejecting dangling edges, duplicate ids, and otherwise-unlayoutable graphs), and saves
  `{type: 'diagram_ir', diagram_ir, theme_name, layout: 'native', render: 'svg'}`; the engine primes
  `render.svg` / `render.png` server-side via `render_diagram`, and the diagram is edited in the
  embedded draw.io canvas.
- **Infographic**: emits an `InfographicSpec` (a style, a title, and 3–6 items) mapped to one of
  four curated AntV templates (list / steps / pyramid / grid), with deterministic text clamping to
  fixed card limits.
- **Podcast**: generates a two-host script (A = host, B = guest); audio is a best-effort
  enrichment via optional local **Kokoro** TTS producing a single WAV, and ships the transcript
  even if TTS is missing.

Optional **AI slide-image generation** ("Nano Banana", Gemini `gemini-2.5-flash-image`) fills
unmatched hero / image slots. It is a separate `ImageProviderConfig` with its own enable gate,
defaults **OFF**, and a failure leaves the slot empty rather than sinking the deck.

### Versioning, jobs, and export

Artifacts are **versioned**: `save_artifact` bumps the head version, snapshots into
`artifact_versions`, and flips status to `ready` in one transaction, allocating the version via
`UPDATE ... RETURNING` so concurrent saves get distinct versions. Revert re-saves an old snapshot
as a new version. Build status is `building` | `ready` | `failed`; a fresh stub starts `building`
and only reaches `ready` once a payload is saved.

Background work runs as **in-process async jobs** with an SSE event stream. Because tasks live in
the starting process's event loop, a restart loses them, and `reconcile_jobs()` marks every
DB-`running` job `failed` (and `building` artifacts failed) at startup. Jobs carry a DB heartbeat
column, refreshed every 30 seconds, enabling a lease-mode reconcile that can distinguish a crashed
node's zombie from a sibling node's live job on a shared Postgres. Job body concurrency is bounded
(default 4, `DOCLOOM_MAX_CONCURRENT_JOBS`).

**Export** renders the docloom IR to one of docloom's formats after baking `asset://` paths and
stamping the active brand logo; it lints with the theme and refuses (HTTP 422) if any
error-severity finding remains. `irx.py` is the single studio-envelope ↔ docloom-IR
down-conversion point: the payload is `{ir, theme_name, brand_kit_id}`, and `bake()` resolves
`asset://{id}` references and diagram/infographic renders to real files, with every resolution
scoped to the exporting `user_id` (a `None` user resolves nothing, rather than leaking another
tenant's files).

An artifact's own media, renders, and exports are served only through per-artifact,
ownership-checked routes confined to that artifact's directory; an older shared `/api/files?path=`
route, which could read `studio.db` or another tenant's files, was replaced.

---

## Output Formats & Quality

Six formats come from the engine: **PPTX, DOCX, XLSX, PDF, HTML, Markdown** (plus the `typ` Typst
source key). Beyond simply producing them, docloom invests in making them *good*:

- **Native, editable objects, not pictures.** PPTX **charts** are native editable charts
  (`add_chart` with `CategoryChartData` / `XL_CHART_TYPE`), not rasterized images — the IR's
  `Chart` docstring states PPTX renders native editable charts while other formats fall back to an
  image or table. PPTX **diagrams** (on the native path) emit native PowerPoint shapes with real
  connector glue (`p:cxnSp` with `stCxn` / `endCxn`), so dragging a node re-routes its connector.
- **Measured autofit** bakes a real, font-measured size into PPTX text so it fits without waiting
  for PowerPoint to reflow it.
- **Table hardening** in DOCX pins fixed, content-weighted, water-filled column widths on every
  cell, repeats header rows, and prevents rows from splitting across pages.
- **WCAG 2.x contrast.** `theme.contrast_ratio()` computes the standard
  (L1 + 0.05) / (L2 + 0.05) ratio with 0.2126 / 0.7152 / 0.0722 luminance weights.
  `qa.check_contrast` flags any text run below **4.5:1** (WCAG 2 AA normal text) as a
  `qa/low-contrast` warning. APCA was deliberately not used.
- **Geometric QA pass** audits a built presentation for off-slide shapes, overlap, font and palette
  sprawl, and low contrast — all as warnings, so quality signals never hard-block a deck.

---

## Diagrams

Diagrams are **one system**: the studio generates the engine's coordinate-free Diagram IR, edits it
in a self-hosted draw.io canvas seeded from that IR, and the engine lays it out and renders it. An
older client-side D2 editor survives only to open diagrams authored before the switch.

### Studio: the draw.io editor, seeded from the IR

In the studio app, `run_diagram_pipeline` generates the coordinate-free Diagram IR (`DiagramGen`)
from a prompt, validates it by running the engine's `solve()` layout, and saves
`{type: 'diagram_ir', diagram_ir, theme_name, layout: 'native', render: 'svg'}`. The engine primes
`render.svg` / `render.png` server-side via `render_diagram` (PNG needs the `[diagrams]` resvg
extra). The user edits the diagram in **the real [draw.io](https://www.drawio.com) editor, embedded
in the studio and running fully offline**: the IR is seeded into draw.io as mxGraph XML on first
open, and every edit writes back a `render.svg` through the same path decks bake, so the diagram you
edit is the one your deck ships. A legacy D2/Mermaid text editor remains only for diagrams authored
before the switch (a legacy `source` / `mermaid_src` payload); newly generated diagrams never take
that path.

### Engine: the coordinate-free Diagram IR

The core docloom engine has a **separate** coordinate-free architecture-diagram subsystem.
`Diagram` / `DiagramNode` / `DiagramEdge` / `DiagramGroup` carry only structure — nodes, edges,
groups, labels, and a direction (LR/TB). There are **no** `x` / `y` / `row` / `col` / `pos` /
`size` / `route` / `via` / `labelAt` fields; the docstring explicitly forbids hand-placement
geometry ("No row/col/pos/size/route/via/labelAt-style hand-placement fields, ever"). `DiagramNode.type`
uses an **archify-inspired painter's vocabulary of exactly seven kinds**: `service`, `client`,
`store`, `queue`, `security`, `cloud`, `external` (default `service`). `DiagramGroup` geometry is
**derived** (the bounding box of its members plus padding), never authored; its kinds are `region`
or `security-group`.

**One solver, three emitters.** `diagram_svg.solve()` runs layout **once**, producing a
`SolvedDiagram`; the SVG emitter (`paint_svg`), the native-PPTX emitter (`diagram_pptx`), and the
`.drawio` emitter (`drawio.py`) all consume that same solved geometry without re-laying it out.
Determinism is explicit — no randomness, no order-sensitive iteration. The solver is **pure stdlib**
with a full Sugiyama-style pipeline written by hand in Python: rank assignment (longest-path
layering on an acyclic projection), proper-graph dummy nodes, barycenter crossing-minimization,
median coordinate straightening, orthogonal routing, and a grid-packing "bands" pass. There is no
external layout engine, no DOM, and no browser: the studio generates and renders through this same
solver server-side.

**The .drawio export is a derived, one-way export.** docloom never reads a `.drawio` file back.
Repositioning shapes in draw.io forks the file; regenerating overwrites it with no merge. An IR
content hash — `diagram_hash(d)`, the first 12 hex chars of the SHA-1 of the Diagram's JSON — is
stamped as an XML comment for drift detection only, and `render_drawio` is byte-deterministic given
`(solved, theme, d)`.

**Editability is a two-tier contract.** Tier 1 is the Diagram block in the document JSON — the only
source of truth. Tier 2 is everything derived (SVG, PNG, native PPTX shapes, `.drawio`), regenerated
on every render. Nothing today reconciles a Tier-2 edit back into Tier 1.

**Legibility guarantees.** Node labels follow a "nothing authored is lost" rule: `fit_label` wraps
the label and steps the font down (14.5 → 11.0 pt, then up to three lines at 11 / 10.5 pt) and
**never** ellipsizes or drops a word from the label. On the PPTX side an 8 pt node-label floor
(`MIN_LABEL_PT = 8.0`) is enforced: when a diagram cannot reach 8 pt even after stepping down a
three-rung detail ladder (full → label+sub → label), it falls back to a raster image at the
sparsest layout **and** emits a warning — an earlier silent-degradation bug is fixed at HEAD. All
five report formats render an IR diagram and none silently drop it (HTML inlines vector SVG with an
`aria-label`; DOCX degrades to a visible `[diagram: alt]` placeholder when resvg is absent).

### The unification (done)

The studio has been re-pointed at the Diagram IR: `run_diagram_pipeline` emits `type: 'diagram_ir'`,
the engine solves and renders it server-side, and the embedded draw.io canvas edits it, so the
studio and the engine now share one IR and one render path. The legacy D2/WASM editor remains only
to open pre-switch artifacts.

---

## Running It

One command from the repository root launches everything:

- **Windows (PowerShell):** `studio.ps1`
- **macOS / Linux / Git Bash:** `studio.sh`

The launcher installs dependencies, builds the frontend, and starts a single-process server that
serves both API and SPA on one port, then opens a browser. The default port is **8899**
(`http://127.0.0.1:8899`), overridable with `-Port` / `--port` (passed through as
`DOCLOOM_STUDIO_PORT`).

**Prerequisites:** `uv`, Node 22+, and npm on `PATH`; the launcher exits with an install hint if
any is missing.

**Launcher flags:** `-Rebuild` / `--rebuild` (force a fresh web build), `-Setup` / `--setup`
(force a dependency reinstall), `-Port` / `--port`, and `-NoBrowser` / `--no-browser` (sets
`DOCLOOM_STUDIO_NO_BROWSER`).

On every start the launcher verifies that the resvg SVG rasterizer is importable and self-installs
`resvg-py>=0.3.3` if it is missing — without it, the studio's browser-rendered diagrams, charts,
and infographics can export as silent blanks.

**Docker:** build from the repository root with `docloom-studio/Dockerfile` and run
`-p 8899:8899` with a `/data` volume.

---

## Configuration

### Providers

The provider layer supports these kinds: **ollama, llama-server, lmstudio, openai, anthropic,
gemini**, each with its own HTTP shape (OpenAI-compatible chat completions, Ollama `/api/chat`,
Anthropic `/v1/messages`, and Gemini's native `generateContent` / `streamGenerateContent` /
`embedContent`). Generation and embeddings are configured separately inside the running app's
Settings, and the model list is fetched live from the base URL.

The **in-code default** generation provider is **Ollama** with model `qwen3.5:9b` and `max_tokens`
32768; embeddings default to Ollama `nomic-embed-text`; both point at
`http://localhost:11434`. Gemini is a fully supported generation provider (its native Generative
Language API); `gemini-2.5-flash` allows 65536 output tokens, so `max_tokens` can be raised through
the provider setting. Any non-default provider/model — for instance running Gemini as the default —
is a per-machine stored DB setting, not the shipped code default.

Settings resolve in order: per-user override (`user_settings`), then global (`settings`), then the
built-in `DEFAULTS`.

Other configured defaults: podcast TTS defaults to `kokoro` (local voices), language `a` (American
English), host voice `af_heart`, guest voice `am_michael`. Image generation ("Nano Banana",
`kind='gemini'`, model `gemini-2.5-flash-image`) is a separate cloud/paid surface for illustrative
slide images only, disabled by default. The default deck theme is `paper`. Optional research
(Tavily) and asset (Pexels) API keys default empty.

### Local-first storage and data directory

SQLite is the default, with nothing to configure; Postgres (via `DOCLOOM_DB_URL`) is available for
multi-node deployments. The data directory — holding the SQLite DB, uploaded sources, assets,
exports, and cache — is created under `%LOCALAPPDATA%/docloom-studio` on Windows or
`~/docloom-studio` elsewhere, overridable via `DOCLOOM_STUDIO_HOME`.

### Secrets at rest

Secret settings (`api_key` fields, `research.tavily_key`, `assets.pexels_key`) are **Fernet-encrypted
at rest** and never sent to the client in cleartext: GET masks them as `__stored__`, and PUT treats
the mask as "keep the stored value." The Fernet key comes from `DOCLOOM_SECRET_KEY` or an
auto-generated `data_dir()/secret.key` (chmod 600). Ciphertext is tagged `enc:`; legacy plaintext
passes through and is re-encrypted on the next save.

### Optional extras

- **Engine:** installed with `[pdf,diagrams]` extras; `[diagrams]` adds the resvg rasterizer.
- **Studio:** `[dev]` (pytest), `[ingest]` (EPUB + YouTube-transcript parsing), `[podcast]`
  (kokoro + soundfile audio), `[postgres]`.

A local model runs the studio fully offline; a hosted API key is optional, and the account
registered on first visit is a local account.

---

## Design Principles

- **Validated IR, not code.** The LLM produces a schema-validated data structure, never
  executable output. The schema is deliberately shaped (non-recursive, plain tagged unions, no
  `oneOf`, stripped constraints) to survive real structured-output constraints from Anthropic and
  OpenAI.
- **Deterministic renderers.** Given an IR and a theme, rendering is reproducible. Diagrams are
  solved exactly once and every emitter consumes the same geometry; `.drawio` output is
  byte-deterministic.
- **Lint before render, and let findings self-correct.** A machine-readable linter runs before any
  file is written; only `error` severity blocks a render, while quality signals stay as warnings so
  they never hard-block a whole document. Findings can loop back to the model.
- **Nothing authored is lost, nothing drawn is unreadable.** Node labels are never truncated or
  word-dropped; when text cannot be made legible at the layout's density, the system steps down
  detail and warns rather than silently shipping something unreadable.
- **Local-first and multi-tenant-safe.** The studio runs offline against a local model with a local
  account; cross-tenant access returns 404, session tokens are stored only as hashes, secrets are
  encrypted at rest, and file serving is confined to ownership-checked, per-artifact routes.
- **Graceful degradation everywhere.** Optional dependencies (Pillow for autofit, resvg for
  rasterization, Kokoro for TTS) improve output when present and degrade to a safe fallback — never
  a crash — when absent.
