"""Tests for render/drawio.py (docs/diagram-plan.md section 4c, P3): the
.drawio (mxGraph XML) emitter, the CLI's --diagram-sources flag, and the
public one-shot docloom.render_diagram() API.

The 5 bake-off specs (scratchpad/bakeoff/specs/spec{1..5}.json) are embedded
verbatim, same rationale as tests/test_ir_diagram.py: this file must not
depend on the ephemeral scratchpad temp directory surviving between
sessions. _spec_to_diagram below is the same painter-spec -> IR Diagram
adapter tests/test_ir_diagram.py uses (each test file keeps its own copy per
this run's strict file-ownership rule).

XSD validation is against the vendored, official jgraph mxfile.xsd
(tests/data/mxfile.xsd). The lxml import is guarded with pytest.importorskip
as a defense-in-depth belt (lxml is not a runtime dependency of docloom, so a
consumer's install of just the base package must never need it); lxml itself
is listed in pyproject.toml's `dev` extra so the guard is not load-bearing on
a normal `pip install -e .[dev]` dev checkout -- the XSD oracle actually
executes there, it does not silently skip.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from docloom import cli
from docloom.ir import Diagram, DiagramEdge, DiagramGroup, DiagramNode, diagram_hash
from docloom.render.diagram_svg import (
    SolvedDiagram,
    SolvedEdge,
    SolvedGroup,
    SolvedNode,
    solve,
)
from docloom.render.drawio import render_drawio

DATA_DIR = Path(__file__).parent / "data"
XSD_PATH = DATA_DIR / "mxfile.xsd"

# ---------------------------------------------------------------------------
# the 5 bake-off specs, embedded verbatim (painter spec-dict vocabulary)
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


def _spec_to_diagram(spec: dict) -> Diagram:
    """Painter spec dict (key/kind/sub/tag vocabulary) -> IR Diagram."""
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


BAKEOFF_DIAGRAMS = [_spec_to_diagram(s) for s in BAKEOFF_SPECS]
BAKEOFF_IDS = [s["title"] for s in BAKEOFF_SPECS]


def _solved(d: Diagram):
    return solve(d, target_aspect=2.2)


# ---------------------------------------------------------------------------
# well-formedness + XSD validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("d", BAKEOFF_DIAGRAMS, ids=BAKEOFF_IDS)
def test_drawio_is_well_formed_xml(d):
    xml = render_drawio(d, _solved(d))
    root = ET.fromstring(xml)  # raises ParseError if malformed
    assert root.tag == "mxfile"


@pytest.mark.parametrize("d", BAKEOFF_DIAGRAMS, ids=BAKEOFF_IDS)
def test_drawio_validates_against_official_mxfile_xsd(d):
    lxml_etree = pytest.importorskip("lxml.etree")
    schema = lxml_etree.XMLSchema(lxml_etree.parse(str(XSD_PATH)))
    xml = render_drawio(d, _solved(d))
    doc = lxml_etree.fromstring(xml.encode("utf-8"))
    assert schema.validate(doc), schema.error_log


def test_drawio_uses_uncompressed_xml():
    """draw.io's own AI-generation guidance: compressed="false" always, so
    the file is a plain, greppable XML tree (docs/diagram-plan.md section
    4c)."""
    d = BAKEOFF_DIAGRAMS[0]
    xml = render_drawio(d, _solved(d))
    assert 'compressed="false"' in xml
    # and no base64-deflate payload masquerading as diagram content
    assert "<mxGraphModel" in xml


# ---------------------------------------------------------------------------
# referential integrity: every edge resolves, every child's parent resolves,
# child coordinates are RELATIVE to their container (official checklist #12)
# ---------------------------------------------------------------------------


def _parse_cells(xml: str):
    root = ET.fromstring(xml)
    root_cell = root.find("./diagram/mxGraphModel/root")
    return list(root_cell)


def _is_legend_cell(c) -> bool:
    """True for a legend cell (kind swatch/bar/label, header rule, or
    edge-style key line/label) emitted by drawio.py's _legend_cells(),
    identified by the `docloomLegend=1` marker every such cell's style
    string carries -- never by document position, since legend cells are
    real mxCell vertex/edge cells that must not be miscounted as diagram
    content in the referential-integrity/arrowhead/dangling-edge tests
    below (all written before the legend existed)."""
    return "docloomLegend=1" in (c.get("style") or "")


def _real_edge_cells(cells):
    """Edge cells that represent an actual Diagram edge (source/target set),
    excluding legend key lines (which are edge="1" but source/target-less,
    connecting nothing)."""
    return [c for c in cells if c.get("edge") == "1" and not _is_legend_cell(c)]


@pytest.mark.parametrize("d", BAKEOFF_DIAGRAMS, ids=BAKEOFF_IDS)
def test_referential_integrity_and_relative_child_coords(d):
    s = _solved(d)
    xml = render_drawio(d, s)
    cells = _parse_cells(xml)

    ids = {c.get("id") for c in cells}
    assert "0" in ids and "1" in ids

    vertex_ids = {c.get("id") for c in cells if c.get("vertex") == "1"}
    edge_cells = _real_edge_cells(cells)
    assert len(edge_cells) == len(s.edges)

    for c in edge_cells:
        assert c.get("source") in vertex_ids
        assert c.get("target") in vertex_ids
        assert c.get("parent") == "1"

    # legend cells (kind swatches, key labels) are real vertex="1" cells too
    # (drawio has no other way to place a colored chip or a text label), so
    # exclude them here the same way -- this test's job is diagram content
    # referential integrity, not the legend's own layout (covered separately
    # below by the legend-specific tests).
    vertex_cells = [c for c in cells if c.get("vertex") == "1" and not _is_legend_cell(c)]
    assert len(vertex_cells) == len(s.nodes) + len(s.groups)
    for c in vertex_cells:
        parent = c.get("parent")
        assert parent == "1" or parent in vertex_ids

    # group cells: parent is always the root layer, never nested
    group_cell_ids = set()
    for g in s.groups:
        matches = [c for c in vertex_cells if c.get("value") == g.label]
        assert matches, f"no vertex cell found for group {g.id!r}"
        gc = matches[0]
        assert gc.get("parent") == "1"
        geom = gc.find("mxGeometry")
        assert float(geom.get("x")) == pytest.approx(g.x, abs=0.15)
        assert float(geom.get("y")) == pytest.approx(g.y, abs=0.15)
        assert float(geom.get("width")) == pytest.approx(g.w, abs=0.15)
        assert float(geom.get("height")) == pytest.approx(g.h, abs=0.15)
        group_cell_ids.add(gc.get("id"))

    # node cells whose IR node carries a `group` sit inside that group's
    # cell, with LOCAL (container-relative) coordinates: id + group-origin
    # reconstructs the absolute canvas position solve() computed.
    by_group = {g.id: g for g in s.groups}
    for n in s.nodes:
        matches = [
            c for c in vertex_cells
            if c.get("id") not in group_cell_ids and n.label in (c.get("value") or "")
        ]
        assert matches, f"no vertex cell found for node {n.id!r}"
        nc = matches[0]
        geom = nc.find("mxGeometry")
        rel_x, rel_y = float(geom.get("x")), float(geom.get("y"))
        if n.group and n.group in by_group:
            g = by_group[n.group]
            assert nc.get("parent") in group_cell_ids
            assert (rel_x + g.x) == pytest.approx(n.x, abs=0.2)
            assert (rel_y + g.y) == pytest.approx(n.y, abs=0.2)
        else:
            assert nc.get("parent") == "1"
            assert rel_x == pytest.approx(n.x, abs=0.15)
            assert rel_y == pytest.approx(n.y, abs=0.15)


# ---------------------------------------------------------------------------
# Tier 1 / Tier 2 hash stamp (docs/diagram-plan.md section 1)
# ---------------------------------------------------------------------------


def test_hash_comment_present_and_matches_diagram_hash():
    d = BAKEOFF_DIAGRAMS[0]
    xml = render_drawio(d, _solved(d))
    assert f"<!-- docloom:hash:{diagram_hash(d)} -->" in xml


def test_hash_comment_changes_when_diagram_content_changes():
    d1 = BAKEOFF_DIAGRAMS[0]
    d2 = d1.model_copy(deep=True)
    d2.nodes[0].label = d2.nodes[0].label + " (renamed)"
    xml1 = render_drawio(d1, _solved(d1))
    xml2 = render_drawio(d2, _solved(d2))
    h1 = re.search(r"docloom:hash:([0-9a-f]+)", xml1).group(1)
    h2 = re.search(r"docloom:hash:([0-9a-f]+)", xml2).group(1)
    assert h1 != h2
    assert h1 == diagram_hash(d1)
    assert h2 == diagram_hash(d2)


def test_hash_comment_is_schema_invisible():
    """An XML comment does not participate in XSD content-model matching:
    the file validates whether or not the comment is there."""
    lxml_etree = pytest.importorskip("lxml.etree")
    schema = lxml_etree.XMLSchema(lxml_etree.parse(str(XSD_PATH)))
    d = BAKEOFF_DIAGRAMS[0]
    xml = render_drawio(d, _solved(d))
    assert "<!-- docloom:hash:" in xml
    doc = lxml_etree.fromstring(xml.encode("utf-8"))
    assert schema.validate(doc), schema.error_log


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("d", BAKEOFF_DIAGRAMS, ids=BAKEOFF_IDS)
def test_render_drawio_is_deterministic(d):
    """No wall-clock (or any other) non-determinism survives: two independent
    solve() + render_drawio() passes over the same Diagram are byte-identical,
    not just equal after masking a timestamp."""
    s1, s2 = _solved(d), _solved(d)
    xml1 = render_drawio(d, s1)
    xml2 = render_drawio(d, s2)
    assert xml1 == xml2


def test_render_drawio_has_no_modified_attribute():
    """`modified` was the one wall-clock byte range in the emitter (draw.io
    stamps it on every save, but it carries no semantic weight and is not
    part of the diagram_hash contract). It is optional in the official XSD
    (tests/data/mxfile.xsd), so the emitter omits it outright rather than
    fake a value -- a frozen epoch would be a lie a human could trip over."""
    d = BAKEOFF_DIAGRAMS[0]
    xml = render_drawio(d, _solved(d))
    assert "modified=" not in xml


def test_render_drawio_byte_identical_across_independent_renders():
    """Same Diagram, two completely independent solve()+render_drawio() call
    chains (fresh SolvedDiagram each time): the output bytes must match
    exactly, confirming the export is byte-deterministic end to end."""
    d = Diagram(
        title="determinism check",
        nodes=[DiagramNode(id="a", label="A"), DiagramNode(id="b", label="B")],
        edges=[DiagramEdge(source="a", target="b")],
    )
    first = render_drawio(d, solve(d, target_aspect=2.2))
    second = render_drawio(d, solve(d, target_aspect=2.2))
    assert first.encode("utf-8") == second.encode("utf-8")


# ---------------------------------------------------------------------------
# visual fidelity: node kind colors/shapes, edge styles, arrowheads, labels
# ---------------------------------------------------------------------------


def _diagram_with_all_kinds():
    kinds = ["service", "client", "store", "queue", "security", "cloud", "external"]
    nodes = [DiagramNode(id=k, label=k.title(), type=k) for k in kinds]
    edges = [
        DiagramEdge(source=kinds[i], target=kinds[i + 1])
        for i in range(len(kinds) - 1)
    ]
    return Diagram(title="all kinds", nodes=nodes, edges=edges)


def test_store_node_uses_cylinder_shape():
    d = _diagram_with_all_kinds()
    xml = render_drawio(d, _solved(d))
    cells = _parse_cells(xml)
    store_cell = next(c for c in cells if c.get("value", "").startswith("<b>Store"))
    assert "shape=cylinder3" in store_cell.get("style")


def test_external_node_is_dashed():
    d = _diagram_with_all_kinds()
    xml = render_drawio(d, _solved(d))
    cells = _parse_cells(xml)
    ext_cell = next(c for c in cells if c.get("value", "").startswith("<b>External"))
    assert "dashed=1" in ext_cell.get("style")


def test_node_kind_fill_colors_differ():
    """Distinct kinds -> distinct fillColor: the "node kind colors"
    requirement (docs/diagram-plan.md section 4)."""
    d = _diagram_with_all_kinds()
    xml = render_drawio(d, _solved(d))
    cells = _parse_cells(xml)

    def fill_of(label_prefix):
        c = next(c for c in cells if c.get("value", "").startswith(f"<b>{label_prefix}"))
        m = re.search(r"fillColor=([^;]+);", c.get("style"))
        return m.group(1)

    fills = {fill_of(k.title()) for k in ["Service", "Client", "Store", "Queue",
                                           "Security", "Cloud", "External"]}
    assert len(fills) >= 5  # service/store/external are always distinct;
    # the four brand-rotated kinds may share a hue family but should mostly
    # differ too -- this asserts real per-kind differentiation, not a flat
    # single color reused everywhere


def test_edge_styles_map_to_distinct_dashed_and_color():
    nodes = [DiagramNode(id="a", label="A"), DiagramNode(id="b", label="B"),
             DiagramNode(id="c", label="C"), DiagramNode(id="d2", label="D"),
             DiagramNode(id="e", label="E")]
    edges = [
        DiagramEdge(source="a", target="b", style="solid", label="s"),
        DiagramEdge(source="a", target="c", style="dashed", label="d"),
        DiagramEdge(source="a", target="d2", style="emphasis", label="e"),
        DiagramEdge(source="a", target="e", style="secure", label="x"),
    ]
    d = Diagram(title="edge styles", nodes=nodes, edges=edges)
    xml = render_drawio(d, _solved(d))
    cells = _parse_cells(xml)
    edge_cells = {c.get("value"): c.get("style") for c in cells if c.get("edge") == "1"}

    assert "dashed=0" in edge_cells["s"] and "strokeColor=#6B7280" in edge_cells["s"]
    assert "dashed=1" in edge_cells["d"] and "strokeColor=#6B7280" in edge_cells["d"]
    assert "dashed=0" in edge_cells["e"] and "strokeColor=#1D4ED8" in edge_cells["e"]
    assert "dashed=1" in edge_cells["x"] and "strokeColor=#0E9F6E" in edge_cells["x"]


def test_every_edge_has_an_arrowhead():
    # legend key lines are deliberately endArrow=none (a legend swatch line
    # is not itself a diagram edge and must not look like one) -- exclude
    # them via _real_edge_cells so this test only asserts the arrowhead
    # contract for actual Diagram edges.
    d = BAKEOFF_DIAGRAMS[2]
    xml = render_drawio(d, _solved(d))
    cells = _parse_cells(xml)
    edge_cells = _real_edge_cells(cells)
    assert edge_cells
    for c in edge_cells:
        style = c.get("style")
        assert "endArrow=block" in style
        assert "endFill=1" in style


def test_legend_key_lines_have_no_arrowhead():
    """The mirror of the assertion above: legend key lines are edge="1"
    cells (drawio's own vocabulary for an unconnected straight line) but are
    not diagram edges, so they must NOT carry an arrowhead -- an arrow on a
    legend swatch would misleadingly suggest direction."""
    d = BAKEOFF_DIAGRAMS[2]
    xml = render_drawio(d, _solved(d))
    cells = _parse_cells(xml)
    legend_lines = [c for c in cells if c.get("edge") == "1" and _is_legend_cell(c)]
    assert legend_lines  # the header rule plus 4 edge-style key lines
    for c in legend_lines:
        assert "endArrow=none" in c.get("style")


def test_edge_labels_survive_and_are_escaped():
    # repinned for finding 16: edge cells declare html=1 in their style (see
    # _style pairs in render_drawio), so draw.io's Electron app renders
    # `value` as HTML, not plain text. A single XML-decode (what ElementTree,
    # or draw.io's own mxGraph loader, does when parsing the file) is not
    # the end of the pipeline for an html=1 cell -- draw.io then decodes the
    # result AGAIN as HTML before painting it. The old assertion here
    # (`edge_cell.get("value") == "A & B <ok>"`) only proved the file is
    # well-formed XML; it did not prove the label is inert once draw.io's
    # HTML renderer gets it, and in fact it was not (see the hostile-label
    # test below). The label must come out of ONE XML-decode still carrying
    # HTML-entity syntax as literal text, so a SECOND (HTML) decode is
    # required to recover the original characters -- that second decode is
    # exactly what happens organically when draw.io paints an html=1 value,
    # and it is what makes the result "escaped text on screen" rather than
    # "a live tag".
    d = Diagram(
        title="labels",
        nodes=[DiagramNode(id="a", label="A"), DiagramNode(id="b", label="B")],
        edges=[DiagramEdge(source="a", target="b", label="A & B <ok>")],
    )
    xml = render_drawio(d, _solved(d))
    cells = _parse_cells(xml)
    edge_cell = next(c for c in cells if c.get("edge") == "1")
    # one XML-decode: still HTML-entity-escaped text, not the raw label
    assert edge_cell.get("value") == "A &amp; B &lt;ok&gt;"
    # a second (HTML) decode -- draw.io's own next step for an html=1 value
    # -- recovers the original text, proving it never becomes a live tag
    import html as html_mod
    assert html_mod.unescape(edge_cell.get("value")) == "A & B <ok>"
    assert "&amp;amp;" in xml and "&amp;lt;ok&amp;gt;" in xml  # double-escaped in the raw XML text


def test_group_and_edge_labels_with_hostile_markup_round_trip_as_inert_text():
    """finding 16: group and edge labels declare html=1 in their mxCell
    style but, before this fix, only went through quoteattr() (XML
    well-formedness) and never escape() (neutralizing them as HTML). A group
    label containing `<img src=x onerror=...>` therefore rendered as LIVE
    markup in draw.io's Electron app -- node labels were already safe
    because _node_label() escapes user text before wrapping it in its own
    <b>/<br/>/<font> tags. Covers a <script> tag, an <img onerror> tag, the
    CDATA terminator "]]>" (a classic XML-escaping edge case), and a bare
    "&", all in one payload, for both a group label and an edge label."""
    import html as html_mod

    hostile = '<script>alert(1)</script><img src=x onerror=alert(2)> ]]> a & b'
    d = Diagram(
        title="hostile",
        nodes=[
            DiagramNode(id="a", label="A", group="g1"),
            DiagramNode(id="b", label="B"),
        ],
        edges=[DiagramEdge(source="a", target="b", label=hostile)],
        groups=[DiagramGroup(id="g1", label=hostile, kind="region")],
    )
    xml = render_drawio(d, _solved(d))

    # still well-formed XML: escaping did not break the document
    root = ET.fromstring(xml)
    assert root.tag == "mxfile"

    cells = _parse_cells(xml)
    edge_cell = next(c for c in cells if c.get("edge") == "1")
    group_cell = next(c for c in cells if "container=1" in c.get("style", ""))

    for cell, what in ((edge_cell, "edge"), (group_cell, "group")):
        value = cell.get("value")
        # no raw tag survives a single XML-decode: draw.io's html=1 value
        # renderer would otherwise parse this as real markup
        assert "<script>" not in value, what
        assert "<img" not in value, what
        # a second (HTML) decode -- draw.io's own rendering step for an
        # html=1 value -- recovers the exact original string as inert,
        # displayed text, never executed markup
        assert html_mod.unescape(value) == hostile, what


@pytest.mark.parametrize("payload", [
    '<img src=x onerror=alert(1)>',
    '<script>alert(1)</script>',
    ']]>',
    'a & b',
    'she said "hello" & <b>bye</b>',  # a literal quote inside the label
], ids=["img-onerror", "script-tag", "cdata-terminator", "bare-ampersand", "literal-quote"])
def test_hostile_labels_round_trip_as_inert_text_group_and_edge(payload):
    """Independent re-verification (this run does not trust the prior wave's
    own tests as proof of the prior wave's own fix): every one of these five
    hostile payloads, individually, in BOTH a group label and an edge label
    (both declare html=1 in their mxCell style, so both go through draw.io's
    HTML renderer on load), must:
      1. produce well-formed XML (quoteattr()'s job -- a literal `"` or `<`
         loose in an attribute value would otherwise break the document,
         XSD validation would then fail as an unrelated-looking symptom)
      2. never expose a raw `<script>`/`<img` tag after a single XML-decode
         (escape()'s job -- this is the actual injection this test defends
         against: an un-escaped html=1 value is live markup, not text)
      3. recover to the exact original payload after the SECOND (HTML)
         decode draw.io itself performs when painting an html=1 value --
         proving the text is preserved, not merely broken/mangled into
         safety.
    """
    import html as html_mod

    d = Diagram(
        title="hostile-param",
        nodes=[
            DiagramNode(id="a", label="A", group="g1"),
            DiagramNode(id="b", label="B"),
        ],
        edges=[DiagramEdge(source="a", target="b", label=payload)],
        groups=[DiagramGroup(id="g1", label=payload, kind="region")],
    )
    xml = render_drawio(d, _solved(d))

    root = ET.fromstring(xml)  # (1) well-formed
    assert root.tag == "mxfile"

    cells = _parse_cells(xml)
    edge_cell = next(c for c in cells if c.get("edge") == "1" and not _is_legend_cell(c))
    group_cell = next(c for c in cells if "container=1" in c.get("style", ""))

    for cell, what in ((edge_cell, "edge"), (group_cell, "group")):
        value = cell.get("value") or ""
        assert "<script>" not in value, what          # (2)
        assert "<img" not in value, what               # (2)
        assert html_mod.unescape(value) == payload, what  # (3)

    # XSD validation ties it together: a payload that broke escaping in a
    # way quoteattr()/ET tolerated but the schema does not (e.g. a stray
    # byte sequence) would still be caught here.
    lxml_etree = pytest.importorskip("lxml.etree")
    schema = lxml_etree.XMLSchema(lxml_etree.parse(str(XSD_PATH)))
    doc = lxml_etree.fromstring(xml.encode("utf-8"))
    assert schema.validate(doc), schema.error_log


def test_hostile_label_in_legend_kind_name_cannot_occur_but_node_label_path_is_also_safe():
    """The legend's own text (`s.legend` kind names, edge-style key names)
    comes from a fixed, code-controlled vocabulary (DiagramNode.type is a
    validated Literal in ir.py, never arbitrary user text), so it is not an
    injection surface the way group/edge labels are -- this test instead
    re-confirms the node-label path (already escape()'d before this task,
    per _node_label's docstring) holds under the same hostile payload, since
    a node can carry a group membership that places it visually beside the
    legend and both paint from the same fillColor/strokeColor family.
    """
    import html as html_mod

    payload = '<script>alert(1)</script><img src=x onerror=alert(2)> ]]> a & b "q"'
    d = Diagram(
        title="hostile node",
        nodes=[DiagramNode(id="a", label=payload, sublabel=payload, tag=payload)],
        edges=[],
    )
    xml = render_drawio(d, _solved(d))
    root = ET.fromstring(xml)
    assert root.tag == "mxfile"
    cells = _parse_cells(xml)
    node_cell = next(c for c in cells if c.get("vertex") == "1" and not _is_legend_cell(c))
    value = node_cell.get("value")
    assert "<script>" not in value
    assert "<img" not in value
    assert html_mod.unescape(value).count(payload) == 3  # label + sublabel + tag


def test_edge_with_no_label_has_empty_value():
    d = Diagram(
        title="no label",
        nodes=[DiagramNode(id="a", label="A"), DiagramNode(id="b", label="B")],
        edges=[DiagramEdge(source="a", target="b")],
    )
    xml = render_drawio(d, _solved(d))
    cells = _parse_cells(xml)
    edge_cell = next(c for c in cells if c.get("edge") == "1")
    assert edge_cell.get("value") in ("", None)


# ---------------------------------------------------------------------------
# z-order: groups first, then nodes, then edges (docs/diagram-plan.md
# section 4b's ordering, applied identically here)
# ---------------------------------------------------------------------------


def test_group_nodes_edges_document_order():
    d = BAKEOFF_DIAGRAMS[2]  # spec3: 3 groups, 14 nodes, 20 edges
    xml = render_drawio(d, _solved(d))
    cells = _parse_cells(xml)
    kinds = []
    for c in cells:
        if c.get("id") in ("0", "1"):
            continue
        if c.get("edge") == "1":
            kinds.append("edge")
        elif c.get("style", "").find("container=1") != -1:
            kinds.append("group")
        else:
            kinds.append("node")
    last_group = max(i for i, k in enumerate(kinds) if k == "group")
    first_edge = min(i for i, k in enumerate(kinds) if k == "edge")
    first_node = min(i for i, k in enumerate(kinds) if k == "node")
    assert last_group < first_node < first_edge


# ---------------------------------------------------------------------------
# defensive: a dangling edge in a hand-built SolvedDiagram must not crash the
# emitter and must not appear in the output (solve() itself would already
# refuse a genuinely dangling edge -- confirmed: it KeyErrors during
# rank_nodes before a SolvedDiagram ever exists, matching lint's
# diagram/dangling-edge error -- so this exercises render_drawio's own
# defensive guard directly, for any future caller that builds/edits a
# SolvedDiagram by hand instead of going through solve()).
# ---------------------------------------------------------------------------


def test_dangling_edge_in_hand_built_solved_diagram_is_skipped_not_raised():
    d = Diagram(
        title="hand built",
        nodes=[DiagramNode(id="a", label="A")],
        edges=[],  # the IR itself has no edges; only the SolvedDiagram below does
    )
    s = SolvedDiagram(
        width=200, height=200, title="hand built",
        nodes=[SolvedNode(id="a", type="service", label="A", sublabel=None,
                           tag=None, group=None, x=10, y=10, w=100, h=50)],
        edges=[SolvedEdge(source="a", target="ghost", label=None, style="solid",
                           pts=[(10, 10), (200, 200)], label_box=None)],
        groups=[], legend=["service"], direction="LR",
    )
    xml = render_drawio(d, s)  # must not raise
    cells = _parse_cells(xml)
    # the dangling edge itself must not survive; legend key lines are a
    # separate, legitimate source of edge="1" cells (this SolvedDiagram sets
    # legend=["service"] with the default legend_h > 0), so exclude those
    # via _real_edge_cells rather than asserting zero edge="1" cells overall.
    edge_cells = _real_edge_cells(cells)
    assert edge_cells == []


# ---------------------------------------------------------------------------
# legend: finding 16's legend_h contract -- reserved band == drawn band
# (docs/diagram-status.md; diagram_svg.paint_svg and render/diagram_pptx.py
# both draw a real legend into this band, .drawio used to inherit the
# reservation and draw nothing in it)
# ---------------------------------------------------------------------------


def test_legend_is_drawn_when_reserved():
    d = BAKEOFF_DIAGRAMS[0]
    s = _solved(d)
    assert s.legend and s.legend_h > 0  # sanity: this spec's solve() did
    # reserve a band -- otherwise this test would trivially pass for the
    # wrong reason
    xml = render_drawio(d, s)
    cells = _parse_cells(xml)
    legend_cells = [c for c in cells if _is_legend_cell(c)]
    assert legend_cells

    # every kind in s.legend gets a text label cell somewhere in the legend
    legend_label_texts = {
        c.get("value") for c in legend_cells
        if c.get("vertex") == "1" and (c.get("value") or "")
    }
    for kind in s.legend:
        assert kind in legend_label_texts

    # and the edge-style key names are present too
    for _style_key, name in (("solid", "flow"), ("dashed", "async / return"),
                              ("emphasis", "primary path"), ("secure", "secure")):
        assert name in legend_label_texts

    # the header rule + 4 key lines are unconnected edge="1" cells (no
    # source/target), confirmed distinct from real diagram edges
    legend_lines = [c for c in legend_cells if c.get("edge") == "1"]
    assert len(legend_lines) == 5
    for c in legend_lines:
        assert c.get("source") is None and c.get("target") is None
        geom = c.find("mxGeometry")
        assert geom.get("relative") == "1"
        pts = {p.get("as") for p in geom.findall("mxPoint")}
        assert pts == {"sourcePoint", "targetPoint"}


def test_legend_omitted_when_solve_reserves_no_band():
    """solve(d, legend=False) reserves legend_h == 0; render_drawio must not
    draw a legend into a band that was never reserved (it would paint over
    the diagram's own last row of content, exactly the class of bug the
    legend_h contract exists to prevent)."""
    d = BAKEOFF_DIAGRAMS[0]
    s = solve(d, target_aspect=2.2, legend=False)
    assert s.legend_h == 0
    xml = render_drawio(d, s)
    cells = _parse_cells(xml)
    assert not any(_is_legend_cell(c) for c in cells)


def test_legend_cells_still_validate_against_xsd():
    lxml_etree = pytest.importorskip("lxml.etree")
    schema = lxml_etree.XMLSchema(lxml_etree.parse(str(XSD_PATH)))
    d = BAKEOFF_DIAGRAMS[2]  # 3 groups, real edges, and a legend all at once
    s = _solved(d)
    assert s.legend  # this spec has a non-empty legend
    xml = render_drawio(d, s)
    doc = lxml_etree.fromstring(xml.encode("utf-8"))
    assert schema.validate(doc), schema.error_log


def test_legend_swatch_colors_match_node_kind_colors():
    """The legend's own contract: a swatch's fillColor for a given kind must
    be the exact color that kind's nodes use (kind_palette(theme) is the one
    shared source both draw from), so the legend is a truthful key, not a
    decorative guess."""
    d = _diagram_with_all_kinds()
    xml = render_drawio(d, _solved(d))
    cells = _parse_cells(xml)

    def node_fill(label_prefix):
        c = next(c for c in cells if c.get("value", "").startswith(f"<b>{label_prefix}"))
        return re.search(r"fillColor=([^;]+);", c.get("style")).group(1)

    # find the legend chip immediately followed (in the emitted style pairs)
    # by the same fillColor as the matching node -- locate via the label
    # text cell, then the chip two cells before it (chip, bar, label order,
    # see drawio.py's _legend_cells).
    legend_cells = [c for c in cells if _is_legend_cell(c)]
    for kind in ("service", "store", "external"):
        label_cell = next(c for c in legend_cells if c.get("value") == kind)
        idx = legend_cells.index(label_cell)
        chip_cell = legend_cells[idx - 2]
        chip_fill = re.search(r"fillColor=([^;]+);", chip_cell.get("style")).group(1)
        assert chip_fill == node_fill(kind.title())


# ---------------------------------------------------------------------------
# theme adaptation
# ---------------------------------------------------------------------------


def test_custom_theme_changes_colors():
    d = Diagram(
        title="themed", nodes=[DiagramNode(id="a", label="A")], edges=[],
    )
    default_xml = render_drawio(d, _solved(d))
    custom = {"primary": "#FF00AA", "accent": "#00FFAA"}
    custom_xml = render_drawio(d, _solved(d), custom)
    assert default_xml != custom_xml


# ---------------------------------------------------------------------------
# the public one-shot API: docloom.render_diagram(d, theme, fmt="drawio")
# ---------------------------------------------------------------------------


def test_render_diagram_public_api_drawio():
    import docloom
    d = BAKEOFF_DIAGRAMS[0]
    xml = docloom.render_diagram(d, fmt="drawio")
    assert isinstance(xml, str)
    root = ET.fromstring(xml)
    assert root.tag == "mxfile"
    assert f"docloom:hash:{diagram_hash(d)}" in xml


def test_render_diagram_public_api_matches_direct_call():
    import docloom
    from docloom.theme import DEFAULT
    d = BAKEOFF_DIAGRAMS[1]
    via_api = docloom.render_diagram(d, DEFAULT, fmt="drawio")
    theme_dict = {
        "primary": DEFAULT.primary, "accent": DEFAULT.accent,
        "surface": DEFAULT.surface, "text": DEFAULT.text,
        "muted": DEFAULT.muted, "background": DEFAULT.background,
    }
    # render_diagram() calls solve() at its own default target_aspect (2.0),
    # not this file's _solved() helper (2.2, used elsewhere in this file to
    # match the plan's own aspect-assertion acceptance criteria) -- so the
    # direct-call comparison must solve() the same way the API does.
    direct = render_drawio(d, solve(d, theme_dict), theme_dict)
    assert via_api == direct


def test_render_diagram_rejects_unknown_format():
    import docloom
    d = BAKEOFF_DIAGRAMS[0]
    with pytest.raises(ValueError):
        docloom.render_diagram(d, fmt="pdf")


# ---------------------------------------------------------------------------
# CLI: --diagram-sources
# ---------------------------------------------------------------------------


def _write_doc(tmp_path: Path, blocks: list[dict]) -> Path:
    doc = {"title": "CLI Diagram Test", "blocks": blocks}
    p = tmp_path / "doc.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _diagram_block(diagram_id: str | None = "arch1") -> dict:
    block = {
        "type": "diagram", "title": "System", "caption": "how it fits together",
        "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B", "type": "store"}],
        "edges": [{"source": "a", "target": "b", "label": "writes"}],
    }
    if diagram_id is not None:
        block["id"] = diagram_id
    return block


def test_cli_diagram_sources_writes_sidecar_drawio(tmp_path):
    doc_path = _write_doc(tmp_path, [
        {"type": "heading", "level": 1, "text": "Architecture"},
        _diagram_block("arch1"),
    ])
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0
    md = out_dir / "cli-diagram-test.md"
    drawio = out_dir / "cli-diagram-test.diagrams" / "arch1.drawio"
    assert md.is_file()
    assert drawio.is_file()
    xml = drawio.read_text(encoding="utf-8")
    root = ET.fromstring(xml)
    assert root.tag == "mxfile"


def test_cli_without_diagram_sources_writes_no_sidecar(tmp_path):
    doc_path = _write_doc(tmp_path, [_diagram_block("arch1")])
    out_dir = tmp_path / "out"
    code = cli.main(["render", str(doc_path), "-f", "md", "-o", str(out_dir)])
    assert code == 0
    assert not (out_dir / "cli-diagram-test.diagrams").exists()


def test_cli_diagram_sources_falls_back_to_index_when_no_id(tmp_path):
    doc_path = _write_doc(tmp_path, [_diagram_block(diagram_id=None)])
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0
    assert (out_dir / "cli-diagram-test.diagrams" / "0.drawio").is_file()


def test_cli_diagram_sources_is_noop_when_document_has_no_diagrams(tmp_path):
    doc_path = _write_doc(tmp_path, [{"type": "paragraph", "text": "no diagrams here"}])
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0
    assert not (out_dir / "cli-diagram-test.diagrams").exists()


def test_cli_diagram_sources_multiple_diagrams_get_distinct_files(tmp_path):
    doc_path = _write_doc(tmp_path, [_diagram_block("first"), _diagram_block("second")])
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0
    diagrams_dir = out_dir / "cli-diagram-test.diagrams"
    assert (diagrams_dir / "first.drawio").is_file()
    assert (diagrams_dir / "second.drawio").is_file()
