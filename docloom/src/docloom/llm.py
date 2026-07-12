"""Helpers for generating the IR with an LLM via structured output."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from .ir import Document


_VALID_TYPES = {
    "heading", "paragraph", "bullets", "numbered", "quote",
    "code", "table", "image", "callout", "divider",
    "chart", "stats", "artifact",
}
_TAG_TO_MODEL = {
    "heading": "Heading", "paragraph": "Paragraph", "bullets": "BulletList",
    "numbered": "NumberedList", "quote": "Quote", "code": "Code",
    "table": "Table", "image": "Image", "callout": "Callout",
    "divider": "Divider", "chart": "Chart", "stats": "StatRow",
    "artifact": "Artifact",
}
_MODEL_NAMES = set(_TAG_TO_MODEL.values())
# tag variants observed from real (mostly local) models
_TYPE_ALIASES = {
    "bulletlist": "bullets", "bullet_list": "bullets", "bulletpoints": "bullets",
    "bullet": "bullets", "list": "bullets", "ul": "bullets",
    "numberedlist": "numbered", "numbered_list": "numbered", "ol": "numbered",
    "orderedlist": "numbered", "ordered_list": "numbered",
    "blockquote": "quote", "codeblock": "code", "code_block": "code",
    "img": "image", "picture": "image", "hr": "divider", "rule": "divider",
    "text": "paragraph", "para": "paragraph", "p": "paragraph",
    "kpi": "stats", "kpis": "stats", "metric": "stats", "metrics": "stats",
    "stat": "stats", "graph": "chart", "barchart": "chart",
    "linechart": "chart", "piechart": "chart", "chart_block": "chart",
    "diagram": "artifact", "infographic": "artifact",
}


# only "blocks" and "right" ever hold Block union members (the IR is
# deliberately non-recursive), so only dicts reached through those keys
# carry a meaningful block "type" tag. Checking every dict, including the
# document root or a slide, would flag harmless extra tags Pydantic itself
# ignores (extra="ignore" is the default), e.g. {"type": "document", ...}.
# keys whose value holds a type-tagged Block/Image node: the block lists
# (blocks/right) plus the standalone Image slots (Slide.image, Document.logo)
_BLOCK_LIST_KEYS = {"blocks", "right", "image", "logo"}


def _normalize_types(
    node: Any, path: str, problems: list[str], in_blocks: bool = False
) -> None:
    if isinstance(node, dict):
        if in_blocks:
            tag = node.get("type")
            if isinstance(tag, str) and tag not in _VALID_TYPES:
                norm = tag.strip().lower().replace("-", "_")
                if norm in _VALID_TYPES:
                    node["type"] = norm
                else:
                    alias = _TYPE_ALIASES.get(norm)
                    if alias:
                        node["type"] = alias
                    else:
                        problems.append(f'{path}: unknown block type "{tag}"')
        for key, value in node.items():
            _normalize_types(
                value, f"{path}.{key}", problems, key in _BLOCK_LIST_KEYS
            )
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _normalize_types(value, f"{path}[{i}]", problems, in_blocks)


def _parse_one(t: str, model: type[BaseModel]) -> Any:
    """Parse+validate a single JSON candidate string. Raises ValueError (or
    lets a validation Exception through) if this candidate is not `model`."""
    try:
        data = json.loads(t)
    except json.JSONDecodeError as e:
        # quote the offending region so an LLM retry loop can actually fix it
        lo, hi = max(0, e.pos - 60), min(len(t), e.pos + 60)
        raise ValueError(
            f"invalid JSON ({e.msg} at position {e.pos}), "
            f"near: ...{t[lo:hi]}..."
        ) from e
    if (model is Document and isinstance(data, dict)
            and set(data) == {"document"}):
        data = data["document"]
    problems: list[str] = []
    _normalize_types(data, "$", problems)
    if problems:
        valid = ", ".join(sorted(_VALID_TYPES))
        raise ValueError(
            "; ".join(problems) + f". Valid block types are: {valid}."
        )
    try:
        return model.model_validate(data)
    except Exception as e:
        filtered = _filter_union_errors(data, e)
        if filtered:
            raise ValueError(
                "document validation failed: " + "; ".join(filtered[:8])
            ) from e
        raise


def parse_llm_output(text: str, model: type[BaseModel] = Document) -> Any:
    """Parse an LLM's response leniently into `model` (default Document).

    Providers with enforced structured output return bare JSON, but smaller
    or local models (and providers that silently drop the schema, as Ollama
    does for some model families) wrap it in markdown fences or prose, add a
    {"document": ...} envelope, or misname block type tags ("bulletlist").
    This normalizes all of that before validating; validation itself stays
    strict, and an unknown block type raises one clear, self-correctable
    error instead of a cascade of union mismatches.

    A model that emits an illustrative example fence alongside the real
    document fence (common with local models) is handled by collecting every
    fenced JSON candidate (matched non-greedily, so each fence is isolated
    instead of spanning from the first candidate's { to the last candidate's })
    and returning the *richest* one that validates. An example is usually a
    title-only skeleton while the real document carries blocks/slides/sheets,
    so the real one wins whether the example precedes or follows it.
    """
    t = text.strip()
    candidates = [
        m.group(1)
        for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL)
    ]
    if not candidates:
        start, end = t.find("{"), t.rfind("}")
        candidates = [t[start:end + 1]] if start != -1 and end > start else [t]

    validated = []
    for candidate in candidates:
        try:
            validated.append(_parse_one(candidate, model))
        except Exception:
            continue
    if not validated:
        return _parse_one(candidates[-1], model)  # none valid: surface its error

    def _content_len(parsed: Any) -> int:
        # blocks/slides/sheets on a Document; 0 for a bare skeleton or a model
        # that has none of them, so ties fall through to the later candidate
        return sum(len(getattr(parsed, attr, None) or [])
                   for attr in ("blocks", "slides", "sheets"))

    best = validated[0]
    for parsed in validated[1:]:
        if _content_len(parsed) >= _content_len(best):  # >= keeps the later one
            best = parsed
    return best


def _filter_union_errors(data: Any, exc: Exception) -> list[str]:
    """Reduce a plain-union ValidationError cascade to the errors of the
    union member the block's own type tag names.

    A block like {"type": "table", "rows": "oops"} fails every union member,
    and Pydantic reports all ten — leading with 'Input should be "heading"',
    which live testing showed actively misleads a self-correcting LLM. Only
    the Table branch is relevant; keep it."""
    if not hasattr(exc, "errors"):
        return []

    scalar_members = {"bool", "int", "float", "str"}

    def tag_matches(loc: tuple) -> bool:
        obj = data
        for segment in loc:
            if isinstance(segment, str) and segment in _MODEL_NAMES:
                tag = obj.get("type") if isinstance(obj, dict) else None
                expected = _TAG_TO_MODEL.get(tag)
                if expected is not None and expected != segment:
                    return False
                continue  # member names are not keys in the data
            # Cell union: a dict cell can only be a Formula, a scalar cell
            # can only be a scalar — drop the other branch's noise
            if segment == "Formula" and not isinstance(obj, dict):
                return False
            if segment in scalar_members and isinstance(obj, dict):
                return False
            if isinstance(segment, str) and (segment == "Formula"
                                             or segment in scalar_members):
                continue
            try:
                obj = obj[segment]
            except Exception:
                return True  # cannot resolve; keep the error
        return True

    kept = []
    for err in exc.errors():
        if tag_matches(err["loc"]):
            path = ".".join(str(s) for s in err["loc"])
            kept.append(f"{path}: {err['msg']}")
    return kept


def llm_schema(model: type[BaseModel] = Document) -> dict[str, Any]:
    """JSON Schema for Document (or any docloom model), prepared for LLM
    structured output — pass e.g. Slide for per-slide drafting pipelines.

    The IR is deliberately non-recursive with plain tagged unions (anyOf, never
    oneOf), and this helper closes every object with additionalProperties:
    false — the shape Anthropic structured outputs and OpenAI json_schema mode
    accept directly. OpenAI *strict* mode additionally requires every property
    to be listed in `required`; the OpenAI SDK's .parse()/Pydantic helpers
    apply that transform for you, so pass the Document model there instead of
    this raw schema.
    """
    schema = model.model_json_schema()

    # Anthropic's raw API rejects these; Pydantic still validates the ranges
    # when you parse the model output, so nothing is lost by stripping them.
    constraint_keys = (
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        "multipleOf", "minLength", "maxLength", "pattern",
    )
    # editor/asset bookkeeping the LLM must never invent (all optional, so
    # removing them + additionalProperties:false forbids emission; Source.id
    # is REQUIRED and therefore kept)
    bookkeeping = ("id", "asset_id", "artifact_id")

    def close(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                if "additionalProperties" not in node:
                    node["additionalProperties"] = False
                props = node.get("properties", {})
                required = node.get("required", [])
                for key in bookkeeping:
                    if key in props and key not in required:
                        del props[key]
                # the Literal type tags have defaults so Pydantic leaves them
                # optional, but an untagged block resolves against the plain
                # union unpredictably — force the model to always emit them
                props = node.get("properties", {})
                if "const" in props.get("type", {}):
                    required = node.setdefault("required", [])
                    if "type" not in required:
                        required.append("type")
            if node.get("type") in ("integer", "number", "string"):
                for key in constraint_keys:
                    node.pop(key, None)
            for value in node.values():
                close(value)
        elif isinstance(node, list):
            for value in node:
                close(value)

    close(schema)
    return schema


AUTHORING_GUIDE = """\
You write documents as docloom JSON (schema provided). Rules:
- Slides: one idea per slide. Max 7 bullets/slide, max 130 chars/bullet,
  titles at most 60 chars. Put detail in `notes` (speaker notes), not the slide.
- Use layouts: "title" for the opener, "section" for chapter breaks,
  "two_column" for comparisons, "quote" for a single big statement.
- Reports: use heading levels in order (1, then 2, then 3). Prefer short
  paragraphs and bullet lists over walls of text.
- Sheets: put numbers in typed cells (not strings); use {"formula": "=..."}
  for totals; set column `format` for currency/percent/date columns.
- Cite: every factual claim from provided evidence gets a span with
  `cite` set to a source id, and that source must exist in `sources`.
  If no evidence supports a claim, do not state it.
- Text fields accept either a plain string or a list of spans; only use
  spans when you need bold/italic/links/citations.
- Charts: use a "chart" block with `labels` and `series` (every series has
  exactly one value per label). Prefer a chart over a table when the point
  is a trend or comparison.
- Key numbers: use a "stats" block (label + display-ready value + optional
  delta) instead of burying them in prose.
- Images: emit an "image" block (or a slide `image` for hero/image_left/
  image_right layouts) with a short `query` describing the ideal picture.
  Never invent file paths.
"""
