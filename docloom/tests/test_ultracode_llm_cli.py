"""Regression tests for the ultracode llm-cli-mcp fix pass.

Finding 1 (llm.py): a non-string block `type` tag (dict/list) crashed
parse_llm_output with a raw TypeError instead of the documented
self-correctable ValueError.

Finding 2 (cli.py): _diagram_filenames could mint the same filename for two
different diagrams when a later diagram's id slug collided with an earlier
diagram's *minted* "{base}-{i}" name, silently overwriting one diagram's
file with another's.

Finding 3 (mcp_server.py): lint_document always linted the DEFAULT theme
even when render_document was given a theme_json override, so the
theme/low-contrast rule could never fire for the theme actually rendered,
and render_document never refused to render lint-error documents at all.
"""

from __future__ import annotations

import json

import pytest

from docloom import cli
from docloom.llm import parse_llm_output
from docloom.mcp_server import _lint_document, _render_document


# ---------------------------------------------------------------------------
# Finding 1: non-string block `type` tag must raise ValueError, not TypeError.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "document_json",
    [
        # dict type tag, reached via Document.blocks
        json.dumps({"title": "T", "blocks": [{"type": {"a": 1}, "text": "x"}]}),
        # list type tag, reached via Document.blocks
        json.dumps({"title": "T", "blocks": [{"type": ["heading"], "text": "x"}]}),
        # dict type tag, reached via Slide.right (a different _BLOCK_LIST_KEYS path)
        json.dumps({
            "title": "T",
            "slides": [{"right": [{"type": {"z": 0}, "text": "x"}]}],
        }),
    ],
)
def test_non_string_block_type_tag_raises_value_error_not_type_error(document_json):
    with pytest.raises(ValueError):
        parse_llm_output(document_json)


# ---------------------------------------------------------------------------
# Finding 2: minted "{base}-{i}" names must not collide with a later
# diagram's own slug.
# ---------------------------------------------------------------------------


def test_diagram_filenames_unique_when_a_later_id_collides_with_a_minted_name():
    from docloom.cli import _diagram_filenames
    from docloom.ir import Diagram

    diagrams = [
        Diagram(id="dup", nodes=[], edges=[]),
        Diagram(id="dup", nodes=[], edges=[]),
        Diagram(id="dup-1", nodes=[], edges=[]),
    ]
    names = _diagram_filenames(diagrams)
    assert len(names) == len(set(names)) == 3


def _write_doc(tmp_path, blocks: list[dict], name: str = "doc.json"):
    doc = {"title": "Ultracode Filename Collision Test", "blocks": blocks}
    p = tmp_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _diagram_block(diagram_id, label: str) -> dict:
    block = {
        "type": "diagram",
        "caption": label,
        "nodes": [
            {"id": "a", "label": "A"}, {"id": "b", "label": "B"},
        ],
        "edges": [
            {"source": "a", "target": "b", "label": label},
        ],
    }
    if diagram_id is not None:
        block["id"] = diagram_id
    return block


def test_three_diagrams_do_not_silently_overwrite_via_minted_name_collision(tmp_path):
    blocks = [
        _diagram_block("dup", "FIRST"),
        _diagram_block("dup", "SECOND"),
        _diagram_block("dup-1", "THIRD"),
    ]
    doc_path = _write_doc(tmp_path, blocks)
    out_dir = tmp_path / "out"
    code = cli.main([
        "render", str(doc_path), "-f", "md", "-o", str(out_dir), "--diagram-sources",
    ])
    assert code == 0

    sidecar = out_dir / "ultracode-filename-collision-test.diagrams"
    written = list(sidecar.iterdir())
    assert len(written) == 3

    contents = [p.read_text(encoding="utf-8") for p in written]
    for label in ("FIRST", "SECOND", "THIRD"):
        assert sum(label in c for c in contents) == 1


# ---------------------------------------------------------------------------
# Finding 3: lint_document must lint the theme it is given, and
# render_document must refuse (and write nothing) for a theme with lint
# errors unless no_lint=True.
# ---------------------------------------------------------------------------


DOC = json.dumps({
    "title": "Q3",
    "slides": [{"layout": "title", "title": "Q3 Review"}],
})
BAD_THEME = json.dumps({
    "text": "#FFFFFF", "background": "#FFFFFF", "surface": "#FEFEFE",
})


def test_lint_document_sees_the_theme_override():
    assert json.loads(_lint_document(DOC)) == []

    findings = json.loads(_lint_document(DOC, BAD_THEME))
    assert any(
        f["rule"] == "theme/low-contrast" and f["severity"] == "error"
        for f in findings
    )


def test_render_document_refuses_unreadable_theme(tmp_path):
    with pytest.raises(ValueError):
        _render_document(DOC, out_dir=str(tmp_path), theme_json=BAD_THEME)
    assert list(tmp_path.glob("*.pptx")) == []

    result = _render_document(
        DOC, out_dir=str(tmp_path), theme_json=BAD_THEME, no_lint=True
    )
    paths = json.loads(result)
    assert len(paths) == 1
    assert list(tmp_path.glob("*.pptx"))
