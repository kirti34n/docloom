"""Stage B regression: image generation ("Nano Banana") in providers.py.

Covers CONTRACT C7's provider slice:
- ImageProviderConfig defaults (kind="gemini", the real Gemini base_url, the
  gemini-2.5-flash-image model id, enabled=False so the paid cloud feature
  stays opt-in).
- _gemini_image_request_body: pure builder for the generateContent body,
  asserted against the exact shape from research-nano-banana.md section 3
  (responseModalities:["IMAGE"] + imageConfig.aspectRatio).
- _gemini_parse_image_response: pure parser that extracts base64 image bytes
  from the first part with inlineData (or inline_data), and raises
  ProviderError on a blocked prompt, an empty candidate list, a
  safety/blocked finishReason, or a candidate with no image part at all.
- generate_image(): end-to-end through a mocked httpx transport, mirroring
  the _mock_client/_json_response pattern in test_stageA_providers.py
  (duplicated here, not imported, since this is a new, independent file).

generate_image() is intentionally NOT wired into complete()/stream_text() as
a `kind` branch (per CONTRACT C7); this file only exercises the standalone
function.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-stageB-providers-image-"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from docloom_studio import providers as P  # noqa: E402
from docloom_studio.providers import ImageProviderConfig  # noqa: E402

_RAW_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-bytes-not-a-real-png"
_B64_PNG = base64.b64encode(_RAW_PNG_BYTES).decode()


# =========================================== ImageProviderConfig defaults


def test_image_provider_config_defaults():
    cfg = ImageProviderConfig()
    assert cfg.kind == "gemini"
    assert cfg.base_url == "https://generativelanguage.googleapis.com"
    assert cfg.api_key == ""
    assert cfg.model == "gemini-2.5-flash-image"
    assert cfg.enabled is False


def test_image_provider_config_accepts_overrides():
    cfg = ImageProviderConfig(api_key="secret", model="gemini-3-pro-image", enabled=True)
    assert cfg.api_key == "secret"
    assert cfg.model == "gemini-3-pro-image"
    assert cfg.enabled is True


# =========================================== _gemini_image_request_body


def test_image_request_body_matches_contract_shape():
    body = P._gemini_image_request_body("a clean isometric illustration", "16:9")
    assert body == {
        "contents": [{"role": "user", "parts": [{"text": "a clean isometric illustration"}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "16:9"},
        },
    }


def test_image_request_body_threads_aspect_ratio():
    body = P._gemini_image_request_body("prompt", "9:16")
    assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "9:16"


# =========================================== _gemini_parse_image_response


def test_parse_image_response_extracts_bytes_from_inline_data_camel_case():
    data = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": _B64_PNG}},
    ]}, "finishReason": "STOP"}]}
    assert P._gemini_parse_image_response(data) == _RAW_PNG_BYTES


def test_parse_image_response_extracts_bytes_from_inline_data_snake_case():
    data = {"candidates": [{"content": {"parts": [
        {"inline_data": {"mime_type": "image/png", "data": _B64_PNG}},
    ]}, "finishReason": "STOP"}]}
    assert P._gemini_parse_image_response(data) == _RAW_PNG_BYTES


def test_parse_image_response_skips_a_leading_text_part():
    # The model can interleave a text part ("Here is the illustration...")
    # before the image part; the parser must not stop at the first part.
    data = {"candidates": [{"content": {"parts": [
        {"text": "Here is the illustration you asked for."},
        {"inlineData": {"mimeType": "image/png", "data": _B64_PNG}},
    ]}, "finishReason": "STOP"}]}
    assert P._gemini_parse_image_response(data) == _RAW_PNG_BYTES


def test_parse_image_response_no_image_part_raises_provider_error():
    data = {"candidates": [{"content": {"parts": [{"text": "just text, no image"}]},
                            "finishReason": "STOP"}]}
    with pytest.raises(P.ProviderError, match="no image"):
        P._gemini_parse_image_response(data)


@pytest.mark.parametrize("reason", ["IMAGE_SAFETY", "PROHIBITED_CONTENT", "SAFETY"])
def test_parse_image_response_blocked_finish_reason_raises_provider_error(reason):
    data = {"candidates": [{"content": {"parts": []}, "finishReason": reason}]}
    with pytest.raises(P.ProviderError, match=reason):
        P._gemini_parse_image_response(data)


def test_parse_image_response_prompt_block_reason_raises_before_touching_candidates():
    data = {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
    with pytest.raises(P.ProviderError, match="SAFETY"):
        P._gemini_parse_image_response(data)


def test_parse_image_response_no_candidates_raises_provider_error():
    with pytest.raises(P.ProviderError):
        P._gemini_parse_image_response({})


def test_parse_image_response_part_with_no_data_key_is_skipped_not_crashed():
    # A malformed/empty inlineData blob (no "data") must not raise a KeyError;
    # it is simply not a usable image part.
    data = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png"}},
        {"inlineData": {"mimeType": "image/png", "data": _B64_PNG}},
    ]}, "finishReason": "STOP"}]}
    assert P._gemini_parse_image_response(data) == _RAW_PNG_BYTES


# =========================================== mocked-transport plumbing
# Mirrors test_stageA_providers.py's _mock_client/_json_response exactly, but
# is kept local to this file (a NEW file) to avoid any cross-file collision.

_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatching


def _mock_client(handler):
    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return _RealAsyncClient(*args, **kwargs)
    return factory


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# =========================================== generate_image() end-to-end


def test_generate_image_sends_expected_url_header_and_body(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["api_key_header"] = request.headers.get("x-goog-api-key")
        seen["no_bearer_auth"] = "authorization" not in request.headers
        seen["body"] = json.loads(request.content)
        return _json_response({"candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "image/png", "data": _B64_PNG}},
        ]}, "finishReason": "STOP"}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ImageProviderConfig(api_key="AIza-secret")
    out = asyncio.run(P.generate_image(cfg, "a data pipeline illustration"))

    assert out == _RAW_PNG_BYTES
    assert seen["url"] == ("https://generativelanguage.googleapis.com"
                           "/v1beta/models/gemini-2.5-flash-image:generateContent")
    assert seen["api_key_header"] == "AIza-secret"
    assert seen["no_bearer_auth"]
    assert seen["body"] == {
        "contents": [{"role": "user", "parts": [{"text": "a data pipeline illustration"}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "16:9"},
        },
    }


def test_generate_image_threads_custom_aspect_ratio(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _json_response({"candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "image/png", "data": _B64_PNG}},
        ]}, "finishReason": "STOP"}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ImageProviderConfig(api_key="x")
    asyncio.run(P.generate_image(cfg, "prompt", aspect_ratio="9:16"))
    assert seen["body"]["generationConfig"]["imageConfig"]["aspectRatio"] == "9:16"


def test_generate_image_strips_models_prefix_and_honors_base_url_override(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://my-proxy.example/v1beta/models/gemini-2.5-flash-image:generateContent"
        return _json_response({"candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "image/png", "data": _B64_PNG}},
        ]}, "finishReason": "STOP"}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ImageProviderConfig(model="models/gemini-2.5-flash-image",
                              base_url="https://my-proxy.example", api_key="x")
    out = asyncio.run(P.generate_image(cfg, "prompt"))
    assert out == _RAW_PNG_BYTES


def test_generate_image_falls_back_to_default_base_url_when_blank(monkeypatch):
    def handler(request):
        assert str(request.url).startswith("https://generativelanguage.googleapis.com/")
        return _json_response({"candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "image/png", "data": _B64_PNG}},
        ]}, "finishReason": "STOP"}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ImageProviderConfig(base_url="", api_key="x")
    out = asyncio.run(P.generate_image(cfg, "prompt"))
    assert out == _RAW_PNG_BYTES


def test_generate_image_raises_provider_error_on_blocked_response(monkeypatch):
    def handler(request):
        return _json_response({"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ImageProviderConfig(api_key="x")
    with pytest.raises(P.ProviderError):
        asyncio.run(P.generate_image(cfg, "prompt"))


def test_generate_image_raises_provider_error_when_no_image_returned(monkeypatch):
    # e.g. the model refused and only returned a text explanation.
    def handler(request):
        return _json_response({"candidates": [{"content": {"parts": [
            {"text": "I can't create that image."},
        ]}, "finishReason": "STOP"}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ImageProviderConfig(api_key="x")
    with pytest.raises(P.ProviderError):
        asyncio.run(P.generate_image(cfg, "prompt"))


def test_generate_image_propagates_http_error_status(monkeypatch):
    def handler(request):
        return httpx.Response(429, json={"error": "rate limited"})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = ImageProviderConfig(api_key="x")
    with pytest.raises(P.ProviderError, match="429"):
        asyncio.run(P.generate_image(cfg, "prompt"))
