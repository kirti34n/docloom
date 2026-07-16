"""docloom command line: render, lint, schema, guide."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .ir import Diagram, Document
from .lint import has_errors, lint
from .llm import AUTHORING_GUIDE, llm_schema
from .render import FORMATS, RenderError, render, slug
from .render.diagram_svg import solve
from .render.drawio import render_drawio
from .theme import DEFAULT, Theme


def _load(doc_path: str, theme_path: str | None) -> tuple[Document, Theme]:
    return Document.load(doc_path), Theme.load(theme_path) if theme_path else DEFAULT


def _theme_dict(theme: Theme) -> dict[str, str]:
    """Adapt a docloom.theme.Theme to the plain dict overlay
    render/diagram_svg.py and render/drawio.py both take (docs/diagram-
    plan.md section 3: "the docloom Theme model is adapted by callers")."""
    return {
        "primary": theme.primary, "accent": theme.accent,
        "surface": theme.surface, "text": theme.text,
        "muted": theme.muted, "background": theme.background,
    }


def _iter_diagrams(doc: Document):
    """Every Diagram block in the document, in document order: doc.blocks
    (report path) first, then every slide's blocks and right column (deck
    path) -- a document may carry both."""
    for b in doc.blocks:
        if isinstance(b, Diagram):
            yield b
    for s in doc.slides:
        for b in s.blocks:
            if isinstance(b, Diagram):
                yield b
        for b in s.right:
            if isinstance(b, Diagram):
                yield b


def _diagram_filenames(diagrams: list[Diagram]) -> list[str]:
    """Sanitize each Diagram's id into a safe filename stem, then
    de-duplicate deterministically.

    SafeStr only guards against script/HTML injection; it happily permits
    '/', '\\', ':', and '..' segments, so an LLM-authored id such as
    "C:/Windows/Temp/evil" or "../../../pwned" must never reach the
    filesystem verbatim (this repo has already fixed two Windows path
    escapes and a theme path traversal; this is the same class of bug
    again). slug() keeps only \\w characters (collapsing everything else to
    "-"), so its output can never contain a path separator or a drive
    letter; _write_diagram_sources still asserts containment afterwards as
    defense in depth, since a containment check on our own directory is a
    correct guard even though it would not be a substitute for an
    authorization check elsewhere.
    """
    seen: dict[str, int] = {}
    names = []
    for i, d in enumerate(diagrams):
        base = slug(d.id) if d.id else str(i)
        n = seen.get(base, 0)
        seen[base] = n + 1
        # First occurrence keeps the clean name (preserves the existing
        # "id.drawio" / "index.drawio" naming for the common case). A
        # colliding id -- two diagrams sharing an id, or two different ids
        # that sanitize to the same slug -- gets its index appended so one
        # diagram's file never silently overwrites another's.
        names.append(base if n == 0 else f"{base}-{i}")
    return names


def _write_diagram_sources(
    doc: Document, theme: Theme, out_dir: Path, stem: str,
) -> tuple[list[Path], bool]:
    """--diagram-sources: write a .drawio sidecar file for every Diagram
    block, next to the rendered output. One-way DERIVED export
    (docs/diagram-plan.md section 1's Tier 1/Tier 2 contract): docloom never
    reads a .drawio file back, so editing one in draw.io is a fork that a
    future render overwrites, it does not merge.

    Returns (written_paths, had_errors). A malformed diagram (dangling edge,
    unknown group id, ...) makes solve() raise ValueError or KeyError; lint
    normally rejects those before render, but --no-lint bypasses lint, so
    that diagram is skipped with a stderr warning here instead of crashing
    the whole CLI with a raw traceback. had_errors lets the caller report a
    non-zero exit without losing the diagrams that DID render.
    """
    diagrams = list(_iter_diagrams(doc))
    if not diagrams:
        return [], False
    out = out_dir / f"{stem}.diagrams"
    out.mkdir(parents=True, exist_ok=True)
    out_resolved = out.resolve()
    t = _theme_dict(theme)
    names = _diagram_filenames(diagrams)

    written: list[Path] = []
    had_errors = False
    for i, d in enumerate(diagrams):
        try:
            solved = solve(d, t)
            xml = render_drawio(d, solved, t)
        except (ValueError, KeyError) as e:
            print(f"diagram-sources: skipping diagram {i} "
                  f"(id={d.id!r}): {e}", file=sys.stderr)
            had_errors = True
            continue
        path = out / f"{names[i]}.drawio"
        # Defense in depth: names[i] came from slug(), which cannot contain
        # a path separator or drive letter, so this resolves inside `out`
        # by construction. Assert it anyway rather than trust that
        # invariant silently forever.
        resolved = path.resolve()
        if resolved.parent != out_resolved:
            print(f"diagram-sources: refusing to write outside {out_resolved} "
                  f"(diagram {i}, id={d.id!r})", file=sys.stderr)
            had_errors = True
            continue
        path.write_text(xml, encoding="utf-8")
        written.append(path)
    return written, had_errors


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
    r.add_argument(
        "--diagram-sources", action="store_true",
        help="also write a .drawio sidecar for every Diagram block, in "
             "{stem}.diagrams/ next to the output (one-way derived export; "
             "docloom never reads .drawio files back)",
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

    if args.diagram_sources:
        try:
            written, had_errors = _write_diagram_sources(doc, theme, out_dir, slug(doc.title))
            for path in written:
                print(f"wrote {path}")
            if had_errors:
                code = 2
        except OSError as e:
            print(f"diagram-sources: {e}", file=sys.stderr)
            code = 2

    return code


if __name__ == "__main__":
    sys.exit(main())
