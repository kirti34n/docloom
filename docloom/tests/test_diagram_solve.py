"""Tests for docloom.render.diagram_svg (docs/diagram-plan.md section 3, P0):
the layout/paint seam -- solve() / paint_svg() / render_svg() /
layout_report() / check() / estimate_depth() -- plus regression tests for
the four blocking defects fixed in this module:
  1. fan-in edge-label attribution
  2. aspect ratio for slides (target_aspect + auto-flip)
  3. group titles struck through by edges (z-order)
  4. off-brand palette (rotation clamped to the theme's own hues)

...and for two more findings fixed later, in docs/diagram-status.md:
  9. "client" and "security" kind colors were perceptually identical
  16. legend dead space (reserved unconditionally, drawn only by paint_svg)
      and a silently-dropped duplicate-id node when lint is bypassed

The 5 bake-off specs are embedded verbatim below, in the painter's own
spec-dict vocabulary (key/kind/sublabel/tag), the same content as
tests/test_ir_diagram.py's SPEC1..SPEC5. Each test file owns its own
fixtures rather than importing a sibling test module, and this way neither
file depends on the ephemeral scratchpad directory surviving.
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import replace

import pytest

from docloom.ir import Diagram, DiagramEdge, DiagramGroup, DiagramNode, diagram_hash
from docloom.render import diagram_svg as P

# ---------------------------------------------------------------------------
# the 5 bake-off specs (painter spec-dict vocabulary)
# ---------------------------------------------------------------------------

SPEC1 = json.loads("""
{
  "title": "docloom: validated IR to native documents",
  "direction": "LR",
  "groups": [
    { "key": "renderers", "label": "Renderer layer (one IR in, native files out)", "kind": "region" }
  ],
  "nodes": [
    { "key": "cli", "label": "docloom CLI", "sublabel": "render / lint / schema / guide / theme", "tag": "argparse", "kind": "client", "group": null },
    { "key": "llm", "label": "LLM Bridge", "sublabel": "llm.py: llm_schema, parse_llm_output, AUTHORING_GUIDE", "tag": "structured output", "kind": "service", "group": null },
    { "key": "provider", "label": "LLM Provider", "sublabel": "OpenAI / Anthropic / Gemini / Ollama", "tag": "external", "kind": "external", "group": null },
    { "key": "ir", "label": "Document IR", "sublabel": "ir.py: pydantic v2 tagged union", "tag": "blocks / slides / sheets", "kind": "service", "group": null },
    { "key": "lint", "label": "Linter", "sublabel": "lint.py: overflow, content, chart, citation rules", "tag": "errors block render", "kind": "service", "group": null },
    { "key": "theme", "label": "Theme Tokens", "sublabel": "theme.py: primary, accent, surface, text, muted", "tag": "semantic only", "kind": "store", "group": null },
    { "key": "dispatch", "label": "Render Dispatch", "sublabel": "render/__init__.py FORMATS table", "tag": "7 formats", "kind": "service", "group": null },
    { "key": "chart", "label": "chart_svg", "sublabel": "shared themed SVG chart builder", "tag": "bar / line / pie / scatter", "kind": "service", "group": null },
    { "key": "pptx", "label": "PPTX Renderer", "sublabel": "python-pptx, 16:9 decks", "tag": null, "kind": "service", "group": "renderers" },
    { "key": "docx", "label": "DOCX Renderer", "sublabel": "python-docx, styled reports", "tag": null, "kind": "service", "group": "renderers" },
    { "key": "xlsx", "label": "XLSX Renderer", "sublabel": "openpyxl workbooks", "tag": null, "kind": "service", "group": "renderers" },
    { "key": "typst", "label": "Typst Renderer", "sublabel": "emits .typ, compiles to PDF", "tag": null, "kind": "service", "group": "renderers" },
    { "key": "html", "label": "HTML Renderer", "sublabel": "self-contained, fonts inlined", "tag": null, "kind": "service", "group": "renderers" },
    { "key": "markdown", "label": "Markdown Renderer", "sublabel": "portable .md", "tag": null, "kind": "service", "group": "renderers" }
  ],
  "edges": [
    { "source": "llm", "target": "provider", "label": "JSON schema + authoring guide", "style": "secure" },
    { "source": "provider", "target": "llm", "label": "document JSON", "style": "dashed" },
    { "source": "llm", "target": "ir", "label": "parse, coerce tags, validate", "style": "emphasis" },
    { "source": "cli", "target": "ir", "label": "Document.load()", "style": "emphasis" },
    { "source": "cli", "target": "theme", "label": "--theme", "style": "solid" },
    { "source": "cli", "target": "lint", "label": "lint before render", "style": "emphasis" },
    { "source": "ir", "target": "lint", "label": "validated IR", "style": "solid" },
    { "source": "theme", "target": "lint", "label": "contrast + overflow budget", "style": "solid" },
    { "source": "lint", "target": "dispatch", "label": "clean, or --no-lint", "style": "emphasis" },
    { "source": "ir", "target": "dispatch", "label": "Document", "style": "solid" },
    { "source": "theme", "target": "dispatch", "label": "tokens", "style": "solid" },
    { "source": "theme", "target": "chart", "label": "series colors", "style": "solid" },
    { "source": "dispatch", "target": "pptx", "label": ".pptx", "style": "solid" },
    { "source": "dispatch", "target": "docx", "label": ".docx", "style": "solid" },
    { "source": "dispatch", "target": "xlsx", "label": ".xlsx", "style": "solid" },
    { "source": "dispatch", "target": "typst", "label": ".typ / .pdf", "style": "solid" },
    { "source": "dispatch", "target": "html", "label": ".html", "style": "solid" },
    { "source": "dispatch", "target": "markdown", "label": ".md", "style": "solid" },
    { "source": "chart", "target": "pptx", "label": "chart SVG", "style": "dashed" },
    { "source": "chart", "target": "typst", "label": "chart SVG", "style": "dashed" },
    { "source": "chart", "target": "html", "label": "inline SVG", "style": "dashed" }
  ]
}
""")

SPEC2 = json.loads("""
{
  "title": "docloom studio: local-first AI document workspace",
  "direction": "LR",
  "groups": [
    { "key": "trust", "label": "Tenant isolation boundary", "kind": "security-group" }
  ],
  "nodes": [
    { "key": "spa", "label": "Web SPA", "sublabel": "vanilla JS, the loom design system", "tag": "served from /dist", "kind": "client", "group": null },
    { "key": "api", "label": "FastAPI App", "sublabel": "main.py, routers: notebooks, sources, assets, artifacts", "tag": "ASGI", "kind": "service", "group": null },
    { "key": "auth", "label": "Auth", "sublabel": "auth.py: email + password, opaque session tokens", "tag": "SHA-256 at rest", "kind": "security", "group": "trust" },
    { "key": "crypto", "label": "Key Vault", "sublabel": "crypto.py: provider API keys encrypted at rest", "tag": null, "kind": "security", "group": "trust" },
    { "key": "notebooks", "label": "Notebooks", "sublabel": "notebooks.py: workspaces, chat, settings", "tag": null, "kind": "service", "group": null },
    { "key": "ingest", "label": "Ingest + Chunker", "sublabel": "ingest.py: PDF, URL, text, transcripts", "tag": null, "kind": "service", "group": null },
    { "key": "embed", "label": "Retrieval", "sublabel": "embeddings.py: dense vectors + BM25, fused by RRF", "tag": "numpy .npy", "kind": "service", "group": null },
    { "key": "jobs", "label": "Job Runner", "sublabel": "jobs.py: async workers, bounded by semaphore", "tag": "SSE events", "kind": "queue", "group": null },
    { "key": "generate", "label": "Generate", "sublabel": "generate.py: deck and report authoring", "tag": null, "kind": "service", "group": null },
    { "key": "providers", "label": "Provider Layer", "sublabel": "providers.py: ollama, llama-server, lmstudio, openai, anthropic, gemini", "tag": "6 kinds", "kind": "service", "group": null },
    { "key": "llmext", "label": "Model Endpoints", "sublabel": "local Ollama or hosted LLM + image APIs", "tag": null, "kind": "external", "group": null },
    { "key": "db", "label": "SQLite", "sublabel": "db.py: studio.db, Postgres optional", "tag": "row-level tenant scoping", "kind": "store", "group": "trust" },
    { "key": "files", "label": "Data Dir", "sublabel": "source text, chunk vectors, assets, artifacts", "tag": null, "kind": "store", "group": "trust" },
    { "key": "docloom", "label": "docloom", "sublabel": "IR to PPTX / DOCX / XLSX / PDF / HTML / MD", "tag": "library", "kind": "service", "group": null }
  ],
  "edges": [
    { "source": "spa", "target": "api", "label": "REST + session cookie", "style": "emphasis" },
    { "source": "api", "target": "auth", "label": "require session", "style": "secure" },
    { "source": "auth", "target": "db", "label": "users, sessions, workspaces", "style": "solid" },
    { "source": "api", "target": "notebooks", "label": "CRUD", "style": "solid" },
    { "source": "notebooks", "target": "db", "label": "scoped by workspace", "style": "secure" },
    { "source": "api", "target": "ingest", "label": "upload or fetch URL", "style": "solid" },
    { "source": "ingest", "target": "files", "label": "extracted text + chunks", "style": "solid" },
    { "source": "ingest", "target": "embed", "label": "chunks to embed", "style": "solid" },
    { "source": "embed", "target": "providers", "label": "embedding model", "style": "solid" },
    { "source": "embed", "target": "files", "label": "embeddings.npy", "style": "solid" },
    { "source": "api", "target": "jobs", "label": "enqueue build", "style": "emphasis" },
    { "source": "jobs", "target": "spa", "label": "SSE progress, heartbeats", "style": "dashed" },
    { "source": "jobs", "target": "db", "label": "job_events (resume after reload)", "style": "solid" },
    { "source": "jobs", "target": "generate", "label": "run", "style": "emphasis" },
    { "source": "generate", "target": "embed", "label": "retrieve grounding context", "style": "solid" },
    { "source": "generate", "target": "providers", "label": "structured output", "style": "emphasis" },
    { "source": "providers", "target": "crypto", "label": "decrypt API key", "style": "secure" },
    { "source": "crypto", "target": "db", "label": "ciphertext only", "style": "secure" },
    { "source": "providers", "target": "llmext", "label": "HTTPS or localhost", "style": "secure" },
    { "source": "generate", "target": "docloom", "label": "Document IR", "style": "emphasis" },
    { "source": "docloom", "target": "files", "label": "rendered artifacts", "style": "solid" },
    { "source": "api", "target": "files", "label": "download artifact", "style": "solid" }
  ]
}
""")

SPEC3 = json.loads("""
{
  "title": "Payments platform: authorization, ledger, settlement",
  "direction": "LR",
  "groups": [
    { "key": "pci", "label": "PCI DSS cardholder data environment", "kind": "security-group" },
    { "key": "us-east", "label": "us-east-1 (primary)", "kind": "region" },
    { "key": "eu-west", "label": "eu-west-1 (warm standby)", "kind": "region" }
  ],
  "nodes": [
    { "key": "merchant", "label": "Merchant Backend", "sublabel": "server-to-server checkout", "tag": null, "kind": "client", "group": null },
    { "key": "mobile", "label": "Mobile SDK", "sublabel": "iOS + Android, cert pinned", "tag": null, "kind": "client", "group": null },
    { "key": "waf", "label": "Edge + WAF", "sublabel": "TLS termination, bot rules", "tag": "global anycast", "kind": "security", "group": null },
    { "key": "gateway", "label": "API Gateway", "sublabel": "idempotency keys, rate limits", "tag": null, "kind": "service", "group": "us-east" },
    { "key": "authsvc", "label": "Auth Service", "sublabel": "OAuth2 client credentials, scoped JWT", "tag": null, "kind": "security", "group": "us-east" },
    { "key": "payments", "label": "Payment Orchestrator", "sublabel": "state machine: auth, capture, refund", "tag": null, "kind": "service", "group": "us-east" },
    { "key": "vault", "label": "Card Vault", "sublabel": "PAN tokenization, HSM-backed", "tag": "in PCI scope", "kind": "security", "group": "pci" },
    { "key": "fraud", "label": "Fraud Service", "sublabel": "real-time risk score, rules + model", "tag": "p99 40ms", "kind": "service", "group": "us-east" },
    { "key": "ledger", "label": "Ledger Service", "sublabel": "double-entry, append-only", "tag": null, "kind": "service", "group": "us-east" },
    { "key": "settle", "label": "Settlement Worker", "sublabel": "nightly capture batches", "tag": null, "kind": "service", "group": "us-east" },
    { "key": "kafka", "label": "Kafka", "sublabel": "payment.* event log", "tag": "exactly-once", "kind": "queue", "group": "us-east" },
    { "key": "pg", "label": "Postgres", "sublabel": "accounts + ledger entries, multi-AZ", "tag": null, "kind": "store", "group": "us-east" },
    { "key": "pgdr", "label": "Postgres Replica", "sublabel": "streaming replication, RPO under 1 min", "tag": null, "kind": "store", "group": "eu-west" },
    { "key": "cardnet", "label": "Card Network", "sublabel": "Visa / Mastercard rails", "tag": "external", "kind": "external", "group": null }
  ],
  "edges": [
    { "source": "merchant", "target": "waf", "label": "HTTPS", "style": "secure" },
    { "source": "mobile", "target": "waf", "label": "TLS 1.3", "style": "secure" },
    { "source": "waf", "target": "gateway", "label": "filtered traffic", "style": "emphasis" },
    { "source": "gateway", "target": "authsvc", "label": "verify token + scopes", "style": "secure" },
    { "source": "authsvc", "target": "pg", "label": "clients, keys", "style": "solid" },
    { "source": "gateway", "target": "payments", "label": "POST /charges", "style": "emphasis" },
    { "source": "gateway", "target": "fraud", "label": "device fingerprint", "style": "dashed" },
    { "source": "payments", "target": "fraud", "label": "risk check (blocking)", "style": "solid" },
    { "source": "payments", "target": "vault", "label": "PAN to token", "style": "secure" },
    { "source": "vault", "target": "cardnet", "label": "authorization request", "style": "secure" },
    { "source": "cardnet", "target": "vault", "label": "auth code / decline", "style": "dashed" },
    { "source": "vault", "target": "payments", "label": "token + auth result", "style": "secure" },
    { "source": "payments", "target": "ledger", "label": "reserve funds", "style": "emphasis" },
    { "source": "ledger", "target": "pg", "label": "double-entry write", "style": "emphasis" },
    { "source": "payments", "target": "kafka", "label": "payment.authorized", "style": "solid" },
    { "source": "ledger", "target": "kafka", "label": "ledger.posted", "style": "dashed" },
    { "source": "kafka", "target": "settle", "label": "consume authorized", "style": "solid" },
    { "source": "kafka", "target": "fraud", "label": "replay for model training", "style": "dashed" },
    { "source": "settle", "target": "ledger", "label": "post settlement", "style": "solid" },
    { "source": "settle", "target": "cardnet", "label": "capture batch (SFTP)", "style": "secure" },
    { "source": "pg", "target": "pgdr", "label": "streaming replication", "style": "dashed" }
  ]
}
""")

SPEC4 = json.loads("""
{
  "title": "RAG platform: ingestion, hybrid retrieval, grounded generation",
  "direction": "LR",
  "groups": [],
  "nodes": [
    { "key": "client", "label": "Client App", "sublabel": "chat UI + upload", "tag": null, "kind": "client", "group": null },
    { "key": "api", "label": "RAG API", "sublabel": "query orchestration, citation assembly", "tag": null, "kind": "service", "group": null },
    { "key": "ingestq", "label": "Ingest Queue", "sublabel": "durable, retried with backoff", "tag": null, "kind": "queue", "group": null },
    { "key": "workers", "label": "Ingest Workers", "sublabel": "parse PDF, HTML, DOCX, transcripts", "tag": "autoscaled", "kind": "service", "group": null },
    { "key": "chunker", "label": "Chunker", "sublabel": "semantic split with overlap, keeps headings", "tag": null, "kind": "service", "group": null },
    { "key": "embedder", "label": "Embedding Model", "sublabel": "batched, dimension pinned per corpus", "tag": null, "kind": "service", "group": null },
    { "key": "vector", "label": "Vector Store", "sublabel": "HNSW index, metadata filters", "tag": null, "kind": "store", "group": null },
    { "key": "docstore", "label": "Document Store", "sublabel": "raw text + chunk provenance", "tag": null, "kind": "store", "group": null },
    { "key": "retriever", "label": "Retriever", "sublabel": "dense + BM25, fused by RRF", "tag": "hybrid", "kind": "service", "group": null },
    { "key": "reranker", "label": "Reranker", "sublabel": "cross-encoder, top-k to top-n", "tag": null, "kind": "service", "group": null },
    { "key": "cache", "label": "Semantic Cache", "sublabel": "embedding-keyed, TTL + invalidation", "tag": null, "kind": "store", "group": null },
    { "key": "llm", "label": "LLM Provider", "sublabel": "hosted completions API", "tag": "external", "kind": "external", "group": null },
    { "key": "eval", "label": "Eval Harness", "sublabel": "faithfulness, recall@k, answer relevance", "tag": "offline + canary", "kind": "service", "group": null }
  ],
  "edges": [
    { "source": "client", "target": "ingestq", "label": "upload document", "style": "solid" },
    { "source": "ingestq", "target": "workers", "label": "dequeue", "style": "solid" },
    { "source": "workers", "target": "docstore", "label": "raw document", "style": "solid" },
    { "source": "workers", "target": "chunker", "label": "extracted text", "style": "solid" },
    { "source": "chunker", "target": "docstore", "label": "chunks + provenance", "style": "solid" },
    { "source": "chunker", "target": "embedder", "label": "chunk batches", "style": "solid" },
    { "source": "embedder", "target": "vector", "label": "upsert vectors", "style": "emphasis" },
    { "source": "client", "target": "api", "label": "question", "style": "emphasis" },
    { "source": "api", "target": "cache", "label": "lookup by embedding", "style": "solid" },
    { "source": "cache", "target": "api", "label": "hit: cached answer", "style": "dashed" },
    { "source": "api", "target": "retriever", "label": "miss: retrieve", "style": "emphasis" },
    { "source": "retriever", "target": "embedder", "label": "embed the query", "style": "solid" },
    { "source": "retriever", "target": "vector", "label": "ANN search", "style": "solid" },
    { "source": "retriever", "target": "docstore", "label": "hydrate chunk text", "style": "solid" },
    { "source": "retriever", "target": "reranker", "label": "top-k candidates", "style": "solid" },
    { "source": "reranker", "target": "api", "label": "top-n ranked context", "style": "solid" },
    { "source": "api", "target": "llm", "label": "prompt + grounded context", "style": "secure" },
    { "source": "llm", "target": "api", "label": "completion", "style": "dashed" },
    { "source": "api", "target": "cache", "label": "write-through", "style": "dashed" },
    { "source": "api", "target": "client", "label": "answer + citations", "style": "emphasis" },
    { "source": "eval", "target": "api", "label": "golden query set", "style": "dashed" },
    { "source": "eval", "target": "retriever", "label": "recall@k probe", "style": "dashed" },
    { "source": "eval", "target": "llm", "label": "LLM-as-judge", "style": "dashed" }
  ]
}
""")

SPEC5 = json.loads("""
{
  "title": "Analytics pipeline: CDC to warehouse with PII isolation",
  "direction": "TB",
  "groups": [
    { "key": "pii", "label": "PII isolation zone (restricted access, KMS-encrypted)", "kind": "security-group" },
    { "key": "lake", "label": "Analytics account", "kind": "region" }
  ],
  "nodes": [
    { "key": "appdb", "label": "App Postgres", "sublabel": "OLTP system of record", "tag": "source", "kind": "store", "group": null },
    { "key": "saas", "label": "SaaS APIs", "sublabel": "CRM, billing, support", "tag": "external", "kind": "external", "group": null },
    { "key": "events", "label": "Event Tracker", "sublabel": "web + mobile clickstream", "tag": null, "kind": "client", "group": null },
    { "key": "cdc", "label": "CDC Connector", "sublabel": "log-based, schema registry", "tag": null, "kind": "service", "group": null },
    { "key": "bus", "label": "Streaming Bus", "sublabel": "partitioned topics, 7 day retention", "tag": null, "kind": "queue", "group": null },
    { "key": "tokenizer", "label": "PII Tokenizer", "sublabel": "detect, hash, and swap for surrogate keys", "tag": null, "kind": "security", "group": "pii" },
    { "key": "vault", "label": "PII Vault", "sublabel": "surrogate key to raw value, audited reads", "tag": "restricted", "kind": "store", "group": "pii" },
    { "key": "raw", "label": "Raw Zone", "sublabel": "object storage, immutable landing", "tag": "bronze", "kind": "store", "group": "lake" },
    { "key": "transform", "label": "Transform Jobs", "sublabel": "staging to marts, incremental models", "tag": "silver + gold", "kind": "service", "group": "lake" },
    { "key": "dq", "label": "Data Quality", "sublabel": "contract tests, freshness, null budgets", "tag": null, "kind": "service", "group": "lake" },
    { "key": "warehouse", "label": "Warehouse", "sublabel": "columnar, tokenized columns only", "tag": null, "kind": "store", "group": "lake" },
    { "key": "bi", "label": "BI Layer", "sublabel": "semantic model + dashboards", "tag": null, "kind": "service", "group": "lake" },
    { "key": "reverse", "label": "Reverse ETL", "sublabel": "audience segments back to tools", "tag": null, "kind": "service", "group": null },
    { "key": "analyst", "label": "Analyst", "sublabel": "dashboards + ad-hoc SQL", "tag": null, "kind": "client", "group": null }
  ],
  "edges": [
    { "source": "appdb", "target": "cdc", "label": "row-level change capture", "style": "emphasis" },
    { "source": "cdc", "target": "bus", "label": "change events", "style": "emphasis" },
    { "source": "saas", "target": "bus", "label": "scheduled API pull", "style": "solid" },
    { "source": "events", "target": "bus", "label": "event stream", "style": "solid" },
    { "source": "bus", "target": "tokenizer", "label": "records carrying PII", "style": "secure" },
    { "source": "tokenizer", "target": "vault", "label": "raw values (never leave the zone)", "style": "secure" },
    { "source": "tokenizer", "target": "raw", "label": "tokenized records", "style": "secure" },
    { "source": "bus", "target": "raw", "label": "non-PII topics land as-is", "style": "solid" },
    { "source": "raw", "target": "transform", "label": "bronze to silver", "style": "emphasis" },
    { "source": "transform", "target": "dq", "label": "assert contracts", "style": "solid" },
    { "source": "dq", "target": "transform", "label": "fail the run on breach", "style": "dashed" },
    { "source": "transform", "target": "warehouse", "label": "gold marts", "style": "emphasis" },
    { "source": "dq", "target": "warehouse", "label": "quality metrics table", "style": "dashed" },
    { "source": "warehouse", "target": "bi", "label": "semantic model", "style": "emphasis" },
    { "source": "bi", "target": "analyst", "label": "dashboards", "style": "solid" },
    { "source": "analyst", "target": "warehouse", "label": "ad-hoc SQL", "style": "dashed" },
    { "source": "warehouse", "target": "reverse", "label": "audience segments", "style": "solid" },
    { "source": "reverse", "target": "saas", "label": "sync back", "style": "secure" },
    { "source": "vault", "target": "warehouse", "label": "re-identify (break-glass, audited)", "style": "dashed" }
  ]
}
""")

BAKEOFF_SPECS = [SPEC1, SPEC2, SPEC3, SPEC4, SPEC5]
DETAILS = ["full", "label+sub", "label"]


def _spec_to_diagram(spec: dict) -> Diagram:
    """Painter spec dict (key/kind/sublabel/tag vocabulary) -> IR Diagram."""
    nodes = [
        DiagramNode(
            id=n["key"], label=n["label"], type=n["kind"],
            sublabel=n.get("sublabel"), tag=n.get("tag"), group=n.get("group"),
        )
        for n in spec["nodes"]
    ]
    edges = [
        DiagramEdge(
            source=e["source"], target=e["target"],
            label=e.get("label"), style=e.get("style", "solid"),
        )
        for e in spec["edges"]
    ]
    groups = [
        DiagramGroup(id=g["key"], label=g["label"], kind=g["kind"])
        for g in spec.get("groups", [])
    ]
    return Diagram(
        title=spec.get("title"), direction=spec.get("direction", "LR"),
        nodes=nodes, edges=edges, groups=groups,
    )


def _fan_in_diagram(n: int = 4) -> Diagram:
    """n edges converging on a single target: the defect-1 acceptance
    fixture (docs/diagram-plan.md section 3)."""
    return Diagram(
        title="fan-in",
        nodes=[DiagramNode(id=f"s{i}", label=f"Source {i}", type="service")
               for i in range(n)]
        + [DiagramNode(id="t", label="Target", type="store")],
        edges=[DiagramEdge(source=f"s{i}", target="t",
                            label=f"edge label {i}", style="solid")
               for i in range(n)],
    )


# ---------------------------------------------------------------------------
# solve(): determinism, non-mutation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", BAKEOFF_SPECS, ids=[s["title"] for s in BAKEOFF_SPECS])
def test_solve_is_deterministic(spec):
    d = _spec_to_diagram(spec)
    r1 = P.layout_report(P.solve(d))
    r2 = P.layout_report(P.solve(d))
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_solve_does_not_mutate_input_diagram():
    d = _spec_to_diagram(SPEC3)
    before = d.model_dump_json()
    P.solve(d)
    P.solve(d, target_aspect=1.6, detail="label")
    assert d.model_dump_json() == before


def test_paint_svg_is_deterministic_byte_for_byte():
    d = _spec_to_diagram(SPEC2)
    svg1 = P.paint_svg(P.solve(d))
    svg2 = P.paint_svg(P.solve(d))
    assert svg1 == svg2


def test_theme_has_no_effect_on_solved_geometry():
    # solve()'s docstring: theme is accepted for API symmetry only; the
    # current text-metric table is a single fixed font, so geometry must be
    # identical regardless of theme.
    d = _spec_to_diagram(SPEC1)
    r_default = P.layout_report(P.solve(d))
    r_custom = P.layout_report(
        P.solve(d, theme={"primary": "#FF0000", "accent": "#00FF00"})
    )
    assert json.dumps(r_default, sort_keys=True) == json.dumps(r_custom, sort_keys=True)


def test_solve_rejects_unknown_detail():
    d = _spec_to_diagram(SPEC1)
    with pytest.raises(ValueError):
        P.solve(d, detail="bogus")


# ---------------------------------------------------------------------------
# check(): clean across all 5 bake-off specs at all 3 detail levels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", BAKEOFF_SPECS, ids=[s["title"] for s in BAKEOFF_SPECS])
@pytest.mark.parametrize("detail", DETAILS)
def test_check_is_clean_for_bakeoff_specs(spec, detail):
    d = _spec_to_diagram(spec)
    solved = P.solve(d, target_aspect=2.2, detail=detail)
    problems = P.check(solved)
    assert problems == [], problems


def test_check_detects_a_real_node_overlap():
    # check() must not be vacuously true: feed it two overlapping node rects
    # directly (bypassing the layout algorithm, which would never produce
    # this) and confirm it is flagged.
    d = _spec_to_diagram(SPEC1)
    solved = P.solve(d)
    a, b = solved.nodes[0], solved.nodes[1]
    solved.nodes[1] = replace(b, x=a.x, y=a.y)
    problems = P.check(solved)
    assert any("overlap" in p for p in problems)


def test_check_detects_a_non_member_node_inside_a_group_box():
    """docs/diagram-status.md re-audit finding B: check() previously had NO
    rule for a non-member node's rect overlapping a group's derived
    boundary, so a diagram whose picture LIED about containment (a node
    drawn inside a box the IR never put it in) passed check() clean. Feed it
    a hand-built violation directly (bypassing the layout algorithm, which
    layout()/repair_bands() now guarantees cannot produce this) and confirm
    it is flagged."""
    d = _spec_to_diagram(SPEC1)
    solved = P.solve(d)
    g = solved.groups[0]
    outsider = next(n for n in solved.nodes if n.group != g.id)
    # place the outsider's rect fully inside the group's own rect
    solved.nodes[solved.nodes.index(outsider)] = replace(
        outsider, x=g.x + 1, y=g.y + 1,
        w=max(1.0, g.w - 2), h=max(1.0, g.h - 2),
    )
    problems = P.check(solved)
    assert any("overlaps foreign group" in p for p in problems), problems


def test_check_does_not_flag_a_groups_own_members():
    # sanity: the new rule must not be vacuously true by flagging everyone.
    # A group's OWN members legitimately sit inside its box.
    d = _spec_to_diagram(SPEC3)  # has 3 groups
    solved = P.solve(d)
    problems = P.check(solved)
    assert not any("overlaps foreign group" in p for p in problems), problems


def test_group_span_gap_never_traps_a_stranger_inside_the_box():
    """Construction guarantee for finding B: a group whose members are
    spread across non-adjacent ranks (a rank "gap" filled by a ghost item
    internally) with strangers threaded through those gap ranks, pulled
    toward opposite ends by unrelated anchors on either side -- the
    adversarial shape most likely to defeat a per-rank/per-round heuristic
    -- must still end up with every stranger clear of the group's final
    derived box. Regression test for repair_bands()'s deterministic closing
    pass (docs/diagram-status.md re-audit, 2026-07-16)."""
    nodes = [
        DiagramNode(id="g0", label="G0", type="service", group="pci"),
        DiagramNode(id="g3", label="G3", type="store", group="pci"),
        DiagramNode(id="top_a", label="TopA", type="client"),
        DiagramNode(id="bot_a", label="BotA", type="client"),
        DiagramNode(id="mid_hi", label="MidHi", type="external"),
        DiagramNode(id="mid_lo", label="MidLo", type="external"),
        DiagramNode(id="stripe1", label="Stripe1", type="external"),
        DiagramNode(id="stripe2", label="Stripe2", type="external"),
    ] + [
        DiagramNode(id=f"clutter{i}", label=f"Clutter{i}", type="service")
        for i in range(6)
    ]
    edges = [
        DiagramEdge(source="top_a", target="g0"),
        DiagramEdge(source="bot_a", target="g3"),
        DiagramEdge(source="g0", target="stripe1", style="dashed"),
        DiagramEdge(source="stripe1", target="stripe2", style="dashed"),
        DiagramEdge(source="stripe2", target="g3", style="dashed"),
        DiagramEdge(source="mid_hi", target="stripe1"),
        DiagramEdge(source="mid_lo", target="stripe2"),
        DiagramEdge(source="top_a", target="mid_hi"),
        DiagramEdge(source="bot_a", target="mid_lo"),
    ] + [
        DiagramEdge(source=("top_a" if i % 2 == 0 else "bot_a"), target=f"clutter{i}")
        for i in range(6)
    ] + [
        DiagramEdge(source=f"clutter{i}", target=("stripe1" if i % 2 == 0 else "stripe2"))
        for i in range(6)
    ]
    d = Diagram(
        id="t", title="repro", direction="LR",
        groups=[DiagramGroup(id="pci", label="PCI boundary", kind="security-group")],
        nodes=nodes, edges=edges,
    )
    for target_aspect in (2.0, 1.0, 3.5, 0.6):
        solved = P.solve(d, target_aspect=target_aspect)
        problems = P.check(solved)
        assert problems == [], (target_aspect, problems)


def test_layout_report_is_json_serializable():
    d = _spec_to_diagram(SPEC3)
    report = P.layout_report(P.solve(d))
    dumped = json.dumps(report)  # must not raise
    assert '"width"' in dumped and '"nodes"' in dumped and '"edges"' in dumped


# ---------------------------------------------------------------------------
# defect 2: aspect ratio for slides (target_aspect + auto-flip)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", BAKEOFF_SPECS, ids=[s["title"] for s in BAKEOFF_SPECS])
def test_aspect_within_band_for_all_bakeoff_specs(spec):
    d = _spec_to_diagram(spec)
    solved = P.solve(d, target_aspect=2.2)
    aspect = solved.width / solved.height
    assert 1.4 <= aspect <= 2.6, f"{spec['title']!r}: aspect {aspect:.2f}"


def test_spec5_auto_flips_to_landscape_for_a_landscape_target():
    # spec5's own direction is TB and solves to a ~0.60:1 portrait result;
    # against a landscape target_aspect, solve() must try LR instead and
    # land in-band (docs/diagram-plan.md section 3, defect 2).
    d = _spec_to_diagram(SPEC5)
    assert d.direction == "TB"
    solved = P.solve(d, target_aspect=2.2)
    assert solved.direction == "LR"
    assert 1.4 <= solved.width / solved.height <= 2.6


def test_spec5_direction_is_untouched_at_default_target_aspect_two():
    # sanity: the flip is target_aspect-gated, not unconditional. At the
    # module default (2.0, still landscape) it should also flip, but the
    # returned SolvedDiagram.direction must always reflect whichever
    # direction was ACTUALLY used, and TB-solved geometry must never be
    # silently mislabeled LR or vice versa.
    d = _spec_to_diagram(SPEC5)
    solved = P.solve(d)
    assert solved.direction in ("LR", "TB")
    aspect = solved.width / solved.height
    if solved.direction == "TB":
        assert aspect < 1.0
    else:
        assert aspect >= 1.0


def test_auto_flip_does_not_trigger_for_a_portrait_target():
    # the flip is gated on target_aspect >= 1.0 (a landscape ask); asking
    # for a portrait target must not force a direction change.
    d = _spec_to_diagram(SPEC5)
    solved = P.solve(d, target_aspect=0.5)
    assert solved.direction == "TB"


# ---------------------------------------------------------------------------
# defect 1: fan-in edge-label attribution
# ---------------------------------------------------------------------------


def test_fan_in_four_edges_label_attribution():
    d = _fan_in_diagram(4)
    solved = P.solve(d)
    assert P.check(solved) == []

    node_rects = [(n.x, n.y, n.w, n.h) for n in solved.nodes]
    for e in solved.edges:
        assert e.label_box is not None
        x, y, w, h = e.label_box
        # not sitting on top of any node's own text
        for nr in node_rects:
            assert not P._rect_overlaps_rect(e.label_box, nr)
        # attributable to exactly its own edge: the box must not touch any
        # OTHER edge's routed polyline
        for other in solved.edges:
            if other is e:
                continue
            for a, b in zip(other.pts, other.pts[1:]):
                assert not P._seg_intersects_rect(a, b, (x, y, w, h)), (
                    f"{e.source}->{e.target}'s label box touches "
                    f"{other.source}->{other.target}'s line"
                )


def test_fan_in_labels_stay_distinct_from_each_other():
    d = _fan_in_diagram(4)
    solved = P.solve(d)
    boxes = [e.label_box for e in solved.edges if e.label_box]
    assert len(boxes) == 4
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not P._rect_overlaps_rect(a, b)


# ---------------------------------------------------------------------------
# defect 3: group titles struck through by edges (z-order)
# ---------------------------------------------------------------------------


def test_group_caption_plate_is_painted_after_edges():
    # the caption plate (a fixed-height-15 opaque rect with no stroke, drawn
    # right before the caption text) must appear, in the emitted SVG source,
    # after every edge <path> -- otherwise a bend crossing the header band
    # paints over it again (the bug this defect describes).
    d = _spec_to_diagram(SPEC3)  # has 3 groups, several of them dense
    svg = P.paint_svg(P.solve(d, target_aspect=2.2))
    last_edge_path = svg.rfind('marker-end="url(#ar_')
    first_caption_plate = svg.find('height="15" rx="3"')
    assert last_edge_path != -1 and first_caption_plate != -1
    assert first_caption_plate > last_edge_path


def test_every_group_caption_survives_in_the_svg_text():
    d = _spec_to_diagram(SPEC3)
    svg = P.paint_svg(P.solve(d))
    solved = P.solve(d)
    for g in solved.groups:
        assert P.esc(g.label) in svg


# ---------------------------------------------------------------------------
# defect 4: palette derives from theme, clamped rotation
# ---------------------------------------------------------------------------


def test_palette_service_and_store_use_theme_hex_as_is():
    theme = dict(P.THEME, primary="#FF0000", accent="#00FF00")
    pal = P.kind_palette(theme)
    assert pal["service"]["bar"] == "#FF0000"
    assert pal["store"]["bar"] == "#00FF00"


def test_palette_external_is_theme_neutral_unchanged():
    theme = dict(P.THEME, primary="#FF0000", accent="#00FF00")
    pal = P.kind_palette(theme)
    assert pal["external"] == {
        "fill": theme["surface"], "line": theme["muted"], "bar": theme["muted"],
    }


@pytest.mark.parametrize("kind", ["client", "cloud", "queue"])
def test_palette_rotated_kinds_stay_within_40_degrees_of_brand_hue(kind):
    theme = dict(P.THEME)
    pal = P.kind_palette(theme)
    ph, ah = P._hue(theme["primary"]), P._hue(theme["accent"])
    hue = P._hue(pal[kind]["bar"])

    def circular_dist(a, b):
        d = abs(a - b) % 360
        return min(d, 360 - d)

    # +1.0 deg slack: hue is re-extracted from an 8-bit RGB hex round-trip
    # (hsl -> rgb -> hex -> hsl), which is not perfectly hue-preserving
    assert min(circular_dist(hue, ph), circular_dist(hue, ah)) <= 40 + 1.0


def test_palette_reacts_to_a_different_brand():
    # rotating the theme's own hues must move the derived kinds with it (the
    # previous kind_palette used FIXED absolute offsets and ignored the
    # theme entirely for anything but service/store).
    pal_blue = P.kind_palette(dict(P.THEME, primary="#1D4ED8", accent="#0E9F6E"))
    pal_purple = P.kind_palette(dict(P.THEME, primary="#7C3AED", accent="#DB2777"))
    assert pal_blue["client"]["bar"] != pal_purple["client"]["bar"]


# ---------------------------------------------------------------------------
# finding 9 (docs/diagram-status.md): "client" and "security" measured as
# perceptually identical kind colors -- bar #5E3C9F vs #65429A, RGB distance
# 10.5; fills 1.7 apart. Root cause: both were anchored on primary's hue with
# offsets that a shared +/-40 degree clamp collapsed to four degrees apart.
# The fix fans all four rotated kinds (client, cloud, queue, security) out
# from primary's hue alone at fixed, well-separated offsets instead.
# ---------------------------------------------------------------------------

# A theme deliberately chosen so primary and accent are close in HUE (both
# blue, differing only in lightness) -- the "known hard problem" the plan
# names: a monochrome-leaning theme is exactly the case where the OLD
# dual-anchor scheme (client/cloud anchored on primary, queue/security
# anchored on accent) collapsed a DIFFERENT pair, cloud/queue, into each
# other once the two anchors sat close together. primary != accent (so
# service and store, which use the theme hex AS IS, stay distinguishable --
# that is the theme's own choice, not kind_palette's rotation logic, so it is
# excluded from the all-pairs sweep below), but they read as one hue family.
MONOCHROME_THEME = dict(P.THEME, primary="#1E3A8A", accent="#3B82F6")

# The minimum RGB distance (Euclidean over 0-255 channels, the same metric
# docs/diagram-status.md finding 9 itself was measured with) every PAIR of
# kind "bar" colors must clear. Comfortably below every measured pair for
# both fixture themes (48.7 default, 62.9 monochrome) and comfortably above
# the old collision (10.5), so this is a real regression guard, not a
# tautology.
MIN_KIND_BAR_DISTANCE = 35.0

# (service, store) is excluded from the all-pairs sweep: those two kinds use
# theme.primary/theme.accent AS IS (docs/diagram-plan.md section 3's explicit
# design), so their separation is entirely a property of the CALLER's own
# theme choice, not of kind_palette's rotation logic -- a theme whose primary
# and accent are themselves near-identical hex values will always produce a
# near-identical service/store pair, and no rotation formula can fix that
# without breaking the "service and store must be unmistakably on-brand"
# requirement the plan calls out by name.
_EXCLUDED_FROM_DISTANCE_SWEEP = {frozenset({"service", "store"})}


def _rgb_distance(hex1: str, hex2: str) -> float:
    r1, g1, b1 = P._hex2rgb(hex1)
    r2, g2, b2 = P._hex2rgb(hex2)
    return math.sqrt(
        ((r1 - r2) * 255) ** 2 + ((g1 - g2) * 255) ** 2 + ((b1 - b2) * 255) ** 2
    )


@pytest.mark.parametrize(
    "theme", [dict(P.THEME), MONOCHROME_THEME], ids=["default", "monochrome"]
)
def test_every_pair_of_kind_bar_colors_is_perceptually_distinct(theme):
    pal = P.kind_palette(theme)
    kinds = list(pal)
    for a, b in itertools.combinations(kinds, 2):
        if frozenset({a, b}) in _EXCLUDED_FROM_DISTANCE_SWEEP:
            continue
        d = _rgb_distance(pal[a]["bar"], pal[b]["bar"])
        assert d >= MIN_KIND_BAR_DISTANCE, (
            f"{a!r} and {b!r} bar colors are only {d:.1f} RGB units apart "
            f"({pal[a]['bar']} vs {pal[b]['bar']}); the legend would print "
            "them as separate chips that read as the same color"
        )


def test_client_and_security_are_no_longer_the_same_color():
    # the exact fixture finding 9 was measured against: a legend that
    # includes both "client" and "security" kinds must print two visibly
    # different bar colors, not the #5E3C9F / #65429A near-collision this
    # regression-guards against.
    pal = P.kind_palette(dict(P.THEME))
    d = _rgb_distance(pal["client"]["bar"], pal["security"]["bar"])
    assert d >= MIN_KIND_BAR_DISTANCE, (
        f"client/security bar distance regressed to {d:.1f} (was 10.5 "
        "before the fix)"
    )


def test_security_keeps_its_own_warm_hue_offset_and_is_untested_for_40_degrees():
    # security is the one kind explicitly allowed OFF the +/-40-degree
    # on-brand band ("only security may keep a warm hue", kind_palette's own
    # docstring); it must still visibly move when the theme's primary hue
    # moves, i.e. it is still DERIVED from the theme, not a hardcoded color.
    pal_blue = P.kind_palette(dict(P.THEME, primary="#1D4ED8", accent="#0E9F6E"))
    pal_purple = P.kind_palette(dict(P.THEME, primary="#7C3AED", accent="#DB2777"))
    assert pal_blue["security"]["bar"] != pal_purple["security"]["bar"]


# ---------------------------------------------------------------------------
# finding 16 (docs/diagram-status.md): duplicate node/group ids must never
# be silently dropped, even when lint was bypassed.
# ---------------------------------------------------------------------------


def test_solve_raises_on_duplicate_node_id():
    d = Diagram(
        nodes=[
            DiagramNode(id="a", label="First A", type="service"),
            DiagramNode(id="a", label="Second A", type="store"),
        ],
        edges=[],
    )
    with pytest.raises(ValueError, match="duplicate node id|two nodes"):
        P.solve(d)


def test_solve_raises_on_duplicate_group_id():
    d = Diagram(
        nodes=[
            DiagramNode(id="a", label="A", type="service", group="g"),
            DiagramNode(id="b", label="B", type="service", group="g"),
        ],
        groups=[
            DiagramGroup(id="g", label="First group"),
            DiagramGroup(id="g", label="Second group"),
        ],
    )
    with pytest.raises(ValueError, match="duplicate group id|two groups"):
        P.solve(d)


def test_solve_with_unique_ids_does_not_raise():
    # sanity: the new guard must not be trigger-happy on ordinary diagrams.
    d = _spec_to_diagram(SPEC1)
    P.solve(d)  # must not raise


# ---------------------------------------------------------------------------
# finding 16 (docs/diagram-status.md): reserved legend space is a property
# of solve(), not an unconditional add -- paint_svg only draws into a band
# solve() actually reserved (SolvedDiagram.legend_h).
# ---------------------------------------------------------------------------


def test_legend_true_is_the_default_and_matches_old_behavior():
    d = _spec_to_diagram(SPEC1)
    default_solved = P.solve(d)
    explicit_solved = P.solve(d, legend=True)
    assert default_solved.legend_h == P.LEGEND_H
    assert default_solved.height == explicit_solved.height


def test_legend_false_reserves_no_extra_height():
    d = _spec_to_diagram(SPEC1)
    with_legend = P.solve(d, legend=True)
    without_legend = P.solve(d, legend=False)
    assert with_legend.legend_h == P.LEGEND_H
    assert without_legend.legend_h == 0.0
    assert without_legend.height == pytest.approx(with_legend.height - P.LEGEND_H)


def test_legend_kind_list_is_populated_even_when_space_is_not_reserved():
    # the kind list itself (SolvedDiagram.legend) is cheap and always
    # present, so a native emitter that wants to draw its OWN legend inside
    # ITS OWN reserved space still gets the data even if it solved with
    # legend=False for some other reason.
    d = _spec_to_diagram(SPEC1)
    solved = P.solve(d, legend=False)
    assert solved.legend_h == 0.0
    assert solved.legend  # non-empty: kinds are still recorded


def test_paint_svg_skips_the_legend_band_when_no_space_was_reserved():
    d = _spec_to_diagram(SPEC1)
    solved = P.solve(d, legend=False)
    svg = P.paint_svg(solved)
    # "service" is always in SPEC1's legend; if paint_svg respected the
    # contract it must not draw the legend swatch/label for it when no
    # room was reserved.
    assert 'fill="#6B7280">service</text>' not in svg


def test_paint_svg_draws_the_legend_band_when_space_was_reserved():
    d = _spec_to_diagram(SPEC1)
    solved = P.solve(d, legend=True)
    svg = P.paint_svg(solved)
    assert 'fill="#6B7280">service</text>' in svg


def test_legend_reservation_is_included_in_layout_report():
    d = _spec_to_diagram(SPEC1)
    report_with = P.layout_report(P.solve(d, legend=True))
    report_without = P.layout_report(P.solve(d, legend=False))
    assert report_with["legend_h"] == P.LEGEND_H
    assert report_without["legend_h"] == 0.0


# ---------------------------------------------------------------------------
# the `detail` degradation ladder (PPTX emitter font floor, section 4b)
# ---------------------------------------------------------------------------


def _sublabel_tag_diagram() -> Diagram:
    return Diagram(
        title="detail ladder",
        nodes=[
            DiagramNode(id="a", label="Service A", type="service",
                        sublabel="a fairly long descriptive sublabel line",
                        tag="v2"),
            DiagramNode(id="b", label="Service B", type="service"),
        ],
        edges=[DiagramEdge(source="a", target="b")],
    )


def test_detail_full_keeps_sublabel_and_tag():
    d = _sublabel_tag_diagram()
    solved = P.solve(d, detail="full")
    a = next(n for n in solved.nodes if n.id == "a")
    assert a.sublabel is not None
    assert a.tag == "v2"


def test_detail_label_plus_sub_drops_only_the_tag():
    d = _sublabel_tag_diagram()
    solved = P.solve(d, detail="label+sub")
    a = next(n for n in solved.nodes if n.id == "a")
    assert a.sublabel is not None
    assert a.tag is None


def test_detail_label_drops_both_sublabel_and_tag():
    d = _sublabel_tag_diagram()
    solved = P.solve(d, detail="label")
    a = next(n for n in solved.nodes if n.id == "a")
    assert a.sublabel is None
    assert a.tag is None


def test_detail_ladder_never_grows_the_node_box():
    d = _sublabel_tag_diagram()
    full = next(n for n in P.solve(d, detail="full").nodes if n.id == "a")
    sub = next(n for n in P.solve(d, detail="label+sub").nodes if n.id == "a")
    label = next(n for n in P.solve(d, detail="label").nodes if n.id == "a")
    assert label.h <= sub.h <= full.h


# ---------------------------------------------------------------------------
# render_svg(): hash stamp
# ---------------------------------------------------------------------------


def test_render_svg_is_stamped_with_the_diagram_hash():
    d = _spec_to_diagram(SPEC1)
    svg = P.render_svg(d)
    assert f'data-docloom-hash="{diagram_hash(d)}"' in svg
    assert svg.startswith("<svg data-docloom-hash=")


def test_render_svg_hash_changes_when_diagram_content_changes():
    d1 = _spec_to_diagram(SPEC1)
    d2 = _spec_to_diagram(SPEC1)
    d2.nodes[0].label = d2.nodes[0].label + " (renamed)"
    assert diagram_hash(d1) != diagram_hash(d2)
    assert f'data-docloom-hash="{diagram_hash(d1)}"' in P.render_svg(d1)
    assert f'data-docloom-hash="{diagram_hash(d2)}"' in P.render_svg(d2)


# ---------------------------------------------------------------------------
# golden SVG: exact byte-for-byte string equality (deterministic emitter)
# ---------------------------------------------------------------------------
# Re-pinned 2026-07-16 (docs/diagram-status.md re-audit finding A: group
# density). FLOW_GAP_LR moved 148 -> 128 as part of tightening column pitch
# to respond to actual node extents instead of a fixed constant (measured:
# a 10-node grouped diagram's fitted node label rose from 6.21pt to 6.86pt
# in a 16:9 content box at this and the other constant changes together).
# The only bytes that changed in this two-node, no-group fixture are the x
# coordinates of the second node and everything drawn relative to it (the
# edge line, the DB node body, its bar/tag, and the "writes" edge label),
# each shifted left by exactly 20px -- the FLOW_GAP_LR delta. Canvas size,
# legend, and both nodes' own geometry are byte-identical to before.
_GOLDEN_MINIMAL_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="259" '
    'viewBox="0 0 900 259" font-family="Segoe UI, Arial, sans-serif">\n'
    '<rect width="100%" height="100%" fill="#FFFFFF"/>\n'
    '<defs>\n'
    '<marker id="ar_muted" markerWidth="9" markerHeight="7" refX="8.4" '
    'refY="3.5" orient="auto" markerUnits="userSpaceOnUse">'
    '<path d="M0,0 L9,3.5 L0,7 Z" fill="#6B7280"/></marker>\n'
    '<marker id="ar_primary" markerWidth="9" markerHeight="7" refX="8.4" '
    'refY="3.5" orient="auto" markerUnits="userSpaceOnUse">'
    '<path d="M0,0 L9,3.5 L0,7 Z" fill="#1D4ED8"/></marker>\n'
    '<marker id="ar_accent" markerWidth="9" markerHeight="7" refX="8.4" '
    'refY="3.5" orient="auto" markerUnits="userSpaceOnUse">'
    '<path d="M0,0 L9,3.5 L0,7 Z" fill="#0E9F6E"/></marker>\n'
    '</defs>\n'
    '<text x="36" y="34" font-size="21" font-weight="700" '
    'fill="#111827">Golden Fixture</text>\n'
    '<rect x="36" y="44" width="48" height="3" rx="1.5" fill="#1D4ED8"/>\n'
    '<path d="M188.0,128.5 L316.0,128.5" fill="none" stroke="#6B7280" '
    'stroke-width="1.5" stroke-linecap="round" marker-end="url(#ar_muted)"/>\n'
    '<rect x="36.0" y="101.5" width="152.0" height="54.0" rx="9" '
    'fill="#EBF0FC" stroke="#97A8D8" stroke-width="1.3"/>\n'
    '<path d="M36.8,109.5 h4.0 v38.0 h-4.0 z" fill="#1D4ED8"/>\n'
    '<text x="114.5" y="126.8" font-size="14.5" font-weight="650" '
    'text-anchor="middle" fill="#111827">API</text>\n'
    '<text x="114.5" y="139.8" font-size="10.5" text-anchor="middle" '
    'fill="#6B7280">REST</text>\n'
    '<path d="M316.0,103.0 L316.0,154.0 A76.0,9.0 0 0 0 468.0,154.0 '
    'L468.0,103.0 A76.0,9.0 0 0 0 316.0,103.0 Z" fill="#EBFCF6" '
    'stroke="#97D8C2" stroke-width="1.3"/>\n'
    '<path d="M316.0,103.0 A76.0,9.0 0 0 0 468.0,103.0 A76.0,9.0 0 0 0 '
    '316.0,103.0" fill="none" stroke="#97D8C2" stroke-width="1.3"/>\n'
    '<path d="M316.8,105.0 h4.0 v47.0 h-4.0 z" fill="#0E9F6E"/>\n'
    '<text x="394.5" y="129.0" font-size="14.5" font-weight="650" '
    'text-anchor="middle" fill="#111827">DB</text>\n'
    '<rect x="382.4" y="133.5" width="24.2" height="14" rx="7" '
    'fill="#0E9F6E" fill-opacity="0.18" stroke="#0E9F6E" '
    'stroke-opacity="0.4" stroke-width="0.8"/>\n'
    '<text x="394.5" y="144.0" font-size="9.2" font-weight="700" '
    'text-anchor="middle" fill="#0E9F6E">v2</text>\n'
    '<rect x="207.2" y="121.5" width="38.4" height="14.0" rx="3" '
    'fill="#FFFFFF" fill-opacity="0.95" stroke="#6B7280" '
    'stroke-opacity="0.3" stroke-width="0.8"/>\n'
    '<text x="226.4" y="130.5" font-size="10.5" text-anchor="middle" '
    'fill="#6B7280">writes</text>\n'
    '<line x1="36" y1="205.0" x2="864.0" y2="205.0" stroke="#6B7280" '
    'stroke-opacity="0.3"/>\n'
    '<rect x="36.0" y="219.0" width="12" height="12" rx="3" fill="#EBF0FC" '
    'stroke="#1D4ED8"/>\n'
    '<rect x="36.5" y="219.0" width="3" height="12" rx="1.5" fill="#1D4ED8"/>\n'
    '<text x="53.0" y="229.0" font-size="10" fill="#6B7280">service</text>\n'
    '<rect x="104.2" y="219.0" width="12" height="12" rx="3" fill="#EBFCF6" '
    'stroke="#0E9F6E"/>\n'
    '<rect x="104.7" y="219.0" width="3" height="12" rx="1.5" fill="#0E9F6E"/>\n'
    '<text x="121.2" y="229.0" font-size="10" fill="#6B7280">store</text>\n'
    '<line x1="173.6" y1="225.0" x2="197.6" y2="225.0" stroke="#6B7280" '
    'stroke-width="1.5"/>\n'
    '<text x="203.6" y="229.0" font-size="10" fill="#6B7280">flow</text>\n'
    '<line x1="241.9" y1="225.0" x2="265.9" y2="225.0" stroke="#6B7280" '
    'stroke-width="1.5" stroke-dasharray="6 4"/>\n'
    '<text x="271.9" y="229.0" font-size="10" fill="#6B7280">'
    'async / return</text>\n'
    '<line x1="352.7" y1="225.0" x2="376.7" y2="225.0" stroke="#1D4ED8" '
    'stroke-width="2.3"/>\n'
    '<text x="382.7" y="229.0" font-size="10" fill="#6B7280">'
    'primary path</text>\n'
    '<line x1="458.1" y1="225.0" x2="482.1" y2="225.0" stroke="#0E9F6E" '
    'stroke-width="1.9" stroke-dasharray="9 3 2 3"/>\n'
    '<text x="488.1" y="229.0" font-size="10" fill="#6B7280">secure</text>\n'
    '</svg>'
)


def test_golden_svg_minimal_two_node_fixture():
    d = Diagram(
        id="mini",
        title="Golden Fixture",
        direction="LR",
        nodes=[
            DiagramNode(id="a", label="API", type="service", sublabel="REST"),
            DiagramNode(id="b", label="DB", type="store", tag="v2"),
        ],
        edges=[DiagramEdge(source="a", target="b", label="writes", style="solid")],
    )
    svg = P.paint_svg(P.solve(d))
    assert svg == _GOLDEN_MINIMAL_SVG


# ---------------------------------------------------------------------------
# estimate_depth(): lint.py's exact contract
# (node_ids: list[str], edges: list[tuple[str, str]]) -> int, layer count,
# 1 for a single node, back edges dropped, unknown/self-loop edges ignored.
# ---------------------------------------------------------------------------


def test_estimate_depth_single_node_is_one():
    assert P.estimate_depth(["a"], []) == 1


def test_estimate_depth_empty_is_zero():
    assert P.estimate_depth([], []) == 0


def test_estimate_depth_linear_chain():
    assert P.estimate_depth(["a", "b", "c"], [("a", "b"), ("b", "c")]) == 3


def test_estimate_depth_ignores_dangling_edges_without_raising():
    # lint.py calls this with ALL edges, including ones with a diagram/
    # dangling-edge violation (source/target not a real node id); it must
    # not raise.
    depth = P.estimate_depth(["a", "b"], [("a", "b"), ("a", "ghost"), ("nope", "b")])
    assert depth == 2


def test_estimate_depth_drops_self_loops():
    assert P.estimate_depth(["a", "b"], [("a", "b"), ("b", "b")]) == 2


def test_estimate_depth_breaks_cycles_like_rank_nodes():
    # a -> b -> c -> a is a cycle; the back edge is dropped, same as
    # rank_nodes(), so depth is still 3 not infinite/wrong.
    depth = P.estimate_depth(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "a")])
    assert depth == 3


@pytest.mark.parametrize("spec", BAKEOFF_SPECS, ids=[s["title"] for s in BAKEOFF_SPECS])
def test_estimate_depth_matches_solve_ranking_for_bakeoff_specs(spec):
    ids = [n["key"] for n in spec["nodes"]]
    edges = [(e["source"], e["target"]) for e in spec["edges"]]
    depth = P.estimate_depth(ids, edges)
    rank = P.rank_nodes(ids, spec["edges"])
    assert depth == max(rank.values()) + 1
