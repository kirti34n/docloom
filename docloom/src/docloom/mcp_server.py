"""MCP server exposing docloom to agents: schema, lint, render.

Run with `docloom-mcp` (stdio transport). Agents call get_document_schema,
emit a document, lint it, self-correct against the findings, then render.
"""

from __future__ import annotations

import json
from pathlib import Path

from .lint import has_errors, lint
from .llm import AUTHORING_GUIDE, llm_schema, parse_llm_output
from .render import FORMATS, render, slug
from .theme import DEFAULT, Theme


def _lint_document(document_json: str, theme_json: str = "") -> str:
    doc = parse_llm_output(document_json)
    theme = Theme.model_validate_json(theme_json) if theme_json else DEFAULT
    return json.dumps([f.model_dump() for f in lint(doc, theme)])


def _render_document(
    document_json: str,
    formats: str = "pptx",
    out_dir: str = ".",
    theme_json: str = "",
    no_lint: bool = False,
) -> str:
    doc = parse_llm_output(document_json)
    theme = Theme.model_validate_json(theme_json) if theme_json else DEFAULT
    fmts = [f.strip() for f in formats.split(",") if f.strip()]
    unknown = [f for f in fmts if f not in FORMATS]
    if unknown:
        raise ValueError(
            f"unknown format(s) {unknown}; expected a subset of {sorted(FORMATS)}"
        )
    findings = lint(doc, theme)
    if not no_lint and has_errors(findings):
        errors = "; ".join(
            f"[{f.rule}] {f.where}: {f.message}"
            for f in findings if f.severity == "error"
        )
        raise ValueError(f"refusing to render with lint errors: {errors}")
    out = Path(out_dir).resolve()  # server cwd is unknown to the caller
    paths = []
    for fmt in fmts:
        path = render(doc, fmt, out / (slug(doc.title) + FORMATS[fmt][1]), theme)
        paths.append(str(path))
    return json.dumps(paths)


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise SystemExit("docloom-mcp needs the mcp package: pip install 'docloom[mcp]'")

    mcp = FastMCP("docloom")

    @mcp.tool()
    def get_document_schema() -> str:
        """Get the JSON Schema for docloom documents plus authoring rules.

        Call this first. Then author a document as JSON matching the schema,
        lint it with lint_document, and render it with render_document.
        """
        return json.dumps({"schema": llm_schema(), "authoring_guide": AUTHORING_GUIDE})

    @mcp.tool()
    def lint_document(document_json: str, theme_json: str = "") -> str:
        """Validate and lint a docloom document.

        theme_json: optional theme override (colors as #RRGGBB, font names) --
        pass the same theme_json you intend to give render_document, so
        theme-contrast errors are caught here first.
        Returns a JSON list of findings (rule, severity, where, message).
        An empty list means the document is clean. Fix every "error" before
        rendering; treat "warning" as strong advice.
        """
        return _lint_document(document_json, theme_json)

    @mcp.tool()
    def render_document(
        document_json: str,
        formats: str = "pptx",
        out_dir: str = ".",
        theme_json: str = "",
        no_lint: bool = False,
    ) -> str:
        """Render a docloom document to native files.

        formats: comma-separated subset of pptx,docx,xlsx,pdf,html,md.
        theme_json: optional theme override (colors as #RRGGBB, font names).
        no_lint: render even when the linter reports errors (default False --
        by default this refuses to render, and writes nothing, if lint finds
        an "error"-severity finding against the resolved theme).
        Returns a JSON list of written file paths.
        """
        return _render_document(document_json, formats, out_dir, theme_json, no_lint)

    mcp.run()


if __name__ == "__main__":
    main()
