"""LLM provider layer: several HTTP call shapes, six presets.

llama-server / LM Studio / OpenAI speak OpenAI-compatible chat completions
(json_schema response_format = enforced structured output on llama-server and
LM Studio). Ollama uses its native /api/chat with the `format` schema for
masking; the `think` API flag is never set (ollama#15260: think=false silently
disables masking). For qwen3 models we instead inject the `/no_think` prompt
token, which stops the slow reasoning block while keeping masking on.
Anthropic uses /v1/messages with output_config structured outputs. Gemini uses
its native generateContent / streamGenerateContent / embedContent endpoints,
with responseJsonSchema for structured output and thinkingConfig.thinkingBudget
to disable reasoning on the models that allow it.

Every schema-shaped generation goes through generate_validated(): complete →
lenient parse → optional lint → feed findings back → retry. docloom's
parse_llm_output does the lenient half.

Image generation (Nano Banana / gemini-2.5-flash-image) is a separate,
smaller surface: ImageProviderConfig + generate_image() call Gemini's same
generateContent endpoint but with responseModalities:["IMAGE"], and return
raw bytes instead of text. It is intentionally not a `kind` branch of
complete()/stream_text(): those are text-shaped (schema, thinking, streaming)
and image generation shares none of that.
"""

from __future__ import annotations

import base64
import json
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

import httpx
import numpy as np
from pydantic import BaseModel

T = TypeVar("T")

TIMEOUT = httpx.Timeout(600.0, connect=10.0)
DEFAULT_MAX_TOKENS = 8192  # anthropic requires an explicit cap; also complete()'s default


class ProviderConfig(BaseModel):
    kind: str = "ollama"  # llama-server | ollama | lmstudio | openai | anthropic | gemini
    base_url: str = "http://localhost:11434"
    api_key: str = ""
    model: str = ""
    max_tokens: int = DEFAULT_MAX_TOKENS
    # "auto" | "off" | "on". "auto"/"off" disable reasoning where the provider
    # allows it (see the gemini thinkingConfig logic below); "on" always
    # leaves the model's own default reasoning behavior in place.
    thinking: str = "auto"


class ImageProviderConfig(BaseModel):
    """Config for illustrative slide-image generation ("Nano Banana"). Kept
    separate from ProviderConfig: it has its own enable gate (the feature is a
    paid cloud call and must default off) and no schema/thinking/max_tokens
    concerns. Only kind="gemini" is implemented today."""
    kind: str = "gemini"
    base_url: str = "https://generativelanguage.googleapis.com"
    api_key: str = ""
    model: str = "gemini-2.5-flash-image"
    enabled: bool = False


class ProviderError(RuntimeError):
    pass


class TruncatedOutput(ProviderError):
    """The provider stopped because it hit the token cap, not because it
    finished. The output is almost certainly incomplete JSON, so retrying the
    identical call (as generate_validated does for a parse error) just wastes
    rounds on the same cap; callers get a clear reason instead of a confusing
    downstream parse failure."""


class GenerationFailed(ProviderError):
    def __init__(self, rounds: list[dict[str, Any]]):
        self.rounds = rounds
        super().__init__(
            f"no valid output after {len(rounds)} round(s): "
            + (rounds[-1].get("error", "?") if rounds else "no rounds")
        )


def _openai_required_transform(schema: dict) -> dict:
    """OpenAI strict mode requires every property listed in `required`."""
    schema = json.loads(json.dumps(schema))  # deep copy

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"].keys())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema


# qwen3 / qwen3.5 emit a slow <think> block by default (measured ~20x latency on
# Ollama, and it can exhaust the budget and return nothing). The `/no_think`
# soft-switch disables reasoning while KEEPING Ollama's `format` grammar masking
# on — unlike the `think=false` API flag, which silently disables masking
# (ollama#15260) and makes structured output unreliable. So we inject the token
# rather than set the flag.
def _apply_no_think(cfg: "ProviderConfig", messages: list[dict[str, str]]) -> list[dict[str, str]]:
    if cfg.kind != "ollama" or "qwen3" not in (cfg.model or "").lower():
        return messages
    msgs = [dict(m) for m in messages]
    for m in reversed(msgs):
        if m["role"] == "user":
            m["content"] = m["content"].rstrip() + " /no_think"
            return msgs
    msgs.append({"role": "user", "content": "/no_think"})
    return msgs


# Gemini's native Generative Language API is shaped nothing like the
# OpenAI-compatible convention every other fall-through branch speaks: the
# model id is part of the URL (colon-suffixed method), auth is a plain header
# (not Bearer), system prompts/roles/structured-output all use different
# keys, and finish/block reasons need their own defensive parse. The pure
# helpers below build the request body and parse the response without doing
# any network I/O, so they can be unit tested directly (no live API, no
# mocked transport needed).
_GEMINI_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
_GEMINI_BLOCKED_FINISH_REASONS = {
    "SAFETY", "RECITATION", "PROHIBITED_CONTENT", "SPII", "BLOCKLIST",
}
# Image generation can additionally stop with IMAGE_SAFETY (a safety trip
# specific to the image-output path); everything else that can block text
# generation applies here too.
_GEMINI_IMAGE_BLOCKED_FINISH_REASONS = _GEMINI_BLOCKED_FINISH_REASONS | {"IMAGE_SAFETY"}


def _gemini_base_url(cfg: "ProviderConfig") -> str:
    base = cfg.base_url
    # ProviderConfig.base_url defaults to the shared ollama localhost value,
    # which is not a real Gemini endpoint; treat it (and an empty value) as
    # unset so a gemini config that kept the default still reaches Google.
    if not base or base == ProviderConfig.model_fields["base_url"].default:
        base = _GEMINI_DEFAULT_BASE_URL
    return base.rstrip("/")


def _gemini_model_id(cfg: "ProviderConfig") -> str:
    return (cfg.model or "").removeprefix("models/")


def _gemini_can_zero_thinking_budget(model: str) -> bool:
    """Only the Gemini 2.x flash family accepts thinkingBudget: 0. 2.5-pro has a
    nonzero floor; gemini-3.x uses thinkingLevel instead of thinkingBudget. The
    floating "-latest" aliases (gemini-flash-latest) resolve server-side to a
    gemini-3 flash and never contain the literal "gemini-3", so match the 2.x
    flash ids explicitly rather than trying to deny "gemini-3"."""
    m = model.lower()
    return ("gemini-2.5-flash" in m or "gemini-2.0-flash" in m) and "pro" not in m


def _gemini_request_body(
    cfg: "ProviderConfig",
    messages: list[dict[str, str]],
    schema: dict | None,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    """Pure builder for the generateContent / streamGenerateContent body.
    Free of any httpx/network code so tests can call it directly."""
    contents = [
        {"role": "model" if m["role"] == "assistant" else m["role"],
         "parts": [{"text": m["content"]}]}
        for m in messages if m["role"] != "system"
    ]
    generation_config: dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens,
    }
    if schema is not None:
        generation_config["responseMimeType"] = "application/json"
        # responseJsonSchema (not the older responseSchema) accepts docloom's
        # llm_schema() dict as-is, including $defs/$ref/anyOf.
        generation_config["responseJsonSchema"] = schema
    if cfg.thinking != "on" and _gemini_can_zero_thinking_budget(_gemini_model_id(cfg)):
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}
    body: dict[str, Any] = {"contents": contents, "generationConfig": generation_config}
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    return body


def _gemini_parse_response(data: dict[str, Any], max_tokens: int) -> str:
    """Defensive parse per Gemini's finishReason/promptFeedback contract:
    candidates can be missing/empty, and content.parts can be absent even
    when a finishReason is present."""
    block_reason = data.get("promptFeedback", {}).get("blockReason")
    if block_reason:
        raise ProviderError(f"gemini blocked the prompt: {block_reason}")
    candidates = data.get("candidates") or []
    if not candidates:
        raise ProviderError("gemini returned no candidates")
    finish_reason = candidates[0].get("finishReason")
    if finish_reason == "MAX_TOKENS":
        raise TruncatedOutput(
            f"response was cut off at the {max_tokens}-token limit before finishing")
    if finish_reason in _GEMINI_BLOCKED_FINISH_REASONS:
        raise ProviderError(f"gemini stopped generating: {finish_reason}")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if "text" in p)
    # any other non-STOP terminal reason (LANGUAGE, OTHER, ...) that produced no
    # text is a failure, not a silent empty success
    if not text and finish_reason not in (None, "STOP"):
        raise ProviderError(f"gemini stopped without output: {finish_reason}")
    return text


def _gemini_image_request_body(prompt: str, aspect_ratio: str) -> dict[str, Any]:
    """Pure builder for the image generateContent body ("Nano Banana"). Free
    of any httpx/network code so tests can call it directly."""
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": aspect_ratio},
        },
    }


def _gemini_parse_image_response(data: dict[str, Any]) -> bytes:
    """Defensive parse of the image generateContent response: a prompt-level
    block, an empty candidate list, a safety-stopped candidate, or a
    candidate whose parts contain no image data must all raise ProviderError
    so a caller (e.g. a deck's per-slide image fill) can skip the slot
    instead of crashing the whole job."""
    block_reason = data.get("promptFeedback", {}).get("blockReason")
    if block_reason:
        raise ProviderError(f"gemini blocked the image prompt: {block_reason}")
    candidates = data.get("candidates") or []
    if not candidates:
        raise ProviderError("gemini returned no candidates for the image request")
    parts = candidates[0].get("content", {}).get("parts") or []
    for part in parts:
        # v1beta REST is camelCase (inlineData/mimeType); some proto/doc
        # samples show snake_case (inline_data) -- accept either.
        blob = part.get("inlineData") or part.get("inline_data")
        if blob and blob.get("data"):
            return base64.b64decode(blob["data"])
    finish_reason = candidates[0].get("finishReason")
    if finish_reason in _GEMINI_IMAGE_BLOCKED_FINISH_REASONS:
        raise ProviderError(f"gemini stopped generating the image: {finish_reason}")
    raise ProviderError(f"gemini returned no image (finishReason={finish_reason})")


async def complete(
    cfg: ProviderConfig,
    messages: list[dict[str, str]],
    schema: dict | None = None,
    temperature: float = 0.4,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """One completion, returning the text content."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        if cfg.kind == "anthropic":
            # temperature is intentionally not forwarded here: current-generation
            # Anthropic models reject the parameter (HTTP 400) on /v1/messages, so
            # every anthropic call runs at the API default and generate_validated's
            # per-round temperature escalation is a no-op for this provider.
            body: dict[str, Any] = {
                "model": cfg.model,
                "max_tokens": max_tokens,
                "messages": [m for m in messages if m["role"] != "system"],
            }
            system = "\n".join(m["content"] for m in messages if m["role"] == "system")
            if system:
                body["system"] = system
            if schema is not None:
                body["output_config"] = {
                    "format": {"type": "json_schema", "schema": schema}
                }
            r = await client.post(
                (cfg.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages",
                json=body,
                headers={
                    "x-api-key": cfg.api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            await _raise_for_status(r)
            data = r.json()
            if data.get("stop_reason") == "refusal":
                raise ProviderError("the model declined this request")
            if data.get("stop_reason") == "max_tokens":
                raise TruncatedOutput(
                    f"response was cut off at the {max_tokens}-token limit before finishing")
            return "".join(
                b.get("text", "") for b in data.get("content", [])
                if b.get("type") == "text"
            )

        if cfg.kind == "ollama":
            body = {
                "model": cfg.model,
                "messages": _apply_no_think(cfg, messages),
                "stream": False,
                "options": {"num_ctx": 16384, "temperature": temperature,
                            "num_predict": max_tokens},
            }
            if schema is not None:
                body["format"] = schema
            r = await client.post(cfg.base_url.rstrip("/") + "/api/chat", json=body)
            await _raise_for_status(r)
            data = r.json()
            if data.get("done_reason") == "length":
                raise TruncatedOutput(
                    f"response was cut off at the {max_tokens}-token limit before finishing")
            return data["message"]["content"]

        if cfg.kind == "gemini":
            model = _gemini_model_id(cfg)
            body = _gemini_request_body(cfg, messages, schema, temperature, max_tokens)
            r = await client.post(
                _gemini_base_url(cfg) + f"/v1beta/models/{model}:generateContent",
                json=body,
                headers={"x-goog-api-key": cfg.api_key},
            )
            await _raise_for_status(r)
            return _gemini_parse_response(r.json(), max_tokens)

        # OpenAI-compatible: llama-server, lmstudio, openai
        body = {"model": cfg.model, "messages": messages}
        if cfg.kind == "openai":
            # current OpenAI models (GPT-5.x, o-series) reject `max_tokens` (the
            # unified replacement is `max_completion_tokens`) and reject a
            # non-default temperature outright, so neither is sent as-is here;
            # every openai call runs at the API default temperature, same as
            # the anthropic branch above.
            body["max_completion_tokens"] = max_tokens
        else:
            body["temperature"] = temperature
            body["max_tokens"] = max_tokens
        if schema is not None:
            payload = _openai_required_transform(schema) if cfg.kind == "openai" else schema
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": payload, "strict": True},
            }
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        r = await client.post(
            cfg.base_url.rstrip("/") + "/v1/chat/completions",
            json=body, headers=headers,
        )
        await _raise_for_status(r)
        data = r.json()
        if data["choices"][0].get("finish_reason") == "length":
            raise TruncatedOutput(
                f"response was cut off at the {max_tokens}-token limit before finishing")
        return data["choices"][0]["message"]["content"]


async def stream_text(
    cfg: ProviderConfig,
    messages: list[dict[str, str]],
    temperature: float = 0.5,
) -> AsyncIterator[str]:
    """Streamed plain-text completion (chat). No schema enforcement here."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        if cfg.kind == "ollama":
            body = {
                "model": cfg.model, "messages": _apply_no_think(cfg, messages),
                "stream": True,
                "options": {"num_ctx": 16384, "temperature": temperature},
            }
            async with client.stream("POST", cfg.base_url.rstrip("/") + "/api/chat", json=body) as r:
                await _raise_for_status(r)
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    # a mid-stream runtime error is a 200-status NDJSON line with
                    # an "error" key instead of "message" (e.g. the model runner
                    # crashed) -- surface it instead of silently truncating
                    if chunk.get("error"):
                        raise ProviderError(f"ollama stream error: {chunk['error']}")
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        return
            return

        if cfg.kind == "anthropic":
            body: dict[str, Any] = {
                "model": cfg.model,
                "max_tokens": DEFAULT_MAX_TOKENS,
                "messages": [m for m in messages if m["role"] != "system"],
                "stream": True,
            }
            system = "\n".join(m["content"] for m in messages if m["role"] == "system")
            if system:
                body["system"] = system
            async with client.stream(
                "POST",
                (cfg.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages",
                json=body,
                headers={"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"},
            ) as r:
                await _raise_for_status(r)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    event = json.loads(payload)
                    kind = event.get("type")
                    if kind == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield delta["text"]
                    elif kind == "error":
                        raise ProviderError(f"anthropic stream error: {event.get('error')}")
            return

        if cfg.kind == "gemini":
            model = _gemini_model_id(cfg)
            body = _gemini_request_body(cfg, messages, None, temperature, cfg.max_tokens)
            async with client.stream(
                "POST",
                _gemini_base_url(cfg) + f"/v1beta/models/{model}:streamGenerateContent?alt=sse",
                json=body,
                headers={"x-goog-api-key": cfg.api_key},
            ) as r:
                await _raise_for_status(r)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    event = json.loads(payload)
                    # surface a blocked prompt or a safety/recitation stop rather
                    # than ending the stream silently and empty (mirrors the
                    # ollama/anthropic streams and the non-streaming parse)
                    block_reason = event.get("promptFeedback", {}).get("blockReason")
                    if block_reason:
                        raise ProviderError(f"gemini blocked the prompt: {block_reason}")
                    candidates = event.get("candidates") or []
                    if not candidates:
                        continue
                    fr = candidates[0].get("finishReason")
                    if fr in _GEMINI_BLOCKED_FINISH_REASONS:
                        raise ProviderError(f"gemini stopped generating: {fr}")
                    parts = candidates[0].get("content", {}).get("parts") or []
                    if parts and "text" in parts[0]:
                        yield parts[0]["text"]
            return

        body: dict[str, Any] = {
            "model": cfg.model, "messages": messages, "stream": True,
        }
        if cfg.kind != "openai":  # current OpenAI models reject a custom temperature
            body["temperature"] = temperature
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        async with client.stream(
            "POST", cfg.base_url.rstrip("/") + "/v1/chat/completions",
            json=body, headers=headers,
        ) as r:
            await _raise_for_status(r)
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                delta = (json.loads(payload)["choices"][0]
                         .get("delta", {}).get("content"))
                if delta:
                    yield delta


async def embed(cfg: ProviderConfig, texts: list[str]) -> np.ndarray:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        if cfg.kind == "ollama":
            r = await client.post(
                cfg.base_url.rstrip("/") + "/api/embed",
                json={"model": cfg.model, "input": texts},
            )
            await _raise_for_status(r)
            return np.asarray(r.json()["embeddings"], dtype=np.float32)
        if cfg.kind == "gemini":
            model = _gemini_model_id(cfg)
            r = await client.post(
                _gemini_base_url(cfg) + f"/v1beta/models/{model}:batchEmbedContents",
                json={"requests": [
                    {"model": f"models/{model}",
                     "content": {"parts": [{"text": t}]},
                     "taskType": "RETRIEVAL_DOCUMENT"}
                    for t in texts
                ]},
                headers={"x-goog-api-key": cfg.api_key},
            )
            await _raise_for_status(r)
            return np.asarray(
                [e["values"] for e in r.json()["embeddings"]], dtype=np.float32)
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        r = await client.post(
            cfg.base_url.rstrip("/") + "/v1/embeddings",
            json={"model": cfg.model, "input": texts}, headers=headers,
        )
        await _raise_for_status(r)
        rows = sorted(r.json()["data"], key=lambda d: d["index"])
        return np.asarray([d["embedding"] for d in rows], dtype=np.float32)


async def list_models(cfg: ProviderConfig) -> list[str]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        if cfg.kind == "ollama":
            r = await client.get(cfg.base_url.rstrip("/") + "/api/tags")
            await _raise_for_status(r)
            return [m["name"] for m in r.json().get("models", [])]
        if cfg.kind == "anthropic":
            r = await client.get(
                (cfg.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/models",
                headers={"x-api-key": cfg.api_key,
                         "anthropic-version": "2023-06-01"},
            )
            await _raise_for_status(r)
            return [m["id"] for m in r.json().get("data", [])]
        if cfg.kind == "gemini":
            r = await client.get(
                _gemini_base_url(cfg) + "/v1beta/models",
                headers={"x-goog-api-key": cfg.api_key},
            )
            await _raise_for_status(r)
            # Lenient on purpose: list_models doesn't know which slot
            # (generation vs embeddings) is asking, so don't hide models here
            # by guessing a capability filter.
            return [
                m["name"].removeprefix("models/")
                for m in r.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", []) or True
            ]
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        r = await client.get(cfg.base_url.rstrip("/") + "/v1/models",
                             headers=headers)
        await _raise_for_status(r)
        return [m["id"] for m in r.json().get("data", [])]


async def generate_image(
    cfg: ImageProviderConfig,
    prompt: str,
    *,
    aspect_ratio: str = "16:9",
) -> bytes:
    """Text-to-image via Gemini's native generateContent ("Nano Banana").
    Standalone rather than a complete()/stream_text() kind branch: the
    response is an inline-data image blob, not text, and shares none of the
    schema/thinking machinery those functions carry. Raises ProviderError
    (see _gemini_parse_image_response) on a blocked prompt or a candidate
    with no image part, so a per-slot caller can catch it and skip cleanly
    instead of crashing the whole job."""
    model = (cfg.model or "").removeprefix("models/")
    base = (cfg.base_url or _GEMINI_DEFAULT_BASE_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(
            f"{base}/v1beta/models/{model}:generateContent",
            json=_gemini_image_request_body(prompt, aspect_ratio),
            headers={"x-goog-api-key": cfg.api_key},
        )
        await _raise_for_status(r)
        return _gemini_parse_image_response(r.json())


async def generate_validated(
    cfg: ProviderConfig,
    messages: list[dict[str, str]],
    schema: dict,
    parse: Callable[[str], T],
    lint_fn: Callable[[T], list[str]] | None = None,
    max_rounds: int = 3,
    on_round: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None,
) -> T:
    """The one retry loop: complete → parse → lint → feed findings back."""
    history = list(messages)
    rounds: list[dict[str, Any]] = []
    for round_no in range(1, max_rounds + 1):
        raw = await complete(cfg, history, schema=schema,
                             temperature=0.3 + 0.15 * (round_no - 1),
                             max_tokens=cfg.max_tokens)
        entry: dict[str, Any] = {"round": round_no}
        problem: str | None = None
        result: T | None = None
        try:
            result = parse(raw)
            entry["parsed"] = True
            if lint_fn is not None:
                errors = lint_fn(result)
                entry["lint_errors"] = errors
                if errors:
                    problem = ("Fix these problems and return the complete "
                               "corrected JSON:\n" + "\n".join(errors))
        except Exception as e:  # lenient parser gives actionable messages
            entry["parsed"] = False
            entry["error"] = str(e)[:600]
            problem = (f"That JSON failed: {str(e)[:600]}\n"
                       "Return the complete corrected JSON.")
        rounds.append(entry)
        if on_round is not None:
            await on_round(round_no, entry)
        if problem is None and result is not None:
            return result
        history += [
            {"role": "assistant", "content": raw[-3500:]},
            {"role": "user", "content": problem or ""},
        ]
    raise GenerationFailed(rounds)


async def _raise_for_status(r: httpx.Response) -> None:
    if r.status_code >= 400:
        try:
            # r.read() is sync and raises RuntimeError on a response that came
            # from client.stream(...) ("Attempted to call a sync iterator on
            # an async stream"), which the bare except below swallowed --
            # silently losing the error body on every streaming call. aread()
            # is the async-safe equivalent and works for both response kinds.
            detail = (await r.aread()).decode("utf-8", "replace")[:400]
        except Exception:
            detail = ""
        raise ProviderError(f"provider returned HTTP {r.status_code}: {detail}")
