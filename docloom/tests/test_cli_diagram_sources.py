"""Security regression tests for cli.py's _write_diagram_sources (finding 2,
docs/diagram-status.md): arbitrary file write from LLM-authored content.

`Diagram.id` is a `SafeStr`, which only guards against script/HTML
injection. It happily permits '/', '\\', ':', and '..' segments, so
`path = out / f"{d.id or i}.drawio"` let an attacker-controlled document
write anywhere the filesystem permission model allowed:

- an absolute path (a drive letter on Windows, e.g. "C:/Windows/Temp/evil")
  resolved completely outside the sidecar directory.
- a "../" prefix traversed out of the sidecar directory.

Same function had two lesser bugs from the same finding: it caught only
OSError, so a malformed diagram (solve() raising ValueError/KeyError, e.g.
a dangling edge rendered with --no-lint) crashed the whole CLI with a raw
traceback instead of degrading gracefully; and `d.id or i` collided
silently when two diagrams shared an id, so one diagram's file silently
overwrote another's.

Each test below reproduces the exploit/bug as it existed before the fix
(see the file history / diagram-status.md finding 2 for the pre-fix
behavior verified against this exact reproduction) and asserts the fixed
behavior: contained, handled, de-duplicated.
"""

from __future__ import annotations

import json
from pathlib import Path

from docloom import cli


def _write_doc(tmp_path: Path, blocks: list[dict], name: str = "doc.json") -> Path:
    doc = {"title": "Diagram Sources Security Test", "blocks": blocks}
    p = tmp_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _diagram_block(diagram_id, nodes=None, edges=None) -> dict:
    block = {
        "type": "diagram",
        "caption": "how it fits together",
        "nodes": nodes if nodes is not None else [
            {"id": "a", "label": "A"}, {"id": "b", "label": "B", "type": "store"},
        ],
        "edges": edges if edges is not None else [
            {"source": "a", "target": "b", "label": "writes"},
        ],
    }
    if diagram_id is not None:
        block["id"] = diagram_id
    return block


def _sidecar_dir(out_dir: Path) -> Path:
    return out_dir / "diagram-sources-security-test.diagrams"


# ---------------------------------------------------------------------------
# Exploit 1: absolute path (drive letter) escapes the sidecar dir entirely.
# ---------------------------------------------------------------------------


def test_absolute_drive_letter_id_is_contained(tmp_path):
    evil_dir = tmp_path / "evil_target"
    evil_dir.mkdir()
    evil_id = (evil_dir / "evil").as_posix()  # e.g. "C:/.../evil_target/evil"
    assert ":" in evil_id  # sanity: this is a drive-letter absolute path

    doc_path = _write_doc(tmp_path, [_diagram_block(evil_id)])
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0

    # the exploit's target must stay untouched
    assert list(evil_dir.iterdir()) == []

    # exactly one file was written, and it lives inside the sidecar dir
    sidecar = _sidecar_dir(out_dir)
    assert sidecar.is_dir()
    written = list(sidecar.iterdir())
    assert len(written) == 1
    assert written[0].resolve().parent == sidecar.resolve()


# ---------------------------------------------------------------------------
# Exploit 2: "../" traversal escapes the sidecar dir.
# ---------------------------------------------------------------------------


def test_dot_dot_traversal_id_is_contained(tmp_path):
    doc_path = _write_doc(tmp_path, [_diagram_block("../../../../pwned")])
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0

    # nothing named "pwned" escaped anywhere above the sidecar dir
    escaped = [p for p in tmp_path.rglob("*pwned*")
               if p.resolve().parent != _sidecar_dir(out_dir).resolve()]
    assert escaped == []

    sidecar = _sidecar_dir(out_dir)
    assert sidecar.is_dir()
    written = list(sidecar.iterdir())
    assert len(written) == 1
    assert written[0].resolve().parent == sidecar.resolve()


def test_dot_dot_traversal_id_does_not_write_outside_repo_root(tmp_path):
    # A second traversal shape: relative "../" without the tmp_path prefix
    # trick, confirming resolve()-based containment (not string matching).
    doc_path = _write_doc(tmp_path, [_diagram_block("../escape")])
    out_dir = tmp_path / "nested" / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0
    sidecar = _sidecar_dir(out_dir)
    written = list(sidecar.iterdir())
    assert len(written) == 1
    assert written[0].resolve().parent == sidecar.resolve()
    # confirm it did NOT land in out_dir's parent ("nested")
    assert not (out_dir.parent / "escape.drawio").exists()


# ---------------------------------------------------------------------------
# Lesser bug 1: solve() raising ValueError/KeyError must not crash the CLI.
# ---------------------------------------------------------------------------


def test_malformed_diagram_degrades_gracefully_instead_of_crashing(tmp_path):
    # a dangling edge is a lint error normally, but --no-lint bypasses lint
    # and hands the malformed diagram straight to solve(), which raises
    # KeyError for an edge endpoint that names no node.
    bad = _diagram_block(
        "bad",
        nodes=[{"id": "a", "label": "A"}],
        edges=[{"source": "a", "target": "does-not-exist"}],
    )
    doc_path = _write_doc(tmp_path, [bad])
    out_dir = tmp_path / "out"

    # must not raise
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir),
        "--no-lint", "--diagram-sources",
    ])

    # degrades gracefully: reported via a non-zero exit, not a traceback,
    # and the rest of the render (the .md file) still happened
    assert code == 2
    assert (out_dir / "diagram-sources-security-test.md").is_file()
    # no sidecar was produced for the one, sole, malformed diagram
    sidecar = _sidecar_dir(out_dir)
    assert not sidecar.exists() or list(sidecar.iterdir()) == []


def test_one_malformed_diagram_does_not_block_a_sibling_valid_one(tmp_path):
    bad = _diagram_block(
        "bad",
        nodes=[{"id": "a", "label": "A"}],
        edges=[{"source": "a", "target": "does-not-exist"}],
    )
    good = _diagram_block("good")
    doc_path = _write_doc(tmp_path, [bad, good])
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir),
        "--no-lint", "--diagram-sources",
    ])
    assert code == 2  # the malformed one is still reported
    sidecar = _sidecar_dir(out_dir)
    assert (sidecar / "good.drawio").is_file()  # but its sibling still wrote


# ---------------------------------------------------------------------------
# Lesser bug 2: two diagrams sharing an id must not silently collide.
# ---------------------------------------------------------------------------


def test_duplicate_ids_do_not_collide(tmp_path):
    first = _diagram_block(
        "dup",
        nodes=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        edges=[{"source": "a", "target": "b", "label": "first"}],
    )
    second = _diagram_block(
        "dup",
        nodes=[{"id": "x", "label": "X"}, {"id": "y", "label": "Y"}],
        edges=[{"source": "x", "target": "y", "label": "second"}],
    )
    doc_path = _write_doc(tmp_path, [first, second])
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0

    sidecar = _sidecar_dir(out_dir)
    names = sorted(p.name for p in sidecar.iterdir())
    # two distinct files, neither overwrote the other
    assert len(names) == 2
    assert "dup.drawio" in names

    # the two files carry each diagram's own content: "first"/"second"
    # never both end up in the same file
    contents = [(sidecar / n).read_text(encoding="utf-8") for n in names]
    has_first = ["first" in c for c in contents]
    has_second = ["second" in c for c in contents]
    assert has_first.count(True) == 1
    assert has_second.count(True) == 1
    # and no single file contains both edge labels (proof one did not
    # clobber and then get overwritten leaving a merged/corrupted read)
    assert not any(("first" in c and "second" in c) for c in contents)


def test_ids_that_sanitize_to_the_same_slug_also_do_not_collide(tmp_path):
    # "a/b" and "a-b" both sanitize (via slug()) to "a-b"; they must still
    # land in two distinct files.
    first = _diagram_block(
        "a/b",
        nodes=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        edges=[{"source": "a", "target": "b", "label": "slash-variant"}],
    )
    second = _diagram_block(
        "a-b",
        nodes=[{"id": "x", "label": "X"}, {"id": "y", "label": "Y"}],
        edges=[{"source": "x", "target": "y", "label": "dash-variant"}],
    )
    doc_path = _write_doc(tmp_path, [first, second])
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0
    sidecar = _sidecar_dir(out_dir)
    assert len(list(sidecar.iterdir())) == 2


# ---------------------------------------------------------------------------
# Sanity: the ordinary, non-malicious path still works exactly as before.
# ---------------------------------------------------------------------------


def test_normal_id_still_produces_the_expected_plain_filename(tmp_path):
    doc_path = _write_doc(tmp_path, [_diagram_block("arch1")])
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0
    assert (_sidecar_dir(out_dir) / "arch1.drawio").is_file()


def test_missing_id_still_falls_back_to_the_bare_index(tmp_path):
    doc_path = _write_doc(tmp_path, [_diagram_block(None)])
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0
    assert (_sidecar_dir(out_dir) / "0.drawio").is_file()
