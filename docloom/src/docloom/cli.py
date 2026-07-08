"""docloom command line: render, lint, schema, guide."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .ir import Document
from .lint import has_errors, lint
from .llm import AUTHORING_GUIDE, llm_schema
from .render import FORMATS, RenderError, render, slug
from .theme import DEFAULT, Theme


def _load(doc_path: str, theme_path: str | None) -> tuple[Document, Theme]:
    return Document.load(doc_path), Theme.load(theme_path) if theme_path else DEFAULT


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="docloom",
        description="Render validated document JSON to PPTX/DOCX/XLSX/PDF/HTML/MD.",
    )
    p.add_argument("--version", action="version", version=f"docloom {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="render a document to one or more formats")
    r.add_argument("doc", help="path to document JSON")
    r.add_argument(
        "-f", "--formats", default="pptx",
        help=f"comma-separated formats: {','.join(FORMATS)} (default: pptx)",
    )
    r.add_argument("-o", "--out", default=".", help="output directory")
    r.add_argument("--theme", help="path to theme JSON")
    r.add_argument(
        "--no-lint", action="store_true",
        help="render even when the linter reports errors",
    )

    li = sub.add_parser("lint", help="lint a document, exit 1 on errors")
    li.add_argument("doc", help="path to document JSON")
    li.add_argument("--theme", help="path to theme JSON")
    li.add_argument("--json", action="store_true", help="machine-readable output")

    sub.add_parser("schema", help="print the LLM-ready JSON schema for Document")
    sub.add_parser("guide", help="print the authoring guide (LLM system prompt)")

    t = sub.add_parser("theme", help="print the default theme JSON (edit and pass via --theme)")

    args = p.parse_args(argv)

    if args.cmd == "schema":
        print(json.dumps(llm_schema(), indent=2))
        return 0
    if args.cmd == "guide":
        print(AUTHORING_GUIDE)
        return 0
    if args.cmd == "theme":
        print(json.dumps(DEFAULT.model_dump(), indent=2))
        return 0

    try:
        doc, theme = _load(args.doc, args.theme)
    except FileNotFoundError as e:
        print(f"file not found: {e.filename or e}", file=sys.stderr)
        return 2
    except Exception as e:  # malformed JSON / schema-invalid document or theme
        print(f"invalid document or theme: {e}", file=sys.stderr)
        return 2
    findings = lint(doc, theme)

    if args.cmd == "lint":
        if args.json:
            print(json.dumps([f.model_dump() for f in findings], indent=2))
        else:
            for f in findings:
                print(f"{f.severity:8} [{f.rule}] {f.where}: {f.message}")
            print(f"{len(findings)} finding(s)")
        return 1 if has_errors(findings) else 0

    # render
    for f in findings:
        print(f"lint: {f.severity} [{f.rule}] {f.where}: {f.message}", file=sys.stderr)
    if has_errors(findings) and not args.no_lint:
        print("refusing to render with lint errors (use --no-lint to override)",
              file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    code = 0
    for fmt in [f.strip() for f in args.formats.split(",") if f.strip()]:
        try:
            path = render(doc, fmt, out_dir / (slug(doc.title) + FORMATS[fmt][1]), theme)
            print(f"wrote {path}")
        except KeyError:
            print(f"unknown format {fmt!r}; expected one of {sorted(FORMATS)}",
                  file=sys.stderr)
            code = 2
        except RenderError as e:
            print(f"{fmt}: {e}", file=sys.stderr)
            code = 2
    return code


if __name__ == "__main__":
    sys.exit(main())
