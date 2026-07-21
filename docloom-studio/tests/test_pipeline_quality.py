"""Hardening pass on generate/providers/jobs/ingest/embeddings/crypto:

- ingest.fetch_youtube calls the youtube-transcript-api 1.x instance API
- one failing slide/section (ProviderError, a raw httpx error, not just
  GenerationFailed) does not sink the whole deck/doc job, and a truncated
  single-call sheet generation splits into outline + per-sheet calls
- artifact status transitions: 'building' at creation, 'ready' on save,
  'failed' on a job exception or cancellation
- ingest's SSRF guard rejects loopback/private/link-local addresses and
  non-http(s) schemes, and re-checks redirect hops
- providers.complete() detects a truncated response (Ollama done_reason,
  OpenAI finish_reason, Anthropic stop_reason) instead of wasting retries
  on a misleading parse error
- generate._looks_like_d2 rejects Mermaid syntax that a bare "->"/"--"
  substring check would otherwise let through

A few directly-adjacent fixes (openai param shape, the ollama mid-stream
error, the coverage-floor depth guarantee, crypto's fail-loud key loading)
are covered alongside the six areas above since the same fixtures apply.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-plq-"))

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from docloom import Slide  # noqa: E402
from docloom_studio import embeddings as E  # noqa: E402
from docloom_studio import generate as gen  # noqa: E402
from docloom_studio import ingest  # noqa: E402
from docloom_studio import providers as P  # noqa: E402
from docloom_studio.db import (  # noqa: E402
    execute, init_db, new_id, now, query_one,
)
from docloom_studio.generate import DocOutline, DocOutlineItem, DocSection  # noqa: E402
from docloom_studio.generate import Outline, OutlineItem  # noqa: E402
from docloom_studio.providers import ProviderConfig, ProviderError  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()


def _notebook() -> str:
    """A notebook owned by a fresh user's workspace (route auth scoping needs it)."""
    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, f"{uid}@t.local", "x", now()))
    wid = new_id()
    execute("INSERT INTO workspaces (id, user_id, name, created) VALUES (?, ?, ?, ?)",
            (wid, uid, "w", now()))
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, workspace_id, created, updated) "
            "VALUES (?, ?, ?, ?, ?)", (nb, "nb", wid, now(), now()))
    return nb


class FakeCtx:
    def __init__(self):
        self.events = []

    def emit(self, stage, status="running", detail="", data=None):
        self.events.append((stage, status, detail, data))


_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatching


def _mock_client(handler):
    """Route providers.py's `httpx.AsyncClient(...)` through a MockTransport
    so complete()/stream_text() can be driven without a real provider.

    providers.py's `httpx` and this file's `httpx` are the same module
    object (sys.modules is a singleton), so monkeypatching `P.httpx.AsyncClient`
    also rebinds this file's `httpx.AsyncClient` -- the factory must close
    over the real class captured above, not look it up again by name, or
    calling it recurses into itself."""
    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return _RealAsyncClient(*args, **kwargs)
    return factory


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# =========================================================== 1. YouTube API


class _FakeSnippet:
    def __init__(self, text: str):
        self.text = text


class _FakeYTApi1x:
    """Mirrors ONLY the youtube-transcript-api 1.x shape: an instance with a
    .fetch(video_id) method returning objects with a .text attribute.
    Deliberately has no get_transcript staticmethod -- if ingest.py still
    called the 0.x API removed in 1.0, this raises AttributeError instead of
    silently doing the wrong thing."""

    def __init__(self):
        pass

    def fetch(self, video_id):
        assert video_id == "dQw4w9WgXcQ"
        return [_FakeSnippet("Hello"), _FakeSnippet(" world"), _FakeSnippet("  ")]


def test_fetch_youtube_uses_1x_instance_fetch_api(monkeypatch):
    import youtube_transcript_api as yta

    monkeypatch.setattr(yta, "YouTubeTranscriptApi", _FakeYTApi1x)

    class _RaisingClient:  # neutralize the secondary <title> page fetch
        def __init__(self, *a, **k):
            raise RuntimeError("no network in tests")

    monkeypatch.setattr(ingest.httpx, "Client", _RaisingClient)

    title, text = ingest.fetch_youtube(
        "https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ")
    assert text == "Hello world"  # each snippet stripped, blank one dropped
    assert title == "YouTube dQw4w9WgXcQ"  # title fetch failed -> id fallback


def test_fetch_youtube_raises_on_empty_transcript(monkeypatch):
    import youtube_transcript_api as yta

    class _EmptyApi:
        def fetch(self, video_id):
            return [_FakeSnippet("   ")]

    monkeypatch.setattr(yta, "YouTubeTranscriptApi", _EmptyApi)
    with pytest.raises(ValueError):
        ingest.fetch_youtube("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ")


# ============================================ 2. per-unit failure isolation


def test_deck_pipeline_survives_provider_and_httpx_errors_on_some_slides(monkeypatch):
    """Two of three slides fail with the exact exception types the old
    `except GenerationFailed:` could not catch; the deck must still ship
    with all three (two skeletons + one real) instead of the whole job dying."""
    outline = Outline(deck_title="Resilience", slides=[
        OutlineItem(title="One", layout="content", intent="a"),
        OutlineItem(title="Two", layout="content", intent="b"),
        OutlineItem(title="Three", layout="content", intent="c"),
    ])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        user = messages[-1]["content"]
        if "Draft slide" not in user:
            return outline
        if '"Two"' in user:
            raise ProviderError("provider returned HTTP 503: overloaded")
        if '"Three"' in user:
            raise httpx.ReadTimeout("timed out")
        return Slide(layout="content", title="One",
                     blocks=[{"type": "paragraph", "text": "ok"}])

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    nb = _notebook()
    aid = gen.create_artifact(nb, "deck")
    ctx = FakeCtx()
    asyncio.run(gen.run_deck_pipeline(ctx, nb, aid, "resilience test"))

    payload = json.loads(query_one(
        "SELECT payload_json FROM artifacts WHERE id = ?", (aid,))["payload_json"])
    titles = [s["title"] for s in payload["ir"]["slides"]]
    # title slide + all 3 outline slides, none dropped
    assert titles == ["Resilience", "One", "Two", "Three"]
    skipped = [e for e in ctx.events if e[0] == "slide" and e[1] == "skipped"]
    assert len(skipped) == 2
    assert {e[2] for e in skipped} == {"Two", "Three"}
    # the job itself completed (was never allowed to raise out of the pipeline)
    save = [e for e in ctx.events if e[0] == "save" and e[1] == "done"]
    assert save


def test_sheet_pipeline_splits_on_truncation(monkeypatch):
    """A single-call SheetDoc generation that hits the token cap (bug #19)
    must fall back to outline + per-sheet calls, not fail 3 retries against
    the same cap and produce nothing; a per-sheet failure inside that
    fallback still only skips the one sheet."""
    from docloom import Column, Sheet

    from docloom_studio.generate import SheetOutline, SheetOutlineItem

    outline = SheetOutline(title="Big Workbook", sheets=[
        SheetOutlineItem(name="Q1", intent="quarter one numbers"),
        SheetOutlineItem(name="Q2", intent="quarter two numbers"),
    ])
    sheet_q1 = Sheet(name="Q1", columns=[Column(header="Metric"), Column(header="Value")],
                     rows=[["Revenue", 1000]])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        sys = messages[0]["content"]
        user = messages[-1]["content"]
        if "You produce spreadsheets" in sys:  # the single whole-workbook call
            raise P.TruncatedOutput("workbook too large for one call")
        if "You plan spreadsheet workbooks" in sys:
            return outline
        assert "You produce ONE sheet" in sys  # a per-sheet call in the split path
        if '"Q1"' in user:
            return sheet_q1
        raise ProviderError("Q2 generation failed")

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    nb = _notebook()
    aid = gen.create_artifact(nb, "sheet")
    ctx = FakeCtx()
    asyncio.run(gen.run_sheet_pipeline(ctx, nb, aid, "big workbook"))

    payload = json.loads(query_one(
        "SELECT payload_json FROM artifacts WHERE id = ?", (aid,))["payload_json"])
    sheets = payload["ir"]["sheets"]
    assert [s["name"] for s in sheets] == ["Q1", "Q2"]
    assert len(sheets[0]["rows"]) == 1  # Q1: the real generated sheet
    assert sheets[1]["rows"] == []  # Q2: fell back to an empty skeleton sheet
    skipped = [e for e in ctx.events if e[0] == "sheet" and e[1] == "skipped"]
    assert len(skipped) == 1 and skipped[0][2] == "Q2"


def test_doc_pipeline_survives_provider_error_on_one_section(monkeypatch):
    outline = DocOutline(doc_title="Report", sections=[
        DocOutlineItem(heading="Intro", intent="context"),
        DocOutlineItem(heading="Breaks", intent="fails"),
    ])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        if "Section:" not in messages[-1]["content"]:
            return outline
        if '"Breaks"' in messages[-1]["content"]:
            raise ProviderError("provider returned HTTP 500")
        return DocSection(blocks=[{"type": "paragraph", "text": "Body."}])

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    nb = _notebook()
    aid = gen.create_artifact(nb, "doc")
    ctx = FakeCtx()
    asyncio.run(gen.run_doc_pipeline(ctx, nb, aid, "report prompt"))

    payload = json.loads(query_one(
        "SELECT payload_json FROM artifacts WHERE id = ?", (aid,))["payload_json"])
    headings = [b["text"] for b in payload["ir"]["blocks"] if b["type"] == "heading"]
    assert headings == ["Intro", "Breaks"]  # both sections present
    skipped = [e for e in ctx.events if e[0] == "section" and e[1] == "skipped"]
    assert len(skipped) == 1 and skipped[0][2] == "Breaks"


# =============================================== 3. artifact status states


def test_create_artifact_is_building_then_ready_after_save():
    nb = _notebook()
    aid = gen.create_artifact(nb, "deck")
    assert query_one("SELECT status FROM artifacts WHERE id = ?", (aid,))["status"] == "building"

    gen.save_artifact(aid, "Title", {"ir": {"title": "Title"}, "theme_name": "paper"})
    assert query_one("SELECT status FROM artifacts WHERE id = ?", (aid,))["status"] == "ready"


def test_job_exception_marks_its_artifact_failed():
    from docloom_studio.jobs import JOBS, start_job

    nb = _notebook()
    aid = gen.create_artifact(nb, "deck")

    async def work(ctx):
        raise RuntimeError("boom")

    async def run():
        jid = start_job("generate:deck", work, notebook_id=nb, artifact_id=aid)
        await JOBS[jid].task
        return jid

    asyncio.run(run())
    assert query_one("SELECT status FROM artifacts WHERE id = ?", (aid,))["status"] == "failed"


def test_job_cancellation_marks_its_artifact_failed():
    from docloom_studio.jobs import JOBS, cancel_job, start_job

    nb = _notebook()
    aid = gen.create_artifact(nb, "deck")

    async def work(ctx):
        await asyncio.sleep(10)

    async def run():
        jid = start_job("generate:deck", work, notebook_id=nb, artifact_id=aid)
        await asyncio.sleep(0)  # let the task start and reach the sleep
        cancel_job(jid)
        try:
            await JOBS[jid].task
        except asyncio.CancelledError:
            pass
        return jid

    asyncio.run(run())
    assert query_one("SELECT status FROM artifacts WHERE id = ?", (aid,))["status"] == "failed"


def test_job_without_artifact_id_does_not_crash_on_failure():
    """Non-artifact jobs (ingestion, research) pass artifact_id=None; the
    failure-handling hook must be a no-op for them, not raise."""
    from docloom_studio.jobs import JOBS, start_job

    async def work(ctx):
        raise RuntimeError("boom")

    async def run():
        jid = start_job("ingest", work)
        await JOBS[jid].task
        return jid

    jid = asyncio.run(run())
    assert query_one("SELECT status FROM jobs WHERE id = ?", (jid,))["status"] == "failed"


# ============================================================ 4. SSRF guard


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://localhost/admin",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
    "http://10.0.0.5/internal",
    "http://172.16.0.1/internal",
    "http://192.168.1.1/internal",
    "http://[::1]/admin",
])
def test_ssrf_guard_rejects_non_public_addresses(url):
    with pytest.raises(ValueError):
        ingest._guard_url(url)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com/x",
])
def test_ssrf_guard_rejects_non_http_schemes(url):
    with pytest.raises(ValueError):
        ingest._guard_url(url)


def test_ssrf_guard_allows_public_address():
    ingest._guard_url("http://8.8.8.8/")  # must not raise


def test_ssrf_guard_rejects_unresolvable_host():
    with pytest.raises(ValueError):
        ingest._guard_url("http://this-host-does-not-exist.invalid/")


def test_fetch_url_revalidates_redirect_target(monkeypatch):
    """A URL that looks public but 302s to a private/metadata address must be
    refused on the redirect hop, not silently followed (the bug this SSRF
    guard exists for: httpx's own follow_redirects=True never re-validates)."""

    class _RedirectResponse:
        status_code = 302
        headers = {"location": "http://169.254.169.254/latest/meta-data/"}
        has_redirect_location = True
        extensions: dict = {}

        def raise_for_status(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url):
            return _RedirectResponse()

    monkeypatch.setattr(ingest.httpx, "Client", _FakeClient)
    with pytest.raises(ValueError):
        ingest.fetch_url("http://example.com/redirector")


# =================================================== 5. truncation detection


def test_complete_detects_ollama_truncation(monkeypatch):
    def handler(request):
        return _json_response({"message": {"content": '{"a": 1'},
                               "done": True, "done_reason": "length"})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="ollama", base_url="http://fake", model="m")
    with pytest.raises(P.TruncatedOutput):
        asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}]))


def test_complete_detects_openai_truncation(monkeypatch):
    def handler(request):
        return _json_response({"choices": [
            {"message": {"content": "trunc"}, "finish_reason": "length"}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="openai", base_url="http://fake", model="gpt-5", api_key="x")
    with pytest.raises(P.TruncatedOutput):
        asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}]))


def test_complete_detects_anthropic_truncation(monkeypatch):
    def handler(request):
        return _json_response({"stop_reason": "max_tokens",
                               "content": [{"type": "text", "text": "trunc"}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="anthropic", base_url="https://fake", model="claude", api_key="x")
    with pytest.raises(P.TruncatedOutput):
        asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}]))


def test_complete_openai_sends_max_completion_tokens_and_no_temperature(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _json_response({"choices": [
            {"message": {"content": "ok"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="openai", base_url="http://fake", model="gpt-5", api_key="x")
    asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}]))
    assert seen["body"]["max_completion_tokens"] == P.DEFAULT_MAX_TOKENS
    assert "max_tokens" not in seen["body"]
    assert "temperature" not in seen["body"]


def test_complete_llamaserver_still_sends_max_tokens_and_temperature(monkeypatch):
    """Only the `openai` preset changes; llama-server/lmstudio keep the
    older, widely-supported shape."""
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _json_response({"choices": [
            {"message": {"content": "ok"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="llama-server", base_url="http://fake", model="m")
    asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}]))
    assert seen["body"]["max_tokens"] == P.DEFAULT_MAX_TOKENS
    assert "temperature" in seen["body"]


def test_ollama_stream_surfaces_mid_stream_error(monkeypatch):
    """A 200-status NDJSON stream that switches to an {"error": ...} line
    (the model runner crashing mid-generation) must not be silently
    swallowed as a truncated-but-successful answer."""
    lines = [
        json.dumps({"message": {"content": "partial "}, "done": False}),
        json.dumps({"error": "model runner crashed"}),
    ]
    body = ("\n".join(lines) + "\n").encode()

    def handler(request):
        return httpx.Response(200, content=body)

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="ollama", base_url="http://fake", model="m")

    async def drive():
        parts = []
        async for piece in P.stream_text(cfg, [{"role": "user", "content": "hi"}]):
            parts.append(piece)
        return parts

    with pytest.raises(P.ProviderError):
        asyncio.run(drive())


def test_raise_for_status_reads_body_on_a_streaming_response(monkeypatch):
    """_raise_for_status must not lose the error body for a streamed response
    (the sync r.read() this replaced raises on an async-stream Response)."""

    def handler(request):
        return httpx.Response(500, content=b'{"error": "overloaded"}')

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="ollama", base_url="http://fake", model="m")

    async def drive():
        async for _ in P.stream_text(cfg, [{"role": "user", "content": "hi"}]):
            pass

    with pytest.raises(P.ProviderError) as exc:
        asyncio.run(drive())
    assert "overloaded" in str(exc.value)


# ============================================================ 6. D2/Mermaid


@pytest.mark.parametrize("src", [
    "flowchart TD\nA --> B",
    "graph LR\nA --> B",
    "A --> B",                       # bare mermaid arrow, no prefix line
    "a[Start] -> b[End]",            # mermaid node-bracket syntax
    "sequenceDiagram\nA->>B: hi",
])
def test_looks_like_d2_rejects_mermaid(src):
    assert gen._looks_like_d2(src), f"expected {src!r} to be rejected"


def test_looks_like_d2_accepts_valid_d2():
    src = (
        "direction: right\n"
        "user: User { shape: person }\n"
        "api: API service\n"
        "db: Store { shape: cylinder }\n"
        "user -> api\n"
        "api -> db\n"
    )
    assert gen._looks_like_d2(src) == []


def test_looks_like_d2_rejects_empty_and_unbalanced():
    assert gen._looks_like_d2("")
    assert gen._looks_like_d2("a -> b { shape: cylinder")  # unbalanced brace


# ============================================================= bonus: extras


def test_coverage_floor_gives_best_source_more_than_one_chunk(monkeypatch):
    """Once a notebook has >= k sources, retrieval must not collapse to
    exactly one chunk per source: the best-scoring source keeps some depth."""
    from docloom_studio.ingest import _source_dir

    nb = _notebook()
    v = np.array([[1.0, 0.0]], dtype=np.float32)

    def add_source(title: str, chunks: list[str]) -> str:
        sid = new_id()
        execute("INSERT INTO sources (id, notebook_id, kind, title, status, "
                "context_mode, created) VALUES (?, ?, 'text', ?, 'ready', 'full', ?)",
                (sid, nb, title, now()))
        d = _source_dir(sid)
        (d / "chunks.jsonl").write_text(
            "\n".join(json.dumps({"text": t, "chunk_ix": i, "section": ""})
                      for i, t in enumerate(chunks)), encoding="utf-8")
        np.save(d / "embeddings.npy", np.vstack([v] * len(chunks)))
        return sid

    best = add_source("Best", [
        "async standups reduce interruptions for engineering teams",
        "async standups let engineers reply on their own schedule",
        "async standups improve focus time for engineering teams",
    ])
    for i in range(4):  # pad past k with single-chunk, lower-relevance sources
        add_source(f"Other{i}", ["remote work has many other unrelated facets"])

    async def fake_embed(cfg, texts):
        return np.array([[1.0, 0.0]] * len(texts), dtype=np.float32)

    monkeypatch.setattr(E, "embed", fake_embed)
    out = asyncio.run(E.retrieve(nb, "async standups engineering teams", k=4))
    from_best = [r for r in out if r.source_id == best]
    assert len(from_best) >= 2, "best source should contribute more than one chunk"


def test_embed_source_batches_large_inputs(monkeypatch):
    """A source with more chunks than the batch size must issue multiple
    embed() calls, not one oversized request."""
    calls = []

    async def fake_embed(cfg, texts):
        calls.append(len(texts))
        return np.zeros((len(texts), 3), dtype=np.float32)

    monkeypatch.setattr(E, "embed", fake_embed)
    nb = _notebook()
    sid = new_id()
    execute("INSERT INTO sources (id, notebook_id, kind, title, status, "
            "context_mode, created) VALUES (?, ?, 'text', ?, 'ready', 'full', ?)",
            (sid, nb, "Big", now()))
    texts = [f"chunk {i}" for i in range(E._EMBED_BATCH * 2 + 5)]
    asyncio.run(E.embed_source(sid, texts))
    assert len(calls) == 3  # 64 + 64 + 5
    assert sum(calls) == len(texts)


def test_crypto_fails_loudly_on_malformed_secret_key(monkeypatch):
    from docloom_studio import crypto

    monkeypatch.setenv("DOCLOOM_SECRET_KEY", "not-a-valid-fernet-key")
    crypto._loaded = False
    crypto._fernet = None
    try:
        with pytest.raises(Exception):
            crypto.encrypt("sk-secret")
        # must fail loudly again on a second call, not silently downgrade to
        # plaintext once _loaded quietly flips true
        with pytest.raises(Exception):
            crypto.encrypt("sk-secret-2")
    finally:
        crypto._loaded = False
        crypto._fernet = None


def test_sse_heartbeat_keeps_an_idle_connection_alive(monkeypatch):
    from docloom_studio import jobs as J

    monkeypatch.setattr(J, "_SSE_KEEPALIVE_SECONDS", 0.05)
    job = J.Job(id=new_id(), kind="test", status="running")
    J.JOBS[job.id] = job

    async def drive():
        gen_ = J.sse_events(job.id)
        first = await asyncio.wait_for(gen_.__anext__(), timeout=2.0)
        assert first.startswith(":")  # SSE comment, no real event yet
        job.queues[0].put_nowait({"stage": "x", "status": "done", "detail": "",
                                  "data": None, "t": 0})
        second = await asyncio.wait_for(gen_.__anext__(), timeout=2.0)
        assert '"stage": "x"' in second
        job.queues[0].put_nowait(J._SENTINEL)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen_.__anext__(), timeout=2.0)

    try:
        asyncio.run(drive())
    finally:
        J.JOBS.pop(job.id, None)
