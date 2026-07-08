# Build-Ready Implementation Plan: Self-Hosted AI Document Generation Platform

## TL;DR
- Build a **DocumentIR-centric pipeline** orchestrated by **LangGraph**, with **LiteLLM** as the provider layer, where the LLM never emits final files or raw facts — it emits schema-validated structured content that deterministic per-format renderers turn into PPTX/PDF/DOCX/XLSX/diagrams/infographics.
- **Grounding must be a separate enforced verification pass**, not a prompt instruction: every fact carries a citation, and a MiniCheck/NLI + RAGAS-style faithfulness gate blocks or forces abstention on unsupported claims.
- Comparable open-source projects (Presenton, GPT-Researcher, SurfSense/Open Notebook, Docling, AntV Infographic) validate this architecture; borrow their proven patterns — HTML/Tailwind templates + Zod schemas, planner-executor research, hybrid RAG with citations, and spec→deterministic-render for all visuals.

## Key Findings
- **The naive "let the LLM output the file" approach fails at every format.** OOXML/PPTX/XLSX are ZIP archives of interdependent XML; LLM-emitted bytes corrupt. Even LLM-emitted python-pptx code breaks: python-pptx has no canvas-bounds validation, so an AI-computed off-canvas coordinate silently clips. SlideForge reports a static linter of 46 heuristic rules with feedback-retry cut their broken-slide rate "from ~12% to under 1%." The universal answer is an intermediate representation + deterministic renderers + a validating linter.
- **Typst beats HTML-to-PDF for reports.** HTML/CSS output depends on browser version, OS font stack, and installed fonts (a Chromium 122→124 update can change line heights and tables invisibly); CSS lacks native pagination/hyphenation/running headers. Typst is compiled, deterministic, embeds fonts, single-pass (≈50–500ms), Apache-2.0.
- **Naive RAG does not stop hallucination.** Faithfulness is orthogonal to relevance and correctness; grounding must be measured and enforced as a distinct NLI/claim-verification pass with cite-or-abstain generation.
- **LLMs emit broken diagram code and can't reliably draw raw SVG.** Both diagram-as-code and infographics require a validate-and-auto-repair loop, and infographics require a validated declarative spec (AntV DSL / Vega-Lite), never raw SVG.
- **LangGraph is the right orchestration backbone** (traceable nodes, explicit state, conditional edges, graph-native error routing), paired with LlamaIndex for retrieval — the common production combination.

## Details

### 1. Design philosophy — argue against the naive choice at every layer
- **"Let the LLM output a .pptx/.xlsx/.pdf directly."** Fails — Office formats are ZIP archives of verbose, interdependent OOXML; an LLM cannot self-correct against the schema. The python-pptx-code path breaks because python-pptx has no `overflow:hidden` / canvas-bounds validator; an AI-set `left=Inches(10)` clips off the 13.33"×7.5" slide. SlideForge's fix — a pre-execution linter (canvas bounds, text overlap, contrast, font minimums) that feeds violations back to the LLM to retry — took broken slides from ~12% to under 1%. **Decision: LLM emits a validated IR; deterministic renderers produce files; a linter gates every render.**
- **"HTML-to-PDF for reports."** Browser/OS/font-dependent and non-deterministic — unacceptable for auditable documents; CSS has no native pagination. **Decision: Typst as primary PDF/report engine; Playwright HTML-to-PDF only where HTML slide templates must be reused.**
- **"Naive RAG stops hallucination."** No — a system can be relevant but unfaithful. **Decision: cite-or-abstain + separate NLI/claim-verification gate.**
- **"One big agent prompt."** Cannot reliably plan→research→draft→verify→repair with observability. **Decision: LangGraph.**
- **"Render whatever Mermaid/D2 the LLM gives."** GitDiagram's issue tracker documents frequent "Syntax error in text mermaid version 11.4.1." **Decision: validate-and-auto-repair loop with parser-error feedback.**
- **"LLM emits raw SVG for infographics."** Chat2SVG (CVPR 2025) shows LLMs have limited ability to synthesize geometrically complex paths and must be constrained to basic primitives. **Decision: LLM emits a validated declarative spec; deterministic engine renders SVG.**

### 2. System architecture (self-hosted-first)
- **Frontend:** Next.js + shadcn/ui + Framer Motion (Motion) + GSAP; streaming "watch it build" UX over SSE/WebSocket.
- **Backend:** FastAPI (async), REST + SSE streaming endpoint. Auth via **Authentik or Keycloak** (self-hostable OIDC); Supabase Auth if already on Supabase.
- **Async workers:** Celery + Redis with separate queues/pools — `research`, `generate`, `render` (heavy: Typst, Playwright, headless-Chrome diagram render, LibreOffice), `verify`. Render workers are the bottleneck; scale independently.
- **Data stores:** **Postgres + pgvector** (default; ACID, joins, RBAC row-filters, HNSW fine to ~10M vectors) → migrate to **Qdrant** only when filtered-search throughput at scale demands it (best-in-class OSS payload filtering). **MinIO/S3** for assets/brand kits/artifacts. **Redis** for queue + caches.
- **LLM provider layer:** **LiteLLM** gateway (self-hosted Ollama/vLLM vs paid OpenAI/Anthropic/Gemini), routing per task-tier.
- **Observability:** **Langfuse** (self-hostable via Docker Compose/Helm, MIT) tracing every node, prompt versioning, evals, LLM-as-judge; OpenTelemetry/OpenLLMetry for non-LLM spans; native LiteLLM integration.

### 3. Orchestration framework choice: LangGraph (argued)
For a deterministic-yet-agentic pipeline needing plan→research→draft→verify→repair with auditability:
- **LangGraph** models the pipeline as a directed graph of traceable nodes with explicit state, conditional edges, and graph-native error routing — best-in-class error recovery and observability.
- **CrewAI** ships fast but offers less control; its default is to retry with the same approach and can loop — bad for verification-critical work.
- **AutoGen** (multi-agent conversation) adds token/latency overhead and "unnecessary complexity for linear workflows."
- **LlamaIndex Workflows** (event-driven, self-correction via ValidationErrorEvent) is the natural fit for the **retrieval layer**.
- **Recommended combination:** LangGraph orchestration + LlamaIndex retrieval; **Pydantic models + constrained decoding (Outlines or XGrammar via vLLM)** to force schema-valid IR from every generation node; **DSPy** only for offline prompt optimization against the eval set.

### 4. The agentic pipeline (LangGraph node graph)
```
intake → plan → source-routing ─┬─→ user-context extract
                                ├─→ RAG retrieve (hybrid + rerank)
                                └─→ web-research (plan→search→read→synthesize→cite)
        → evidence-store (claims + citations)
        → outline (DocumentIR skeleton)
        → section-draft (grounded, cite-required)  ◄─┐
        → VERIFICATION GATE (NLI/MiniCheck + RAGAS)  ─┘ (fail → revise/abstain)
        → diagram/infographic subgraph (generate→validate→repair)
        → IR assembly + brand-theme resolution
        → per-format render fan-out (parallel Celery)
        → render QA/linter (fail → repair)
        → package + deliver
```
**Source-routing logic (when web vs RAG vs user context):** use user-supplied context first (highest trust); RAG when the topic maps to the ingested corpus; **trigger web research** when a fact is time-sensitive/current, absent from context/RAG, or when the verification gate flags an unsupported claim needing a fresh source. This mirrors **GPT-Researcher's** planner-executor pattern: a planner generates sub-questions; parallel executor agents search + scrape + summarize each with source tracking; a publisher aggregates. Per GPT-Researcher's docs, "the agents leverage both gpt-4o-mini and gpt-4o (128K context)… the average research task takes around 3 minutes to complete, and costs ~$0.005," aggregating over 20 web sources per run — designed explicitly to counter LLMs' outdated-training-data hallucination.

**Web research stack (self-hostable first):** SearXNG (meta-search) → Crawl4AI / Firecrawl (self-hostable) or Playwright extraction → dedupe/summarize. Optional paid: Tavily, Brave, Exa, Serper. Deep-research patterns: GPT-Researcher (planner/executor/publisher) and local-deep-researcher.

### 5. DocumentIR schema design
**Principle (Portable Text, Lexical, ProseMirror, Pandoc):** separate semantic structure from presentation — store *what content means*, not *how it looks*. Portable Text's rationale is exactly this: "HTML and Markdown encode rendering assumptions into the content. Portable Text separates content structure from presentation, making the same content renderable as React components, HTML strings, PDFs, or plain text."

**Structure:** a Pydantic-validated JSON tree of typed nodes (`type` + `attrs` + `children`), modeled on Portable Text's blocks/spans/marks — with `markDefs` for annotations that carry data (links, citations) kept separate from decorators (bold/italic), so multiple spans share annotation data without duplication. Every block gets a stable `_key` for diffing, collaboration, and citation-anchoring. Adopt Lexical's principle of **decoupling structure from formatting order** so the IR is canonical regardless of the order styles were applied (avoids HTML `<b><i>` nesting ambiguity).

**Document-type coverage** — a container with a shared semantic core + type-specific block schemas (a single rich-text IR cannot capture spreadsheet formulas or slide geometry — Pandoc's documented weakness with "complex tables" and layout):
- Shared blocks: heading, paragraph, list, quote, table, image, code, callout, citation-ref.
- `SlideBlock` (layout *intent* — title/bullets/two-column/hero+image — not absolute geometry).
- `SheetBlock` (rows, columns, cell types, formulas, number formats) for XLSX.
- `DiagramBlock` / `InfographicBlock` (validated spec + rendered SVG/PNG ref).
- `ChartBlock` (Vega-Lite/chart JSON spec).

**Every renderer consumes the same IR** via a visitor/serializer per format (Pandoc reader/writer pattern; Portable Text serializer pattern). **Unknown block types degrade gracefully** — renderers skip types they don't recognize (Portable Text guarantee).

**Failure modes to design against (sourced):**
1. **Least-common-denominator loss (Pandoc, explicit):** "Because pandoc's intermediate representation of a document is less expressive than many of the formats it converts between, one should not expect perfect conversions between every format and every other… some document elements, such as complex tables, may not fit into pandoc's simple document model." Mitigation: **custom blocks carrying format-specific payloads** + **per-node overridable handlers/serializers**.
2. **Presentation baked into content** → breaks re-theming. Mitigation: no hard-coded colors/fonts in IR; only semantic roles resolved by the theme layer.
3. **Tabular/slide semantics forced into a text AST** → fidelity loss. Mitigation: dedicated `SheetBlock`/`SlideBlock` schemas.

### 6. Brand-kit / theming token system
**How the leaders do it:** Gamma defines a workspace theme once (colors, fonts, logos, card styles) and applies it automatically across decks/docs/sites via a single theming engine; it can extract brand elements from an existing PPTX/Slides file to auto-create a theme; "Gamma separates content from design," so themes swap instantly without touching content. Canva's Brand Kit stores logos, color themes, and font hierarchies and exposes them as **design tokens** (CSS variables / JS variables / React props) that act as "a single source of truth for design properties."

**Design:** a **brand kit → design-token map → DocumentIR theme resolution** layer. Brand kit (logo variants, banners, palette hex, font files) in MinIO. Tokens are semantic (`color.primary`, `color.onPrimary`, `font.heading`, `font.body`, `logo.mark`, `logo.wordmark`, `spacing.*`). Each renderer implements a **theme adapter** mapping tokens to native mechanisms:
- PPTX: theme part + slide-master placeholders, logo on master, font embedding.
- Typst/PDF: `#set text(font: brand.body)`, header `image(logo)`, brand color `rgb("#…")` (Typst's documented pattern).
- XLSX: named cell styles, header fill = `color.primary`.
- Diagrams/infographics: brand palette into D2 theme variables / AntV theme.
Renderers **honor tokens, never literal values** — this guarantees consistency across all six formats. Font embedding is explicit in every adapter (Typst embeds by default; PPTX must embed to avoid substitution).

### 7. Grounding, citations & the anti-hallucination verification pass
**Grounding-and-citation data model:** every atomic claim links to one or more **evidence records** (`source_id`, `url/doc_id`, `chunk_id`, `verbatim_quote`, `retrieval_score`). Citations are IR annotations (`markDefs` entries of type `citation`) anchored to spans — so they survive into every renderer (footnotes in PDF/DOCX, endnote slide in PPTX, cell comments in XLSX).

**Generation:** cite-or-abstain prompting — the model may only assert facts present in provided evidence and must attach a citation to each; if no evidence supports a needed fact, it must abstain or trigger web research.

**The separate enforced verification pass (the anti-hallucination core):**
1. **Claim decomposition** — split each drafted section into atomic claims (MiniCheck/RAGAS-style).
2. **Claim-vs-source checking** — NLI/entailment of each claim against its cited source span. Options: **Bespoke-MiniCheck** (efficient fact-checking on grounding documents), DeBERTa-MNLI NLI (fast ~50–200ms/claim, cheap, deterministic), or LLM-as-judge (higher accuracy, slower/costlier). Recommend NLI/MiniCheck first, LLM-judge as escalation.
3. **Faithfulness scoring (RAGAS)** — supported-claims / total-claims; strict grounding (RAGAS penalizes unsupported claims even if true in reality).
4. **Gate:** `contradicted`/`unsupported` claims are blocked → revise with missing evidence, re-research, or abstain. Nothing ships un-attributed.

Anthropic's finding that injection/grounding defense must be layered (training + classifiers + runtime checks) and the RAG literature's consensus that hallucination detection "remains challenging" both justify making verification a **distinct, instrumented graph node with its own model**, logged in Langfuse for eval.

### 8. Diagram & infographic subsystem (generate → validate → repair)
**Diagram-as-code:** support **Mermaid, D2, Graphviz**; default to **D2** for architecture (superior TALA auto-layout, cleaner syntax, better theming, SVG icon embedding, browser-free Go renderer), **Mermaid** for flowcharts/sequence/mind-maps (native ecosystem rendering), **Graphviz** for large dependency graphs.

**Generate-and-auto-repair loop (sourced):**
1. **Mermaid-First prompt discipline:** declare diagram type + constraints first, demand code-only output, cap node count, forbid styling drift (negative constraints reduce "creative drift").
2. **Validate** with the real parser in a sandbox (mermaid parser / `mmdc`, D2 CLI, Graphviz); millisecond-level validators exist.
3. On failure, **feed the exact parser error + broken code back** with a repair system prompt ("This failed with error X, output ONLY corrected code"); LLMs self-correct well from specific parser errors. Loop with max retries (3–5); on final failure, fall back to a simpler template or flag.
4. **Layout-quality control:** render to SVG; optionally check node overlap/aspect ratio; re-layout with alternate engine if poor.

**Infographics (NotebookLM-style) — self-hostable, editable, deterministic:**
- **Correction:** NotebookLM's infographic feature is powered by **Nano Banana Pro (Gemini 3 Pro Image)** — a generative *image* model, so output is a static raster: not editable, reproducible, or self-hostable. **Do NOT copy this.**
- **Use AntV `@antv/infographic`** (MIT). Its GitHub confirms "~200 built-in infographic templates" and "High-quality SVG output: Renders with SVG by default," with a built-in editor. The LLM emits a fault-tolerant declarative DSL that renders to SVG and supports **streaming progressive rendering** (`buffer += chunk; infographic.render(buffer)`) — ideal for "watch it build."
- **Charts:** **`@antv/GPT-Vis`** — GitHub confirms "26 Chart Types" and "Charts render as the AI model generates, no need to wait for the full response." And **AntV `mcp-server-chart`**, which supports **private self-hosted deployment**: "You can use AntV's project GPT-Vis-SSR to deploy an HTTP service in a private environment, and then pass the URL address through env `VIS_REQUEST_SERVER`" (26+ charts).
- Also support **Vega-Lite** (declarative JSON grammar; renders SVG/Canvas) — LLM emits JSON validated against the Vega-Lite JSON schema.
- **Principle:** LLM emits a **validated JSON/DSL spec, never raw SVG**; deterministic renderer produces the visual; pair structural schema validation with one semantic/repair retry ("structural correctness does not equal semantic correctness").

### 9. Self-hosted RAG / ingestion stack (NotebookLM-style)
- **Parsing:** **Docling** (IBM, MIT, fully local on commodity hardware) as default — DoclingDocument Pydantic model captures rich structure (DocLayNet layout, TableFormer tables) and avoids OCR when possible; per IBM researcher Peter Staar, "Avoiding OCR reduces errors, and it also speeds up the time-to-solution by 30 times." Alternatives: Unstructured (self-hosted), LlamaParse (paid). SurfSense validates this exact menu (LlamaCloud / Unstructured / Docling fully local).
- **Chunking:** semantic chunking over fixed-size; metadata-enriched chunks (section titles + parent context); **Anthropic-style contextual retrieval** — per Anthropic's "Contextual Retrieval in AI Systems" (Sept 2024): "Contextual Embeddings reduced the top-20-chunk retrieval failure rate by 35% (5.7% → 3.7%)"; combined with Contextual BM25 "by 49% (5.7% → 2.9%)"; and "Reranked Contextual Embedding and Contextual BM25 reduced the top-20-chunk retrieval failure rate by 67% (5.7% → 1.9%)."
- **Retrieval:** **hybrid (BM25 + dense) fused with Reciprocal Rank Fusion**, then **rerank** with a cross-encoder — self-hostable **bge-reranker-v2-m3** (Cohere Rerank paid alternative). Rule of thumb: retrieve ~20, rerank to top 5, send 3–5 (mitigates "lost in the middle"); hybrid+rerank materially beats semantic-only (one benchmark: 66.4% vs 56.7% MRR).
- **Vector store:** pgvector default; Qdrant for high-throughput filtered/multi-tenant search at larger scale.
- **Grounded answering with citations:** SurfSense uses hybrid (vector + BM25) + LangChain Deep Agents (plan, sub-agents, synthesize) to return cited answers; Open Notebook and Khoj are the other self-hostable references. Borrow: per-claim citation, multi-source synthesis, on-infra data.

### 10. Production concerns
- **Multi-tenancy:** tenant_id + Postgres RLS; per-tenant MinIO prefixes; per-tenant brand kits and API-key scoping; Qdrant payload isolation if used.
- **Auth:** Authentik or Keycloak (self-hosted OIDC); short-lived JWTs at FastAPI; RBAC roles (owner/admin/editor/viewer, as SurfSense implements).
- **Observability:** Langfuse (traces/evals/prompt-versioning) + OpenLLMetry/OpenTelemetry; pair with OpenObserve or existing infra stack for metrics/logs.
- **Security / prompt-injection defense (critical for scraping/agent layer):** treat all fetched web/user content as **untrusted data, never instructions**. Layered defenses: (1) wrap external content in explicit data delimiters and instruct the model it is data not commands (SANDWICH/spotlighting); (2) sanitize inputs — strip control chars, bidi overrides (U+202A–202E), zero-width chars hiding injected instructions; (3) classifier scanning of untrusted content; (4) least-privilege tools + output/tool-call validation; (5) never let scraped content trigger tool calls unchecked. Reality check: prompt injection is "an unsolved problem" — use multi-layered mitigation + continuous red-teaming, not a single prompt fix.
- **Caching & cost control:** LiteLLM task-tier routing (cheap for plan/extract, strong for synth/verify); prompt caching; render cache keyed on IR+theme hash; embedding cache; per-tenant token budgets and rate limits; Langfuse cost dashboards/alerts.
- **Evals / CI:** golden datasets in Langfuse; RAGAS faithfulness + retrieval metrics + diagram-validity/render-success in CI; LLM-as-judge on a sample; block deploys on regression.
- **Scaling:** independent Celery pools per queue; render workers (Typst/Playwright/LibreOffice/D2) scale horizontally on queue depth.

### 11. UI/UX & animation specification
- **Reference quality:** Gamma (generative speed + on-brand output), Linear/Vercel/Tome (premium smoothness). Motion (Framer Motion) is used by Framer/Figma across hundreds of thousands of sites and partners with Cursor for one-click examples.
- **"Watch it build" streaming UX:** stream LangGraph node progression over SSE; live timeline (plan → research → draft → verify → render) with per-node status; **AntV infographic/GPT-Vis streaming** progressively renders SVG as the spec streams (fault-tolerant partial render); **skeleton/shimmer** for pending sections; **AnimatePresence** for block enter/exit; **layout animations** (`layout`/`layoutId`) for reflow as content lands; staggered list/card reveals.
- **Framer Motion vs GSAP division of labor (sourced):** Motion for routine React UI transitions, gestures, layout, AnimatePresence; **GSAP for complex timelines, SVG morphs, and scroll-driven sequences** (frame-accurate timeline control Motion lacks). Animate only `transform`/`opacity` for 60fps; run GSAP in `useEffect` with cleanup to avoid SSR hydration mismatch and killed timelines.
- **Micro-interactions:** magnetic/hover states, animated number counters for stats, scrambling text on headings, confetti on completion.
- **Citation UX:** per-claim footnote badges with hover tooltips linking to the source.

## Recommendations
**Staged build order — all six document types in the first production build (no trimmed MVP):**
- **Phase 0 — Foundations:** FastAPI + Celery/Redis + Postgres/pgvector + MinIO; LiteLLM gateway; Authentik/Keycloak; Langfuse. Define **DocumentIR (Pydantic) + theme-token schema**. *Benchmark to advance:* IR round-trips through a serializer without loss on a fixture set.
- **Phase 1 — IR + all six renderers:** PPTX (python-pptx/PptxGenJS + linter), XLSX (XlsxWriter/openpyxl), report/PDF (Typst; python-docx for DOCX), diagrams (D2/Mermaid/Graphviz + repair loop), infographics/charts (AntV Infographic + GPT-Vis/Vega-Lite), with brand-token adapters per renderer. *Exit criteria:* all six formats render on-brand from a hand-authored IR; broken-render rate <1% (SlideForge benchmark).
- **Phase 2 — Generation + grounding:** LangGraph graph, cite-or-abstain drafting, evidence store, verification gate (MiniCheck/NLI + RAGAS); Docling ingestion + hybrid+rerank retrieval. *Benchmark:* RAGAS faithfulness ≥ target on golden set before shipping generated docs.
- **Phase 3 — Autonomous web research:** SearXNG + Crawl4AI/Playwright, planner-executor subgraph, source-routing, injection defenses.
- **Phase 4 — Streaming UX + polish:** SSE "watch it build" UI, Framer Motion/GSAP animations, streaming infographic render, citation UI.
- **Phase 5 — Production hardening:** multi-tenancy/RLS, eval CI, cost controls, autoscaling, red-teaming.

**Thresholds that change the plan:** if filtered-search latency or multi-tenant isolation becomes the bottleneck at scale, migrate pgvector→Qdrant. If NLI verification precision is insufficient on your domain, escalate to LLM-as-judge in the gate. If diagram repair-loop success stalls below ~95%, tighten node-count caps and add a JSON-spec intermediate (structured-JSON→DSL) rather than free-form diagram code.

**Optional paid alternatives (noted, not default):** LLMs — OpenAI/Anthropic/Gemini via LiteLLM. Search — Tavily/Brave/Exa/Serper. Parsing — LlamaParse. Reranker — Cohere Rerank. Vector — Pinecone. Image infographics — Nano Banana Pro (non-editable). Observability cloud — Langfuse Cloud/LangSmith.

## Caveats
- **NotebookLM's internal infographic pipeline is not publicly documented;** the confirmed fact is that it uses Nano Banana Pro (an image model), so it is generative-raster, not spec-render — the platform should deliberately diverge toward the editable/self-hostable AntV spec→SVG approach.
- **Portable Text is formally a "v0.0.1 Working Draft"** though production-stable since 2018; treat its schema as a design reference, not a frozen standard.
- **AntV GPT-Vis v1.0 "stable" is a future-dated release; current is pre-1.0** — pin versions. All three AntV projects are MIT and genuinely self-hostable, but `mcp-server-chart` geographic maps require AMap (China-only, unavailable in private deployment) — avoid map chart types in self-hosted mode.
- **Prompt injection has no complete solution** (multiple 2025–2026 papers); the plan mitigates but cannot eliminate risk in the web-scraping/agent layer — budget for ongoing red-teaming.
- **Some framework-comparison and vector-DB "benchmark" figures come from vendor or SEO blogs** (e.g., MRR and QPS numbers); treat exact figures as directional and re-benchmark on your own data before committing to migrations.
- **Typst's ecosystem is younger than LaTeX** and can produce larger PDFs when embedding full CJK font sets; validate on your document mix and enable Typst's built-in PDF compression / font subsetting.
- **The DocumentIR least-common-denominator problem is inherent** (Pandoc): perfect cross-format fidelity is impossible; the custom-block + per-node-serializer escape hatches manage but do not eliminate it.