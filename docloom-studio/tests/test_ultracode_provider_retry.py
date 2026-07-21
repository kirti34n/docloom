"""Regression tests for the Gemini/HTTP 429 rate-limit retry fix in
providers.py.

CONFIRMED BUG (live reproduction): Gemini's free tier returns HTTP 429
"RESOURCE_EXHAUSTED" after a few calls within a minute -- a per-minute quota
that recovers on its own within about 60s. The old code treated 429 (and
503) as a permanent failure (_raise_for_status raised a bare ProviderError),
so a rate-limited slide call failed outright and the deck pipeline shipped
an empty "(generation failed)" skeleton slide instead of retrying into the
quota reset.

The fix: `RateLimited(ProviderError)` distinguishes 429/503 from every other
status, and `_post_with_retry()` wraps the POST used by complete()/embed()
with a bounded retry loop that sleeps (using the provider's own stated delay
when present, else exponential backoff) and re-issues the request instead of
raising immediately.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-ultracode-retry-"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from docloom_studio import providers as P  # noqa: E402

_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatching


def _mock_client(handler):
    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return _RealAsyncClient(*args, **kwargs)
    return factory


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _no_sleep(monkeypatch):
    """Record every requested sleep duration without actually waiting, so
    the retry tests run instantly instead of burning real wall-clock time."""
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(P.asyncio, "sleep", fake_sleep)
    return calls


_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}},
           "required": ["title"]}

_GEMINI_OK_BODY = {"candidates": [
    {"content": {"parts": [{"text": '{"title":"ok"}'}]}, "finishReason": "STOP"}]}


# ============================================================ core behavior


def test_429_then_200_returns_success_not_error(monkeypatch):
    """The exact scenario from the bug report: a rate-limited call followed
    by a normal success must produce ONE successful return, not a raised
    error."""
    sleeps = _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return _json_response({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}, 429)
        return _json_response(_GEMINI_OK_BODY)

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="gemini", model="gemini-2.5-flash", api_key="x")
    out = asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))

    assert out == '{"title":"ok"}'
    assert calls["n"] == 2
    assert len(sleeps) == 1  # exactly one wait, before the successful retry


def test_gemini_retry_delay_parsed_from_error_body(monkeypatch):
    """Gemini's RetryInfo.retryDelay (e.g. "37s") sizes the wait when
    present, instead of falling back to the default exponential schedule."""
    sleeps = _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return _json_response({
                "error": {
                    "code": 429, "status": "RESOURCE_EXHAUSTED",
                    "details": [{
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "37s",
                    }],
                }
            }, 429)
        return _json_response(_GEMINI_OK_BODY)

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="gemini", model="gemini-2.5-flash", api_key="x")
    out = asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))

    assert out == '{"title":"ok"}'
    assert sleeps == [37.0]


def test_retry_after_header_used_for_openai_compatible_429(monkeypatch):
    calls = {"n": 0}
    sleeps = _no_sleep(monkeypatch)

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "5"}, json={"error": "slow down"})
        return _json_response({"choices": [
            {"finish_reason": "stop", "message": {"content": '{"title":"ok"}'}}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="openai", model="gpt-x", api_key="k",
                           base_url="https://x.example")
    out = asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))

    assert out == '{"title":"ok"}'
    assert sleeps == [5.0]


def test_503_is_also_retried(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return _json_response({"error": "temporarily unavailable"}, 503)
        return _json_response(_GEMINI_OK_BODY)

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="gemini", model="gemini-2.5-flash", api_key="x")
    out = asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))

    assert out == '{"title":"ok"}'
    assert len(sleeps) == 1


# ==================================================================== bounds


def test_backoff_schedule_is_exponential_and_bounded_without_provider_hint(monkeypatch):
    """No Retry-After / retryDelay anywhere -- the fallback schedule (2s, 8s,
    30s, holding at 30s) is used, and no single sleep ever exceeds the
    _MAX_BACKOFF_SECONDS cap."""
    sleeps = _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 4:
            return _json_response({"error": "rate limited"}, 429)
        return _json_response(_GEMINI_OK_BODY)

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="gemini", model="gemini-2.5-flash", api_key="x")
    out = asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))

    assert out == '{"title":"ok"}'
    assert sleeps == [2.0, 8.0, 30.0]
    assert all(s <= P._MAX_BACKOFF_SECONDS for s in sleeps)


def test_exhausting_all_attempts_raises_rate_limited(monkeypatch):
    """A rate limit that never clears within the attempt budget must still
    surface as an error (RateLimited, a ProviderError) rather than retrying
    forever or silently succeeding."""
    sleeps = _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return _json_response({"error": "rate limited"}, 429)

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="gemini", model="gemini-2.5-flash", api_key="x")
    with pytest.raises(P.RateLimited):
        asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))

    assert calls["n"] == P._MAX_POST_ATTEMPTS
    assert len(sleeps) == P._MAX_POST_ATTEMPTS - 1  # no sleep after the final failed attempt


def test_non_retryable_status_raises_immediately_no_sleep(monkeypatch):
    """Behavior must be unchanged for a real error (a plain 400): no retry,
    no sleep, one call, ProviderError raised straight through."""
    sleeps = _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return _json_response({"error": "bad request"}, 400)

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="gemini", model="gemini-2.5-flash", api_key="x")
    with pytest.raises(P.ProviderError):
        asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))

    assert calls["n"] == 1
    assert sleeps == []


def test_rate_limited_is_a_provider_error_subclass():
    assert issubclass(P.RateLimited, P.ProviderError)


# ==================================================================== embed()


def test_embed_retries_through_429(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return _json_response({"error": "rate limited"}, 429)
        return _json_response({"embeddings": [{"values": [0.1, 0.2]}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="gemini", model="text-embedding-004", api_key="x")
    out = asyncio.run(P.embed(cfg, ["hello"]))

    assert out.shape == (1, 2)
    assert len(sleeps) == 1
