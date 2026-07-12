"""Free web research: plan -> search (ddgs) -> fetch -> extract -> cited
sources. Results become ordinary notebook sources, so they get chunked,
embedded, and toggled exactly like uploads. No API key required.
Fetched pages are data, never instructions."""

from __future__ import annotations

import asyncio
import itertools
import json

from docloom import llm_schema, parse_llm_output
from pydantic import BaseModel, Field

from .db import execute, new_id, now, owner_of_notebook
from .ingest import fetch_url, ingest_source
from .jobs import JobCtx
from .providers import ProviderConfig, generate_validated
from .settings import get_setting

MAX_PAGES = 16
PER_QUERY = 5
FETCH_CONCURRENCY = 4
MIN_TEXT = 400


class ResearchPlan(BaseModel):
    queries: list[str] = Field(description="3-6 focused web search queries")


PLAN_SYSTEM = """\
You plan web research. Given a topic, return JSON with 3-6 focused search
queries that together cover it well — distinct angles, not rephrasings.
Each query is what you would type into a search engine."""


async def _search(queries: list[str]) -> list[dict]:
    """Run ddgs for each query (concurrently, in threads); dedupe by URL.

    Round-robins across queries instead of exhausting each in turn: appending
    query 1's results, then query 2's, ... and only THEN truncating to
    MAX_PAGES let the first few queries fill the whole cap, so a plan's later,
    distinct-angle queries (the reason a plan asks for several) never
    contributed anything at all."""
    from ddgs import DDGS

    def one(q: str) -> list[dict]:
        try:
            return DDGS().text(q, max_results=PER_QUERY)
        except Exception:
            return []

    per_query = await asyncio.gather(*(asyncio.to_thread(one, q) for q in queries))
    interleaved = (r for round_results in itertools.zip_longest(*per_query)
                  for r in round_results if r is not None)

    seen: set[str] = set()
    hits: list[dict] = []
    for r in interleaved:
        if len(hits) >= MAX_PAGES:
            break
        url = r.get("href") or r.get("url") or ""
        if url and url not in seen:
            seen.add(url)
            hits.append({"url": url, "title": r.get("title", url)[:200]})
    return hits


async def run_research(ctx: JobCtx, notebook_id: str, query: str) -> None:
    # provider config is per-user (Settings saves it under the caller's
    # user_id); without the owner, research always ran on the global
    # default and ignored whatever the user configured
    owner = owner_of_notebook(notebook_id)
    cfg = ProviderConfig(**get_setting("provider.generation", owner))

    ctx.emit("plan", "running")
    plan: ResearchPlan = await generate_validated(
        cfg,
        [{"role": "system", "content": PLAN_SYSTEM},
         {"role": "user", "content": f"Topic: {query}"}],
        schema=llm_schema(ResearchPlan),
        parse=lambda t: parse_llm_output(t, ResearchPlan),
        lint_fn=lambda p: ([] if 1 <= len(p.queries) <= 8 else ["1-8 queries"]),
    )
    ctx.emit("plan", "done", data={"queries": plan.queries})

    ctx.emit("search", "running")
    hits = await _search(plan.queries or [query])
    ctx.emit("search", "done", detail=f"{len(hits)} results")

    # fetch + extract concurrently
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    kept: list[tuple[str, str, str]] = []  # (url, title, text)

    async def grab(hit: dict) -> None:
        async with sem:
            try:
                title, text = await asyncio.to_thread(fetch_url, hit["url"])
            except Exception:
                return
            if text and len(text) >= MIN_TEXT:
                kept.append((hit["url"], title or hit["title"], text))
                ctx.emit("read", "done", detail=title[:60])

    ctx.emit("read", "running", detail=f"reading {len(hits)} pages")
    await asyncio.gather(*(grab(h) for h in hits))

    if not kept:
        ctx.emit("research", "failed", detail="no readable pages found")
        return

    # save each as a research source and ingest (chunk + embed)
    ctx.emit("ingest", "running", detail=f"{len(kept)} sources")
    for url, title, text in kept:
        sid = new_id()
        execute(
            "INSERT INTO sources (id, notebook_id, kind, title, url, status, "
            "context_mode, meta_json, created) VALUES (?, ?, 'research', ?, ?, "
            "'pending', 'full', ?, ?)",
            (sid, notebook_id, title[:200], url,
             json.dumps({"text": text, "query": query}), now()),
        )
        await ingest_source(sid)
    ctx.emit("ingest", "done", detail=f"{len(kept)} cited sources added")
    ctx.emit("save", "done", data={"sources": len(kept)})
