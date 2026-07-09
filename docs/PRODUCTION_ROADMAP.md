# docloom-studio — Production-Grade Engineering Roadmap

## 1. Executive verdict

The **core is genuinely strong and tested**: the outline→per-slide/section generation pipeline, the deterministic citation gate (strips hallucinated source refs), native-shape PPTX/DOCX/XLSX export with chart fallback chains, and the IR/asset-baking layer are production quality. What surrounds that core is **early-prototype**: ingestion silently fails on most file types and relies on vector-only retrieval that loses exact terms and whole sources; asset/logo placement is broken in exactly the format users care about most (PPTX title logo is dropped, fonts never embed despite the UI claiming they do); the podcast/audio feature is entirely absent; the UI renders in fallback system-ui because *no font files ship*, uses native `alert()` for errors, and has no source reader or clickable citations; and the platform layer (WAL/concurrency, job durability, auth, secrets, deploy, CI) barely exists. The honest distance to "production-grade": roughly **one focused quarter** to a robust single-user/self-hosted platform (Track A), plus the flagship podcast build — *not* the multi-tenant SaaS rewrite, which is a separate product decision. The fastest perception win is Phase 0 + fonts + citation UX; the highest accuracy lever is hybrid retrieval.

---

## 2. Phased plan

### Phase 0 — Quick wins (2-4 days)
High-visibility correctness and trust fixes, almost no new architecture.

| Change | Files |
|---|---|
| Ship self-hosted `.woff2` fonts + `@font-face` (makes every `font-display` class start working, zero component edits) | new `web/public/fonts/`, `web/src/tokens.css:22-24`, `index.css:2`, `index.html` head |
| Set tab title `web` → "docloom studio" | `index.html:6` |
| Replace all `alert()` with a toast provider surfacing `ApiError.message` | `App.tsx`, `DeckEditor.tsx:108`, `DocEditor.tsx:104`, `SheetEditor.tsx:85`, `DiagramEditor.tsx:98`, `InfographicEditor.tsx:115` |
| Add `catch` to chat streaming → error bubble + retry instead of dead spinner | `ChatPanel.tsx:51-64` |
| Distinguish error-state from empty-state (stop `.catch(()=>setX([]))`) | `NotebooksList.tsx:16-20,43`, `Settings.tsx:64` |
| **Render PPTX title-slide logo** (currently silently dropped) | `docloom/src/docloom/render/pptx.py:510-524` |
| Stop the false "fonts embed in PDF" upload claim (or wire fonts — see P1) | `assets.py:72` |
| SPA path-traversal confinement (`resolved not in dist.parents → 404`) | `main.py:135-142` |
| Stop echoing `api_key` in `GET /api/settings`; mask secret fields | `settings.py:63-69`, `main.py:42-44` |
| Remove dead `research.tavily_key` / `assets.pexels_key` (read nowhere) | `settings.py:38-39`, `web/src/screens/Settings.tsx:15-16` |
| WAL + busy_timeout + `synchronous=NORMAL` in `_connect()` (removes ~90% of lock risk) | `db.py:76-80` |

**Done looks like:** app renders in real display type; no OS dialogs; a downed backend shows an error card not a fake empty state; a generated PPTX shows the brand logo; no plaintext key leaves the server; concurrent uploads stop throwing `database is locked`.

---

### Phase 1 — Core correctness (2-3 weeks)
Asset placement + ingestion breadth + retrieval accuracy + the NotebookLM-grade citation/source loop + persisted chat.

**1a. Asset placement made correct and user-controlled**
- Wire uploaded fonts into renderers: `typst.compile(..., font_paths=[...])` (`typst.py:353`), `@font-face` data-URIs in `html.py:_css`, and a `font_asset_id`/`font_heading`/`font_body` path brand-kit → `irx.py:33-34 to_docloom_theme`. Add a font field to `BrandKit` (`assets.py:132-134`).
- Per-slide corner/footer logo option across PPTX + report renderers (`pptx.py:_render_slide:647-671`, report renderers), driven by a new brand field — today only the title is even attempted.
- **Let users bind an asset to a slide/slot**: add `image` to `ADD_TYPES` (`web/src/deck/EditableSlide.tsx:9-15`), an asset-picker in `Inspector.tsx`, set-image action in `blocks.tsx:164-176`/`deckStore.ts newBlock:45`.
- Fix the naive resolver: score threshold + type separation (logos out of the content-image pool) in `assets.py:116-127`.
- Deepen brand theming: `apply_brand` (`assets.py:152-158`) should run a contrast check and expose brand fonts, not just overwrite `primary`/`accent`.
- Add **render-level tests** that export PPTX/DOCX/PDF and assert the picture part exists (this is what would have caught the dropped logo) — `tests/test_assets.py`.

**1b. Universal ingestion (Phase-1 subset — cheap, high-value)**
- New parsers as `elif` arms at `ingest.py:127-135` beside `parse_docx`: `parse_pptx` (python-pptx, *already installed*), `parse_xlsx` (add `openpyxl`), structured CSV (stdlib `csv`), EPUB (`ebooklib`), and route `.html` uploads through existing `trafilatura`.
- Extend `parse_docx` to include tables/headers/text boxes (`ingest.py:60-64`).
- Upload guardrails in `add_file` (`sources.py:44-58`): extension allowlist + streamed size cap, reject early.
- Encoding sniff (`charset-normalizer`) in the else-branch (`ingest.py:132-135`).
- YouTube link detection in the URL route → `youtube-transcript-api` (small dep, big UX win).

**1c. Retrieval accuracy (the biggest "accurate document" lever)**
- **Hybrid retrieval**: add SQLite FTS5 full-text index (stdlib sqlite3) alongside cosine in `embeddings.retrieve` (`embeddings.py:58-93`), fuse with RRF. Fixes poor retrieval of exact terms/IDs/numbers.
- **Per-section retrieval** keyed on section/slide intent instead of one static `context_block` reused for every section (`generate.py:249,275`).
- **Content dedup** in `chunk_text` + a per-source coverage floor in `retrieve` so "research all" can't collapse into one verbose source.
- **Surface the silent stale-vector skip** (`embeddings.py:71-72` `continue`) as a real per-source health status; re-embed on mismatch — today unretrievable sources still show `ready`.

**1d. Citation UX + source reader + persisted chat (the NotebookLM moment)**
- Make citation `<sup>` a real `<button>` (`ChatPanel.tsx:23-32`); build `web/src/notebook/SourceReader.tsx` that fetches source content and scrolls-to + highlights the cited passage (evidence already carries `source_title`/`page`/`text`). Lift `selectedCitation` into `NotebookWorkspace.tsx:64-72`; reader toggles with the Create panel. Likely needs a backend source-content endpoint.
- Persist chat turns (currently only `useState`, `ChatPanel.tsx:40`) — DB-backed conversation so reload/navigate doesn't lose it. This also unblocks the persisted-chat UI.
- Skeletons where data loads (extract the proven shimmer from `BuildView.tsx:136-147`): `NotebooksList.tsx:43`, `NotebookWorkspace.tsx`, `SourcesPanel`.

**Effort:** ~2-3 weeks. **Done looks like:** a user can upload a PPTX/XLSX/EPUB/CSV/YouTube link and it's parsed correctly; drop a chosen logo/photo into a chosen slide and see it in the exported PPTX with the right font; ask a question, click a citation, and land on the highlighted passage in a source reader; reload and keep the conversation; generated docs cite exact figures because retrieval is hybrid + per-section.

---

### Phase 2 — Flagship: Podcast / audio overview (1-2 weeks)
Entirely new capability; `payload_json` and free-text `artifacts.kind` mean **no DB migration**.

- **Script**: new `run_podcast_pipeline` in `generate.py` reusing `generation_context` grounding; schemas `PodcastTurn{speaker, text}` + `PodcastScript{title, turns}` via `generate_validated`; 2-host grounded dialogue prompt; lint 6-40 alternating turns.
- **TTS**: new `docloom_studio/tts.py`, backend chosen by a `provider.tts` setting (parallels `provider.generation`). Synthesize per turn, concat via ffmpeg/pydub to `data_dir()/artifacts/{id}/audio.mp3`, emit per-turn SSE progress.
- **Artifact/serve**: register `"podcast"` in `artifacts.py:36-38`; payload `{script, audio_path, duration_s, voices}`; `GET /api/artifacts/{id}/audio.{ext}` FileResponse **with HTTP range** for scrubbing (mirror `artifacts.py:199-204`).
- **UI**: `web/src/screens/PodcastEditor.tsx` + route `n/:notebookId/podcast/:artifactId` (`main.tsx:16-32`); `<audio controls>` + transcript from `payload.script.turns`; click-turn-to-seek (store per-turn offsets during concat); add tile + `KIND_ICON` in `ArtifactsPanel.tsx:11-25`. Transcript edit → re-synthesize via same job.
- **Smallest viable path first:** Piper (2 preset voices) + `PodcastScript` + ffmpeg concat + `<audio>` + static transcript. Per-turn re-synth, seek-sync, cloud voices layer on after.

**Done looks like:** from a notebook, generate a grounded 2-host audio overview, play it in-app with a synced clickable transcript, and export the mp3.

---

### Phase 3 — Production hardening + deploy (1-2 weeks, Track A)
Make the single-user/self-hosted app genuinely robust.

- **Job durability**: startup reconciliation `UPDATE jobs SET status='failed' WHERE status='running'` + synthetic terminal SSE frame for dead jobs (`jobs.py:102-123`); bounded worker pool (asyncio.Semaphore/queue) so N uploads don't hammer the provider + DB (`jobs.py:52-85`).
- **Kill the O(events²) job writes**: append-only `job_events` table (one row per event) instead of rewriting the whole `events_json` blob every `ctx.emit()` (`jobs.py:46-47`).
- **Route DB calls off the event loop** (`asyncio.to_thread` / single-writer task) — today all of `db.py:99-112` runs sync on the FastAPI loop.
- **Crash-safe migrations**: explicit `BEGIN/COMMIT` per migration, bump `user_version` in the same txn (`db.py:83-88`).
- **Secrets at rest**: encrypt (`cryptography.Fernet` w/ env key, or OS keychain for desktop); separate secret from display settings.
- **Deploy**: Dockerfile + `.env`/`python-dotenv`, env-driven HOST/PORT/data-dir (`main.py:27`, `settings.py:14-18`), drop browser-open in server mode, switch `@app.on_event("startup")` → lifespan (`main.py:150`), hatch build hook that builds+copies `web/dist` or fails the build (`pyproject.toml:36-41`).
- **Observability**: stdlib `logging` (JSON in prod), request-ID middleware, job-lifecycle logging by id; readiness `/api/health` that checks DB writability (`main.py:36-39`); emit chat errors as a typed `error` SSE event, not tokens in the answer stream (`chat.py:62`).
- **CI + tests**: add `.github/workflows` for docloom-studio (backend tests + frontend lint/typecheck/build — currently none exist); FastAPI `TestClient` route tests (status/404/validation/secret-echo/traversal); `respx`-based provider-boundary tests for `providers.py` (currently zero — the riskiest code); jobs SSE/cancel/restart tests; a WAL concurrency test.
- Fix default model tag `qwen3.5:9b` → a real Ollama tag (`settings.py:30`).

**Done looks like:** concurrent jobs run bounded without lock errors or zombie "running" rows; secrets encrypted and never echoed; `docker run` with env config serves the built SPA; green CI on backend + frontend; provider branching covered by mocked-HTTP tests.

---

### Phase 4 — Top-class UI overhaul (2-3 weeks)
Structural polish on top of the Phase-0 font/error fixes.

- **Design tokens**: add a `--text-*` type scale (retire scattered `text-[13px]`/`text-[62%]` arbitrary sizes), accent `hover/subtle/tint` variants (replace inline `/5` `/40` opacities), a 2-3 step elevation scale for popovers/modals/reader drawer, and motion duration/easing tokens. `tokens.css`, `index.css:4-19`.
- **Responsive shell**: breakpoints + collapse/drawer below ~1100px for the 816px of fixed chrome (`App.tsx:13`, `SourcesPanel.tsx:124`, `ArtifactsPanel.tsx:41`, `NotebookWorkspace.tsx:64`); collapsible sources rail; right pane toggles Create ↔ Source Reader.
- **Motion pass** using the already-installed `motion` lib (route transitions, list stagger, toast enter/exit), bounded by the existing `prefers-reduced-motion` guard (`index.css:103`).
- **a11y sweep**: `aria-label` on icon-only buttons (`ChatPanel.tsx:111`, `SourcesPanel.tsx:128,214`); keyboard-reachable citations (falls out of Phase 1d).
- **Rich-block editing** — the single seam is `EditableBlock.tsx:69-73` (table/chart/stats/image render read-only). Add Inspector-driven editors + list entries in `DocEditor.tsx:29-30` / `EditableSlide.tsx:9-15`.
- **Studio tiles** redesign of `ArtifactsPanel.tsx:47-63` (thumbnails, recently-generated, Audio Overview + Notes slots).
- **Fix the diagram canvas-reload bug** (rehydrate `excalidraw_scene.elements` on load before the `canvas && Excalidraw` gate, `DiagramEditor.tsx:30-38,157`); richer Mermaid (classDef/subgraphs/architecture-beta icons); read back AntV `editable:true` infographic canvas mutations (`InfographicEditor.tsx:85-100`); sheet delete row/col + format control. Optional dark chrome using the existing dark stage palette (`tokens.css:17-20`).

**Done looks like:** the app reads as an editorial "studio," works down to tablet width, animates consistently, is keyboard/AT-navigable, and every generated block (tables, charts, images, diagrams, infographics) is fully editable in-app.

---

## 3. Decisions the user must make

**(a) TTS provider for podcast**
- *Local-first (Piper / Kokoro-82M)* — free, private, no API key, on-brand; Piper = smallest/fastest, Kokoro = best quality/size. XTTS-v2 clones voices but its CPML license bars commercial use.
- *Cloud (OpenAI `gpt-4o-mini-tts` / ElevenLabs)* — best expressiveness, breaks local-first/privacy.
- **Recommendation:** ship **Piper as the default** (2 preset host voices), architect `tts.py` behind a `provider.tts` setting so Kokoro and cloud engines are opt-in swaps. Matches the project's no-API-key identity while leaving a premium path.

**(b) UI visual direction**
- *Editorial studio* — actually ship the fonts already declared (Sora/Inter/JetBrains Mono) on the warm-paper palette that's already assumed everywhere.
- *Warm serif document house* — humanist serif display signals "documents."
- *Calm neutral + single vivid accent* — reserve the dark stage palette for artifact previews.
- **Recommendation:** **Editorial studio.** Lowest risk — the palette, tokens, and `font-display` class usage already assume it; it's a matter of shipping `.woff2` files, not a redesign.

**(c) Single-user local-first vs multi-tenant**
- The entire codebase (global unscoped SQL, in-process jobs, localhost bind, plaintext keys to client) assumes one trusted user. Multi-tenancy is a data-layer + query-layer + auth + secrets + externalized-jobs rewrite — a different product.
- **Recommendation:** **Stay local-first single-user as the default; keep multi-tenant as a separately-justified, opt-in "team server" profile** sharing the render/citation core. Track A gets ~80% of the "production-grade" perception for ~20% of the effort. Only start Track B behind a concrete commercial driver.

**(d) SQLite vs Postgres**
- **Recommendation:** **WAL-tuned SQLite** for the local-first product — Postgres is over-engineering there. Keep the `query_one/query_all/execute` surface driver-agnostic so Postgres can slot in **only if** you commit to Track B (many concurrent tenants), where SQLite WAL won't hold up.

---

## 4. Top 10 fixes to start now

1. **PPTX title logo is silently dropped** — add `add_picture(s.image.path)` in `_title_slide`, guarded by `_usable_image`. `docloom/src/docloom/render/pptx.py:510-524`
2. **Fonts never embed despite UI claiming they do** — either wire uploaded fonts into `typst.compile(font_paths=...)` + `@font-face` in `html.py`, or remove the false claim. `assets.py:72`, `docloom/src/docloom/render/typst.py:353`
3. **`GET /api/settings` returns provider `api_key` in cleartext** — mask/omit secret fields. `settings.py:63-69`, `main.py:42-44`
4. **SPA catch-all path traversal (arbitrary file read)** — resolve + confine to `dist` before serving. `main.py:135-142`
5. **SQLite has no WAL → `database is locked` under concurrent ingest+read** — `PRAGMA journal_mode=WAL; busy_timeout=10000; synchronous=NORMAL`. `db.py:76-80`
6. **Silent stale-vector skip drops whole sources from every answer while UI shows `ready`** — surface as source health / re-embed. `embeddings.py:71-72`
7. **Chat streaming has no `catch` → failed request leaves a dead spinner forever** — add error bubble + retry. `web/src/chat/ChatPanel.tsx:51-64`
8. **Running jobs become permanent zombies on restart** — startup `UPDATE jobs SET status='failed' WHERE status='running'` + terminal SSE frame. `jobs.py:102-123`
9. **Non-PDF/DOCX uploads read as UTF-8 mojibake, PPTX/XLSX/CSV unsupported** — add `parse_pptx`(installed)/`parse_xlsx`/structured-CSV `elif` arms + encoding sniff. `ingest.py:127-135`
10. **Every `font-display` class renders as system-ui (no font files ship)** — add `.woff2` + `@font-face`. new `web/public/fonts/`, `web/src/tokens.css:22-24`, `index.html`