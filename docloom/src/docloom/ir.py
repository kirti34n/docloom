"""docloom document IR: the validated schema an LLM emits and every renderer consumes.

Design constraints (deliberate, do not "fix"):
- No recursive models. Anthropic structured outputs reject recursive schemas
  ("Too many recursive definitions"), so lists use flat items with an `level`
  indent instead of nested children.
- Plain tagged unions (Literal `type` field + Union), never Pydantic
  discriminated unions: those emit `oneOf` in JSON Schema, which both OpenAI
  strict mode and Anthropic structured outputs reject. Plain unions emit `anyOf`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import AfterValidator, BaseModel, Field

# C0 control characters (minus \t \n \r) are forbidden in OOXML and corrupt
# or crash every office renderer, so they are stripped at the IR boundary.
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

SafeStr = Annotated[str, AfterValidator(lambda v: _CTRL.sub("", v))]

# ---------------------------------------------------------------- rich text


class Span(BaseModel):
    """A run of text with optional formatting, link, or citation."""

    text: SafeStr
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: SafeStr | None = None
    cite: SafeStr | None = Field(
        None, description="id of a Source in document.sources that backs this claim"
    )


RichText = Union[SafeStr, list[Span]]
"""Plain string, or a list of spans when formatting/citations are needed."""


def spans(rt: RichText) -> list[Span]:
    """Normalize RichText to a list of spans."""
    return [Span(text=rt)] if isinstance(rt, str) else list(rt)


def plain(rt: RichText) -> str:
    """RichText as plain text (formatting dropped)."""
    return rt if isinstance(rt, str) else "".join(s.text for s in rt)


# ------------------------------------------------------------------- blocks


class Heading(BaseModel):
    type: Literal["heading"] = "heading"
    id: SafeStr | None = None
    level: int = Field(1, ge=1, le=4)
    text: RichText


class Paragraph(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    id: SafeStr | None = None
    text: RichText


class ListItem(BaseModel):
    text: RichText
    level: int = Field(0, ge=0, le=4, description="indent depth; 0 = top level")


class BulletList(BaseModel):
    type: Literal["bullets"] = "bullets"
    id: SafeStr | None = None
    items: list[ListItem]


class NumberedList(BaseModel):
    type: Literal["numbered"] = "numbered"
    id: SafeStr | None = None
    items: list[ListItem]


class Quote(BaseModel):
    type: Literal["quote"] = "quote"
    id: SafeStr | None = None
    text: RichText
    attribution: SafeStr | None = None


class Code(BaseModel):
    type: Literal["code"] = "code"
    id: SafeStr | None = None
    code: SafeStr
    language: SafeStr | None = None


class Table(BaseModel):
    type: Literal["table"] = "table"
    id: SafeStr | None = None
    header: list[RichText]
    rows: list[list[RichText]]
    caption: SafeStr | None = None


class Image(BaseModel):
    """An image slot. `path` embeds a local file; a slot may instead carry a
    `query` for an asset resolver to fill, or an `asset_id` once bound."""

    type: Literal["image"] = "image"
    id: SafeStr | None = None
    path: SafeStr | None = Field(
        None, description="local file path; renderers embed the image"
    )
    query: SafeStr | None = Field(
        None, description="what an asset resolver should find for this slot"
    )
    asset_id: SafeStr | None = Field(
        None, description="bound asset in an application asset library"
    )
    alt: SafeStr = ""
    caption: SafeStr | None = None


class Callout(BaseModel):
    type: Literal["callout"] = "callout"
    id: SafeStr | None = None
    style: Literal["info", "success", "warning", "danger"] = "info"
    text: RichText


class Divider(BaseModel):
    type: Literal["divider"] = "divider"
    id: SafeStr | None = None


class Series(BaseModel):
    name: SafeStr = ""
    values: list[float | None] = Field(
        default_factory=list, description="one value per label; null = gap"
    )


class Chart(BaseModel):
    """Typed columnar chart data. PPTX renders these as native editable
    charts; other formats fall back to a rendered image (`path`) or a table."""

    type: Literal["chart"] = "chart"
    id: SafeStr | None = None
    chart: Literal["bar", "column", "line", "area", "pie", "scatter"] = "column"
    title: SafeStr | None = None
    labels: list[SafeStr] = Field(default_factory=list)
    series: list[Series] = Field(default_factory=list)
    caption: SafeStr | None = None
    path: SafeStr | None = Field(
        None, description="optional pre-rendered SVG/PNG used by non-native formats"
    )


class Stat(BaseModel):
    label: SafeStr
    value: SafeStr = Field(description='display-ready, e.g. "42%", "$1.2M"')
    delta: SafeStr | None = Field(None, description='e.g. "+12% YoY"')


class StatRow(BaseModel):
    type: Literal["stats"] = "stats"
    id: SafeStr | None = None
    items: list[Stat]


class Artifact(BaseModel):
    """Reference to an externally-managed visual (diagram, infographic).
    The source spec lives in the application's artifact store; the IR carries
    only the reference and a baked render path for export."""

    type: Literal["artifact"] = "artifact"
    id: SafeStr | None = None
    kind: Literal["diagram", "infographic"] = "diagram"
    artifact_id: SafeStr | None = None
    path: SafeStr | None = Field(
        None, description="rendered SVG/PNG file; export embeds this"
    )
    alt: SafeStr = ""
    caption: SafeStr | None = None


Block = Union[
    Heading, Paragraph, BulletList, NumberedList,
    Quote, Code, Table, Image, Callout, Divider,
    Chart, StatRow, Artifact,
]


# ------------------------------------------------------------------- slides


class Slide(BaseModel):
    """Layout *intent*, not geometry - the renderer owns coordinates."""

    layout: Literal[
        "title", "section", "content", "two_column", "quote",
        "hero", "image_left", "image_right",
    ] = "content"
    id: SafeStr | None = None
    title: SafeStr | None = None
    subtitle: SafeStr | None = Field(None, description="used by title/section layouts")
    image: Image | None = Field(
        None, description="image slot for hero/image_left/image_right layouts"
    )
    accent: SafeStr | None = Field(
        None, description="optional #RRGGBB accent override for this slide"
    )
    blocks: list[Block] = Field(
        default_factory=list, description="slide body; left column for two_column"
    )
    right: list[Block] = Field(
        default_factory=list, description="right column for two_column layout"
    )
    notes: SafeStr | None = Field(None, description="speaker notes")


# ------------------------------------------------------------------- sheets


class Formula(BaseModel):
    """A spreadsheet formula cell, e.g. {"formula": "=SUM(B2:B10)"}."""

    formula: SafeStr


Cell = Union[Formula, bool, int, float, SafeStr, None]


class Column(BaseModel):
    header: SafeStr
    width: float | None = Field(None, description="column width in characters")
    format: SafeStr | None = Field(
        None, description='Excel number format, e.g. "#,##0.00", "0.0%", "yyyy-mm-dd"'
    )


class Sheet(BaseModel):
    name: SafeStr
    columns: list[Column]
    rows: list[list[Cell]]


# ------------------------------------------------------------------ sources


class Source(BaseModel):
    """An evidence record; spans reference it via Span.cite = Source.id."""

    id: SafeStr
    title: SafeStr
    url: SafeStr | None = None
    publisher: SafeStr | None = None
    date: SafeStr | None = None


# ---------------------------------------------------------------- document


class Document(BaseModel):
    """The root IR. blocks -> reports (DOCX/PDF/HTML/MD); slides -> decks
    (PPTX); sheets -> workbooks (XLSX). A document may carry any mix."""

    title: SafeStr
    subtitle: SafeStr | None = None
    authors: list[SafeStr] = Field(default_factory=list)
    date: SafeStr | None = None
    logo: Image | None = Field(
        None, description="brand logo shown on every slide / in report headers"
    )
    blocks: list[Block] = Field(default_factory=list)
    slides: list[Slide] = Field(default_factory=list)
    sheets: list[Sheet] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)

    # -- convenience I/O ---------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Document":
        # utf-8-sig: tolerate the BOM that PowerShell/Notepad prepend
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8-sig"))

    def save(self, path: str | Path) -> None:
        # never exclude defaults: the Literal `type` tags equal their defaults,
        # and dropping them corrupts block types on reload (plain-union IR)
        Path(path).write_text(
            self.model_dump_json(indent=2, exclude_none=True), encoding="utf-8"
        )


def ensure_ids(doc: Document) -> Document:
    """Fill missing block/slide ids with short stable keys (in place).

    Editors need stable keys for reordering and partial updates; documents
    straight from an LLM or 0.1.x files have none."""
    import secrets

    def key() -> str:
        return secrets.token_urlsafe(6)

    for slide in doc.slides:
        slide.id = slide.id or key()
        for block in slide.blocks + slide.right:
            block.id = block.id or key()
    for block in doc.blocks:
        block.id = block.id or key()
    return doc


# ------------------------------------------------------- shared render utils


def flatten_slides(slides: list[Slide]) -> list[Block]:
    """Turn slides into report blocks, so report renderers can handle
    deck-only documents instead of failing."""
    blocks: list[Block] = []
    for s in slides:
        if s.title:
            level = 1 if s.layout in ("title", "section") else 2
            blocks.append(Heading(level=level, text=s.title))
        if s.subtitle:
            blocks.append(Paragraph(text=[Span(text=s.subtitle, italic=True)]))
        if s.image is not None:
            blocks.append(s.image)
        blocks.extend(s.blocks)
        blocks.extend(s.right)
    return blocks


def report_blocks(doc: Document) -> list[Block]:
    """Blocks for report renderers: doc.blocks, else flattened slides."""
    return doc.blocks or flatten_slides(doc.slides)


def source_numbers(doc: Document) -> dict[str, int]:
    """Stable 1-based numbering for citations, in sources order.

    Duplicate ids keep the first number (the linter flags duplicates)."""
    numbers: dict[str, int] = {}
    for s in doc.sources:
        numbers.setdefault(s.id, len(numbers) + 1)
    return numbers


def normalize_table(
    header: list[RichText], rows: list[list[RichText]]
) -> tuple[list[RichText], list[list[RichText]]]:
    """Pad a possibly-ragged table so header and every row share the same
    width (the widest of any of them). All renderers use this so no format
    silently drops cells."""
    ncols = max(len(header), max((len(r) for r in rows), default=0))
    padded_header = list(header) + [""] * (ncols - len(header))
    padded_rows = [list(r) + [""] * (ncols - len(r)) for r in rows]
    return padded_header, padded_rows


def cited_ids(doc: Document) -> set[str]:
    """All Source ids actually referenced by a Span.cite anywhere."""
    ids: set[str] = set()

    def walk_rt(rt: RichText) -> None:
        for sp in spans(rt):
            if sp.cite:
                ids.add(sp.cite)

    def walk_blocks(blocks: list[Block]) -> None:
        for b in blocks:
            if isinstance(b, (Heading, Paragraph, Quote, Callout)):
                walk_rt(b.text)
            elif isinstance(b, (BulletList, NumberedList)):
                for it in b.items:
                    walk_rt(it.text)
            elif isinstance(b, Table):
                for cell in b.header:
                    walk_rt(cell)
                for row in b.rows:
                    for cell in row:
                        walk_rt(cell)

    walk_blocks(doc.blocks)
    for s in doc.slides:
        walk_blocks(s.blocks)
        walk_blocks(s.right)
    return ids
