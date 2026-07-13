"""Stage A regression: the native "gemini" provider kind in providers.py.

Covers:
- C1: ProviderConfig grows max_tokens/thinking with a backward-compatible
  default, and generate_validated() threads cfg.max_tokens into complete().
- C2: the gemini branches of complete()/stream_text()/embed()/list_models(),
  inserted before the OpenAI-compatible fall-through in each. The request
  body is built by a pure helper (_gemini_request_body) so its shape (URL
  path segments aside) is asserted directly; URL/header/wire-format details
  are then exercised end-to-end through a mocked httpx transport, mirroring
  the _mock_client/_json_response pattern already used against providers.py
  in test_pipeline_quality.py (duplicated here, not imported, to avoid any
  collision with that file).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-stageA-providers-"))

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from docloom_studio import providers as P  # noqa: E402
from docloom_studio.providers import ProviderConfig  # noqa: E402

# A schema shaped like docloom's llm_schema() output: $defs + $ref (nested
# model) and an anyOf null-union (Optional field). responseJsonSchema must
# accept it verbatim, unlike the older responseSchema field.
_SAMPLE_SCHEMA = {
    "$defs": {"Kind": {"enum": ["a", "b"], "type": "string"}},
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "kind": {"$ref": "#/$defs/Kind"},
        "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["title"],
}


# =========================================== ProviderConfig defaults (C1)


def test_provider_config_defaults_max_tokens_and_thinking():
    cfg = ProviderConfig()
    assert cfg.max_tokens == 8192
    assert cfg.thinking == "auto"


def test_provider_config_accepts_gemini_kind_and_overrides():
    cfg = ProviderConfig(kind="gemini", model="gemini-2.5-flash", max_tokens=2048, thinking="on")
    assert cfg.kind == "gemini"
    assert cfg.max_tokens == 2048
    assert cfg.thinking == "on"


# ============================================ _gemini_request_body (C2)


def test_request_body_flash_gets_thinking_budget_zero_and_schema_passthrough():
    cfg = ProviderConfig(kind="gemini", model="gemini-2.5-flash", max_tokens=4096)
    body = P._gemini_request_body(cfg, [{"role": "user", "content": "hi"}],
                                   _SAMPLE_SCHEMA, 0.4, cfg.max_tokens)
    gc = body["generationConfig"]
    assert gc["thinkingConfig"] == {"thinkingBudget": 0}
    assert gc["maxOutputTokens"] == cfg.max_tokens
    assert gc["responseMimeType"] == "application/json"
    # passed straight through: $defs/$ref/anyOf untouched, no transform applied
    assert gc["responseJsonSchema"] == _SAMPLE_SCHEMA


def test_request_body_pro_omits_thinking_budget():
    cfg = ProviderConfig(kind="gemini", model="gemini-2.5-pro", max_tokens=4096)
    body = P._gemini_request_body(cfg, [{"role": "user", "content": "hi"}],
                                   _SAMPLE_SCHEMA, 0.4, cfg.max_tokens)
    assert "thinkingConfig" not in body["generationConfig"]
    assert body["generationConfig"]["maxOutputTokens"] == 4096


def test_request_body_gemini3_omits_thinking_budget_even_with_flash_like_id():
    cfg = ProviderConfig(kind="gemini", model="gemini-3-pro-image", max_tokens=2048)
    body = P._gemini_request_body(cfg, [{"role": "user", "content": "hi"}],
                                   None, 0.4, cfg.max_tokens)
    assert "thinkingConfig" not in body["generationConfig"]
    assert body["generationConfig"]["maxOutputTokens"] == 2048


def test_request_body_thinking_on_always_omits_config_even_for_flash():
    cfg = ProviderConfig(kind="gemini", model="gemini-2.5-flash", thinking="on")
    body = P._gemini_request_body(cfg, [{"role": "user", "content": "hi"}], None, 0.4, 8192)
    assert "thinkingConfig" not in body["generationConfig"]


def test_request_body_thinking_off_still_zeros_budget_for_flash():
    # "off" and "auto" both apply the model-conditional rule; only "on" forces
    # thinkingConfig to be omitted outright (see the ProviderConfig.thinking
    # docstring in providers.py).
    cfg = ProviderConfig(kind="gemini", model="gemini-2.5-flash", thinking="off")
    body = P._gemini_request_body(cfg, [{"role": "user", "content": "hi"}], None, 0.4, 8192)
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


def test_request_body_no_schema_omits_json_mode():
    cfg = ProviderConfig(kind="gemini", model="gemini-2.5-flash")
    body = P._gemini_request_body(cfg, [{"role": "user", "content": "hi"}], None, 0.4, 8192)
    gc = body["generationConfig"]
    assert "responseMimeType" not in gc
    assert "responseJsonSchema" not in gc


def test_request_body_maps_roles_and_splits_system_instruction():
    cfg = ProviderConfig(kind="gemini", model="gemini-2.5-flash")
    messages = [
        {"role": "system", "content": "You are docloom."},
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2"},
    ]
    body = P._gemini_request_body(cfg, messages, None, 0.4, 8192)
    assert body["systemInstruction"] == {"parts": [{"text": "You are docloom."}]}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "Q1"}]},
        {"role": "model", "parts": [{"text": "A1"}]},
        {"role": "user", "parts": [{"text": "Q2"}]},
    ]


def test_request_body_omits_system_instruction_when_no_system_message():
    cfg = ProviderConfig(kind="gemini", model="gemini-2.5-flash")
    body = P._gemini_request_body(cfg, [{"role": "user", "content": "hi"}], None, 0.4, 8192)
    assert "systemInstruction" not in body


# ============================================ _gemini_can_zero_thinking_budget


@pytest.mark.parametrize("model,expected", [
    ("gemini-2.5-flash", True),
    ("gemini-2.5-flash-lite", True),
    ("GEMINI-2.5-FLASH", True),  # case-insensitive
    ("gemini-2.5-pro", False),
    ("gemini-3-pro-image", False),
    ("gemini-3-flash", False),  # gemini-3.x uses thinkingLevel, never thinkingBudget
    ("gemini-1.5-pro", False),
    # the floating "-latest" flash aliases resolve server-side to a gemini-3
    # flash (which uses thinkingLevel), so they must NOT get thinkingBudget=0
    ("gemini-flash-latest", False),
    ("gemini-flash-lite-latest", False),
])
def test_can_zero_thinking_budget(model, expected):
    assert P._gemini_can_zero_thinking_budget(model) is expected


# ============================================ _gemini_parse_response


def test_parse_response_extracts_joined_text():
    data = {"candidates": [{"content": {"parts": [{"text": "hello "}, {"text": "world"}]},
                            "finishReason": "STOP"}]}
    assert P._gemini_parse_response(data, 8192) == "hello world"


def test_parse_response_max_tokens_raises_truncated_with_house_message():
    data = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]}
    with pytest.raises(P.TruncatedOutput, match="8192-token limit"):
        P._gemini_parse_response(data, 8192)


def test_parse_response_missing_parts_with_finish_reason_does_not_crash():
    # Contract: parts may be absent even when a finishReason is present.
    data = {"candidates": [{"finishReason": "STOP", "content": {}}]}
    assert P._gemini_parse_response(data, 8192) == ""


@pytest.mark.parametrize("reason", [
    "SAFETY", "RECITATION", "PROHIBITED_CONTENT", "SPII", "BLOCKLIST",
])
def test_parse_response_blocked_finish_reasons_raise_provider_error(reason):
    data = {"candidates": [{"finishReason": reason, "content": {"parts": [{"text": "x"}]}}]}
    with pytest.raises(P.ProviderError):
        P._gemini_parse_response(data, 8192)


def test_parse_response_prompt_block_reason_raises_before_touching_candidates():
    data = {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
    with pytest.raises(P.ProviderError, match="SAFETY"):
        P._gemini_parse_response(data, 8192)


def test_parse_response_no_candidates_raises_provider_error():
    with pytest.raises(P.ProviderError):
        P._gemini_parse_response({}, 8192)


# ============================================ mocked-transport plumbing
# Mirrors test_pipeline_quality.py's _mock_client/_json_response exactly, but
# is kept local to this file (a NEW file) to avoid any cross-file collision.

_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatching


def _mock_client(handler):
    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return _RealAsyncClient(*args, **kwargs)
    return factory


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# ============================================ complete() end-to-end


def test_complete_sends_expected_url_header_and_body(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["api_key_header"] = request.headers.get("x-goog-api-key")
        seen["no_bearer_auth"] = "authorization" not in request.headers
        seen["body"] = json.loads(request.content)
        return _json_response({"candidates": [
            {"content": {"parts": [{"text": '{"ok":true}'}]}, "finishReason": "STOP"}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    # base_url="" exercises the "fall back to the real Gemini host" path (the
    # field's shared pydantic default is the ollama URL; a real UI save always
    # supplies the gemini preset's base_url, but the fallback itself still
    # needs direct coverage).
    cfg = ProviderConfig(kind="gemini", base_url="", model="gemini-2.5-flash",
                         api_key="AIza-secret", max_tokens=1234)
    out = asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}],
                                 schema=_SAMPLE_SCHEMA, max_tokens=cfg.max_tokens))

    assert out == '{"ok":true}'
    assert seen["url"] == ("https://generativelanguage.googleapis.com"
                           "/v1beta/models/gemini-2.5-flash:generateContent")
    assert seen["api_key_header"] == "AIza-secret"
    assert seen["no_bearer_auth"]
    assert seen["body"]["generationConfig"]["maxOutputTokens"] == 1234
    assert seen["body"]["generationConfig"]["responseJsonSchema"] == _SAMPLE_SCHEMA
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["body"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


def test_complete_strips_models_prefix_and_honors_base_url_override(monkeypatch):
    def handler(request):
        url = str(request.url)
        assert url == "https://my-proxy.example/v1beta/models/gemini-2.5-flash:generateContent"
        return _json_response({"candidates": [
            {"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="gemini", model="models/gemini-2.5-flash",
                         base_url="https://my-proxy.example", api_key="x")
    out = asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}]))
    assert out == "ok"


def test_complete_raises_truncated_output_on_max_tokens(monkeypatch):
    def handler(request):
        return _json_response({"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="gemini", model="gemini-2.5-pro", api_key="x")
    with pytest.raises(P.TruncatedOutput):
        asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}]))


def test_complete_raises_provider_error_on_prompt_block(monkeypatch):
    def handler(request):
        return _json_response({"promptFeedback": {"blockReason": "BLOCKLIST"},
                               "candidates": []})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="gemini", model="gemini-2.5-flash", api_key="x")
    with pytest.raises(P.ProviderError):
        asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}]))


# ============================================ stream_text() end-to-end


def test_stream_text_sse_url_and_yields_text_chunks(monkeypatch):
    seen = {}
    sse = (
        'data: {"candidates":[{"content":{"parts":[{"text":"Hello"}]}}]}\n\n'
        'data: {"candidates":[{"content":{"parts":[{"text":" world"}]},'
        '"finishReason":"STOP"}]}\n\n'
    ).encode()

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=sse)

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="gemini", base_url="", model="gemini-2.5-flash", api_key="x")

    async def drive():
        return [p async for p in P.stream_text(cfg, [{"role": "user", "content": "hi"}])]

    parts = asyncio.run(drive())
    assert parts == ["Hello", " world"]
    assert seen["url"] == ("https://generativelanguage.googleapis.com/v1beta/models/"
                           "gemini-2.5-flash:streamGenerateContent?alt=sse")


# ============================================ embed() end-to-end


def test_embed_batch_request_shape_and_order(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["api_key_header"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return _json_response({"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="gemini", base_url="", model="gemini-embedding-001", api_key="x")
    out = asyncio.run(P.embed(cfg, ["chunk one", "chunk two"]))

    assert seen["url"] == ("https://generativelanguage.googleapis.com/v1beta/models/"
                           "gemini-embedding-001:batchEmbedContents")
    assert seen["api_key_header"] == "x"
    reqs = seen["body"]["requests"]
    assert [r["content"]["parts"][0]["text"] for r in reqs] == ["chunk one", "chunk two"]
    assert all(r["model"] == "models/gemini-embedding-001" for r in reqs)
    assert all(r["taskType"] == "RETRIEVAL_DOCUMENT" for r in reqs)
    assert out.dtype == np.float32
    assert out.shape == (2, 2)
    assert out[0].tolist() == pytest.approx([0.1, 0.2])
    assert out[1].tolist() == pytest.approx([0.3, 0.4])


# ============================================ list_models() end-to-end


def test_list_models_strips_models_prefix(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/models"
        assert request.headers.get("x-goog-api-key") == "x"
        return _json_response({"models": [
            {"name": "models/gemini-2.5-flash",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-001",
             "supportedGenerationMethods": ["embedContent"]},
        ]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ProviderConfig(kind="gemini", base_url="", api_key="x")
    out = asyncio.run(P.list_models(cfg))
    # lenient on purpose: not filtered down to only generateContent-capable models
    assert out == ["gemini-2.5-flash", "gemini-embedding-001"]


# ============================================ C1: generate_validated wiring


def test_generate_validated_passes_cfg_max_tokens_into_complete(monkeypatch):
    seen = {}

    async def fake_complete(cfg, history, schema=None, temperature=0.4,
                            max_tokens=P.DEFAULT_MAX_TOKENS):
        seen["max_tokens"] = max_tokens
        return '{"ok": true}'

    monkeypatch.setattr(P, "complete", fake_complete)
    cfg = ProviderConfig(kind="gemini", model="gemini-2.5-flash", max_tokens=2048)
    result = asyncio.run(P.generate_validated(
        cfg, [{"role": "user", "content": "hi"}], schema={"type": "object"},
        parse=json.loads))

    assert result == {"ok": True}
    assert seen["max_tokens"] == 2048
