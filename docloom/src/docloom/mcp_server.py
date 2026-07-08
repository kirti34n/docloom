"""MCP server exposing docloom to agents: schema, lint, render.

Run with `docloom-mcp` (stdio transport). Agents call get_document_schema,
emit a document, lint it, self-correct against the findings, then render.
"""

from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise SystemExit("docloom-mcp needs the mcp package: pip install 'docloom[mcp]'")

    from .ir import Document
    from .lint import lint
    from .llm import AUTHORING_GUIDE, llm_schema
    from .render import FORMATS, render
    from .theme import DEFAULT, Theme

    mcp = FastMCP("docloom")

    @mcp.tool()
    def get_document_schema() -> str:
        """Get the JSON Schema for docloom documents plus authoring rules.

        Call this first. Then author a document as JSON matching the schema,
        lint it with lint_document, and render it with render_document.
        """
        return json.dumps({"schema": llm_schema(), "authoring_guide": AUTHORING_GUIDE})

    @mcp.tool()
    def lint_document(document_json: str) -> str:
        """Validate and lint a docloom document.

        Returns a JSON list of findings (rule, severity, where, message).
        An empty list means the document is clean. Fix every "error" before
        rendering; treat "warning" as strong advice.
        """
        doc = Document.model_validate_json(document_json)
        return json.dumps([f.model_dump() for f in lint(doc, DEFAULT)])

    @mcp.tool()
    def render_document(
        document_json: str,
        formats: str = "pptx",
        out_dir: str = ".",
        theme_json: str = "",
    ) -> str:
        """Render a docloom document to native files.

        formats: comma-separated subset of pptx,docx,xlsx,pdf,html,md.
        theme_json: optional theme override (colors as #RRGGBB, font names).
        Returns a JSON list of written file paths.
        """
        from .render import slug

        doc = Document.model_validate_json(document_json)
        theme = Theme.model_validate_json(theme_json) if theme_json else DEFAULT
        fmts = [f.strip() for f in formats.split(",") if f.strip()]
        unknown = [f for f in fmts if f not in FORMATS]
        if unknown:
            raise ValueError(
                f"unknown format(s) {unknown}; expected a subset of {sorted(FORMATS)}"
            )
        out = Path(out_dir)
        paths = []
        for fmt in fmts:
            path = render(doc, fmt, out / (slug(doc.title) + FORMATS[fmt][1]), theme)
            paths.append(str(path))
        return json.dumps(paths)

    mcp.run()


if __name__ == "__main__":
    main()
