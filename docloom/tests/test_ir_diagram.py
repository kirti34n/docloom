"""IR tests for the Diagram block (docs/diagram-plan.md section 2):
Diagram/DiagramNode/DiagramEdge/DiagramGroup validate, participate in the
Block union, survive the LLM structured-output schema pipeline, and
diagram_hash() is the stable Tier 1/Tier 2 content stamp.

This file owns IR-level checks only. Diagram lint rules (diagram/empty,
diagram/dangling-edge, ...) are tested as additions to the existing lint
test suite, not here. The painter (solve/paint_svg/estimate_depth) is P0's
file and is not exercised here.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from docloom.ir import (
    Block, Diagram, DiagramEdge, DiagramGroup, DiagramNode, Document, Slide,
    diagram_hash,
)
from docloom.llm import llm_schema

# ---------------------------------------------------------------------------
# The 5 bake-off specs (scratchpad/bakeoff/specs/spec{1..5}.json), embedded
# verbatim so this test does not depend on the ephemeral scratchpad temp
# directory surviving. Field names are the painter's spec-dict vocabulary
# (key/kind/sub/tag) that _to_spec (P0, render/diagram_svg.py) will read;
# _spec_to_diagram below is the inverse: painter spec -> IR Diagram.
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
    """Painter spec dict (key/kind/sub/tag vocabulary) -> IR Diagram. The
    inverse of the _to_spec adapter P0 writes in render/diagram_svg.py."""
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


# ------------------------------------------------------------- bake-off specs


@pytest.mark.parametrize("spec", BAKEOFF_SPECS, ids=[s["title"] for s in BAKEOFF_SPECS])
def test_bakeoff_spec_translates_and_validates_as_diagram(spec):
    d = _spec_to_diagram(spec)
    assert d.type == "diagram"
    assert len(d.nodes) == len(spec["nodes"])
    assert len(d.edges) == len(spec["edges"])
    assert len(d.groups) == len(spec.get("groups", []))
    # every id/source/target/group survived the round trip untouched
    assert {n.id for n in d.nodes} == {n["key"] for n in spec["nodes"]}
    for e, src in zip(d.edges, spec["edges"]):
        assert (e.source, e.target) == (src["source"], src["target"])


def test_all_five_bakeoff_specs_are_distinct_and_all_validate():
    diagrams = [_spec_to_diagram(s) for s in BAKEOFF_SPECS]
    assert len(diagrams) == 5
    assert len({diagram_hash(d) for d in diagrams}) == 5  # no two collide


# ------------------------------------------------------------------- defaults


def test_diagram_node_defaults():
    n = DiagramNode(id="a", label="A")
    assert n.type == "service"
    assert n.sublabel is None
    assert n.tag is None
    assert n.group is None


def test_diagram_edge_defaults():
    e = DiagramEdge(source="a", target="b")
    assert e.label is None
    assert e.style == "solid"


def test_diagram_group_defaults():
    g = DiagramGroup(id="g", label="Group")
    assert g.kind == "region"


def test_diagram_defaults():
    d = Diagram()
    assert d.type == "diagram"
    assert d.direction == "LR"
    assert d.nodes == []
    assert d.edges == []
    assert d.groups == []
    assert d.alt == ""


def test_diagram_node_accepts_all_seven_kinds():
    for kind in ("service", "client", "store", "queue", "security", "cloud", "external"):
        assert DiagramNode(id="n", label="N", type=kind).type == kind


def test_diagram_node_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        DiagramNode(id="n", label="N", type="frontend")  # archify's word, not ours


def test_diagram_edge_accepts_all_four_styles():
    for style in ("solid", "dashed", "emphasis", "secure"):
        assert DiagramEdge(source="a", target="b", style=style).style == style


def test_diagram_group_accepts_both_kinds():
    assert DiagramGroup(id="g", label="G", kind="region").kind == "region"
    assert DiagramGroup(id="g", label="G", kind="security-group").kind == "security-group"


def test_diagram_direction_lr_and_tb():
    assert Diagram(direction="LR").direction == "LR"
    assert Diagram(direction="TB").direction == "TB"


# --------------------------------------------------------------- Block union


def test_diagram_is_a_block_union_member():
    import typing
    assert Diagram in typing.get_args(Block)


def test_document_slide_carries_a_diagram_block_and_round_trips():
    d = Diagram(
        id="arch",
        title="Service topology",
        nodes=[
            DiagramNode(id="a", label="API", type="service"),
            DiagramNode(id="b", label="DB", type="store"),
        ],
        edges=[DiagramEdge(source="a", target="b", label="writes")],
    )
    doc = Document(title="T", slides=[Slide(layout="content", title="t", blocks=[d])])
    raw = doc.model_dump_json()
    back = Document.model_validate_json(raw)
    got = back.slides[0].blocks[0]
    assert isinstance(got, Diagram)
    assert got.id == "arch"
    assert [n.id for n in got.nodes] == ["a", "b"]
    assert got.edges[0].source == "a" and got.edges[0].target == "b"


def test_document_report_block_carries_a_diagram_and_save_load_round_trips(tmp_path):
    d = Diagram(nodes=[DiagramNode(id="a", label="A")])
    doc = Document(title="T", blocks=[d])
    path = tmp_path / "doc.json"
    doc.save(path)
    back = Document.load(path)
    assert isinstance(back.blocks[0], Diagram)
    assert back.blocks[0].nodes[0].id == "a"


# ---------------------------------------------------------------- SafeStr


def test_diagram_node_label_strips_control_characters():
    n = DiagramNode(id="a", label="bad\x00label\x1f")
    assert "\x00" not in n.label
    assert "\x1f" not in n.label


# ----------------------------------------------------------------- llm_schema


def test_llm_schema_includes_diagram_block():
    schema = llm_schema()
    text = json.dumps(schema)
    assert '"diagram"' in text
    assert "DiagramNode" in text
    assert "DiagramEdge" in text
    assert "DiagramGroup" in text


def test_llm_schema_stays_provider_safe_with_diagram_added():
    text = json.dumps(llm_schema())
    assert "oneOf" not in text  # rejected by OpenAI strict mode and Anthropic
    assert "$defs" in text


def test_llm_schema_strips_diagram_length_constraints():
    # llm_schema() strips minLength/maxLength/pattern from every string
    # property; diagram label length limits are therefore lint-only (see
    # additions to the lint tests), never a Pydantic field constraint here.
    schema = llm_schema()
    text = json.dumps(schema)
    assert "DiagramNode" in text
    assert "minLength" not in text
    assert "maxLength" not in text


# -------------------------------------------------------------- diagram_hash


def test_diagram_hash_is_twelve_hex_chars():
    d = Diagram(nodes=[DiagramNode(id="a", label="A")])
    h = diagram_hash(d)
    assert len(h) == 12
    assert all(c in "0123456789abcdef" for c in h)


def test_diagram_hash_deterministic_across_repeated_calls():
    d = Diagram(nodes=[DiagramNode(id="a", label="A")])
    assert diagram_hash(d) == diagram_hash(d)


def test_diagram_hash_stable_regardless_of_field_construction_order():
    # kwargs order (and, equivalently, dict key order fed to model_validate)
    # must not affect the hash: model_dump_json serializes in declared field
    # order, not input order, which is what makes the hash a stable stamp.
    a = Diagram(
        title="T", direction="LR", id="x",
        nodes=[DiagramNode(id="a", label="A", type="service", sublabel=None)],
    )
    b = Diagram.model_validate({
        "nodes": [{"sublabel": None, "type": "service", "label": "A", "id": "a"}],
        "id": "x", "direction": "LR", "title": "T",
    })
    assert diagram_hash(a) == diagram_hash(b)


def test_diagram_hash_changes_when_content_changes():
    base = Diagram(nodes=[DiagramNode(id="a", label="A")])
    changed = Diagram(nodes=[DiagramNode(id="a", label="A renamed")])
    assert diagram_hash(base) != diagram_hash(changed)


def test_diagram_hash_differs_for_all_five_bakeoff_specs():
    hashes = {diagram_hash(_spec_to_diagram(s)) for s in BAKEOFF_SPECS}
    assert len(hashes) == 5


# --------------------------------------------------- coordinate-free (ir.py:208)


def test_diagram_models_carry_no_geometry_fields():
    # Layout intent, not geometry: none of archify's hand-placement knobs may
    # ever appear on these models (docs/diagram-plan.md section 2).
    forbidden = {
        "row", "col", "pos", "size", "x", "y", "w", "h", "width", "height",
        "fromSide", "toSide", "route", "via", "labelAt", "labelDx", "labelDy",
        "labelSegment",
    }
    for model in (DiagramNode, DiagramEdge, DiagramGroup, Diagram):
        assert not (set(model.model_fields) & forbidden), model.__name__


def test_diagram_group_membership_lives_on_the_node_not_the_group():
    # archify's wraps[] (group -> members) is rejected in favor of node.group
    # (node -> group): a node in two groups becomes unrepresentable.
    assert "group" in DiagramNode.model_fields
    assert "wraps" not in DiagramGroup.model_fields
    assert "members" not in DiagramGroup.model_fields
