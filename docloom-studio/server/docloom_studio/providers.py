"""LLM provider layer: two HTTP call shapes, five presets.

llama-server / LM Studio / OpenAI speak OpenAI-compatible chat completions
(json_schema response_format = enforced structured output on llama-server and
LM Studio). Ollama uses its native /api/chat with the `format` schema for
masking; the `think` API flag is never set (ollama#15260: think=false silently
disables masking). For qwen3 models we instead inject the `/no_think` prompt
token, which stops the slow reasoning block while keeping masking on.
Anthropic uses /v1/messages with output_config structured outputs.

Every schema-shaped generation goes through generate_validated(): complete →
lenient parse → optional lint → feed findings back → retry. docloom's
parse_llm_output does the lenient half.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

import httpx
import numpy as np
from pydantic import BaseModel

T = TypeVar("T")

TIMEOUT = httpx.Timeout(600.0, connect=10.0)


class ProviderConfig(BaseModel):
    kind: str = "ollama"  # llama-server | ollama | lmstudio | openai | anthropic
    base_url: str = "http://localhost:11434"
    api_key: str = ""
    model: str = ""


class ProviderError(RuntimeError):
    pass


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


async def complete(
    cfg: ProviderConfig,
    messages: list[dict[str, str]],
    schema: dict | None = None,
    temperature: float = 0.4,
    max_tokens: int = 8192,
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
                (cfg.base_url or "https://api.anthropic.com") + "/v1/messages",
                json=body,
                headers={
                    "x-api-key": cfg.api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            _raise_for_status(r)
            data = r.json()
            if data.get("stop_reason") == "refusal":
                raise ProviderError("the model declined this request")
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
            _raise_for_status(r)
            return r.json()["message"]["content"]

        # OpenAI-compatible: llama-server, lmstudio, openai
        body = {
            "model": cfg.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
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
        _raise_for_status(r)
        return r.json()["choices"][0]["message"]["content"]


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
                _raise_for_status(r)
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        return
            return

        if cfg.kind == "anthropic":
            # ponytail: non-streamed fallback; SSE parsing when chat UX needs it
            yield await complete(cfg, messages, temperature=temperature)
            return

        body = {
            "model": cfg.model, "messages": messages,
            "temperature": temperature, "stream": True,
        }
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        async with client.stream(
            "POST", cfg.base_url.rstrip("/") + "/v1/chat/completions",
            json=body, headers=headers,
        ) as r:
            _raise_for_status(r)
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
            _raise_for_status(r)
            return np.asarray(r.json()["embeddings"], dtype=np.float32)
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        r = await client.post(
            cfg.base_url.rstrip("/") + "/v1/embeddings",
            json={"model": cfg.model, "input": texts}, headers=headers,
        )
        _raise_for_status(r)
        rows = sorted(r.json()["data"], key=lambda d: d["index"])
        return np.asarray([d["embedding"] for d in rows], dtype=np.float32)


async def list_models(cfg: ProviderConfig) -> list[str]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        if cfg.kind == "ollama":
            r = await client.get(cfg.base_url.rstrip("/") + "/api/tags")
            _raise_for_status(r)
            return [m["name"] for m in r.json().get("models", [])]
        if cfg.kind == "anthropic":
            r = await client.get(
                (cfg.base_url or "https://api.anthropic.com") + "/v1/models",
                headers={"x-api-key": cfg.api_key,
                         "anthropic-version": "2023-06-01"},
            )
            _raise_for_status(r)
            return [m["id"] for m in r.json().get("data", [])]
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        r = await client.get(cfg.base_url.rstrip("/") + "/v1/models",
                             headers=headers)
        _raise_for_status(r)
        return [m["id"] for m in r.json().get("data", [])]


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
                             temperature=0.3 + 0.15 * (round_no - 1))
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


def _raise_for_status(r: httpx.Response) -> None:
    if r.status_code >= 400:
        try:
            detail = r.read().decode("utf-8", "replace")[:400]
        except Exception:
            detail = ""
        raise ProviderError(f"provider returned HTTP {r.status_code}: {detail}")
