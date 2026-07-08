"""docloom — the document output layer for AI apps.

Your LLM emits a validated JSON document (via structured output); docloom
deterministically renders it to PPTX, DOCX, XLSX, PDF, HTML, or Markdown,
and lints it for the failures generated documents actually ship with.
"""

from .ir import (
    Artifact, Block, BulletList, Callout, Cell, Chart, Code, Column, Divider,
    Document, Formula, Heading, Image, ListItem, NumberedList, Paragraph,
    Quote, RichText, Series, Sheet, Slide, Source, Span, Stat, StatRow,
    Table, ensure_ids,
)
from .lint import Finding, has_errors, lint
from .llm import AUTHORING_GUIDE, llm_schema, parse_llm_output
from .render import FORMATS, RenderError, render
from .theme import DEFAULT, Theme

__version__ = "0.2.0"

__all__ = [
    "Artifact", "Block", "BulletList", "Callout", "Cell", "Chart", "Code",
    "Column", "Divider", "Document", "Formula", "Heading", "Image",
    "ListItem", "NumberedList", "Paragraph", "Quote", "RichText", "Series",
    "Sheet", "Slide", "Source", "Span", "Stat", "StatRow", "Table",
    "ensure_ids", "Finding", "lint", "has_errors", "render", "RenderError",
    "FORMATS", "Theme", "DEFAULT", "llm_schema", "parse_llm_output",
    "AUTHORING_GUIDE", "__version__",
]
