"""Measured text fitting for the PPTX renderer.

python-pptx's TEXT_TO_FIT_SHAPE emits a bare <a:normAutofit/> that only
desktop PowerPoint ever recomputes (and only on click-to-edit); every other
renderer draws the authored run sizes and overflows. This module measures
real wrapped text extents against the real font files so pptx.py can bake a
fitted size into the runs themselves. Pure functions; every failure path
(missing Pillow, unresolvable font family, corrupt font file) degrades to a
deliberately-small per-family estimate instead of raising."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

REF_PT = 200                # load each face once at 200pt; TrueType advances scale linearly
FALLBACK_ADVANCE_EM = 0.45  # err-SMALL avg advance when no real face resolves (see below)
FALLBACK_LINE_EM = 1.2      # err-small single-space line height for the same fallback
LNSPC_STEPS = (0.0, 0.10, 0.20)  # PowerPoint burns up to 20% line-spacing before more font shrink
EPS_PT = 0.75               # fit tolerance: within a hair of the box counts as fitting


@dataclass(frozen=True)
class RunSpec:
    text: str
    family: str
    size_pt: float
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True)
class ParaSpec:
    runs: tuple[RunSpec, ...]
    space_after_pt: float = 0.0
    first_indent_in: float = 0.0  # max(0, marL + indent) in inches (sources hang-indent)
    cont_indent_in: float = 0.0   # max(0, marL) in inches


@dataclass(frozen=True)
class FitResult:
    scale: float            # 1.0 == no shrink; multiply every run's size by this
    lnspc_reduction: float  # 0.0 / 0.10 / 0.20 -> paragraph line_spacing = 1 - r
    fits: bool              # False: clamped at the floor and STILL overflows (caller warns)


@lru_cache(maxsize=64)
def _face(family: str, bold: bool, italic: bool):
    """PIL FreeTypeFont at REF_PT, or None (Pillow missing / family unresolvable)."""
    try:
        from PIL import ImageFont                # Pillow is not a declared docloom dep
        from pptx.text.fonts import FontFiles    # private but present in pptx 1.0.2 (verified)
    except Exception:
        return None
    for b, i in ((bold, italic), (bold, False), (False, italic), (False, False)):
        try:
            return ImageFont.truetype(FontFiles.find(family, b, i), REF_PT)
        except Exception:   # KeyError from find(), OSError from truetype()
            continue
    return None


def _run_width_pt(text: str, run: RunSpec, scale: float) -> float:
    face = _face(run.family, run.bold, run.italic)
    size = run.size_pt * scale
    if face is None:
        return len(text) * size * FALLBACK_ADVANCE_EM
    return face.getlength(text) / REF_PT * size


def _line_height_pt(run: RunSpec, scale: float, reduction: float) -> float:
    face = _face(run.family, run.bold, run.italic)
    if face is None:
        em = FALLBACK_LINE_EM
    else:
        ascent, descent = face.getmetrics()
        em = (ascent + descent) / REF_PT
    return run.size_pt * scale * em * (1.0 - reduction)


def _tokenize(p: ParaSpec) -> list[list[tuple[str, RunSpec]]]:
    """Split a paragraph into hard-break-separated lines of (word, run) pairs,
    each carrying whether a space preceded it (spaces at line starts are
    dropped, matching PowerPoint)."""
    lines: list[list[tuple[str, RunSpec]]] = [[]]
    for run in p.runs:
        segments = run.text.replace("\x0b", "\n").split("\n")
        for seg_i, seg in enumerate(segments):
            if seg_i > 0:
                lines.append([])  # hard break forces a new line
            for word in seg.split():
                lines[-1].append((word, run))
    return lines


def _para_height_pt(p: ParaSpec, width_pt: float, scale: float, reduction: float) -> float:
    if not p.runs:
        return 0.0
    max_size_run = max(p.runs, key=lambda r: r.size_pt)
    hard_lines = _tokenize(p)
    if all(not ln for ln in hard_lines):
        # no words at all: one line at the paragraph's largest run size
        return _line_height_pt(max_size_run, scale, reduction) + p.space_after_pt

    usable_first = max(18.0, width_pt - p.first_indent_in * 72)
    usable_cont = max(18.0, width_pt - p.cont_indent_in * 72)

    line_heights: list[float] = []
    is_first_line = True
    for hard_line in hard_lines:
        if not hard_line:
            # empty hard-break segment still occupies a line
            line_heights.append(_line_height_pt(max_size_run, scale, reduction))
            is_first_line = False
            continue
        cur_words: list[tuple[str, RunSpec]] = []
        cur_w = 0.0
        usable = usable_first if is_first_line else usable_cont

        def _flush():
            nonlocal cur_words, cur_w
            if cur_words:
                dominant = max((r for _, r in cur_words), key=lambda r: r.size_pt)
                line_heights.append(_line_height_pt(dominant, scale, reduction))
            cur_words, cur_w = [], 0.0

        for word, run in hard_line:
            ww = _run_width_pt(word, run, scale)
            if ww > usable:
                # single word wider than the line: flush what's pending, then
                # let this overlong word occupy ceil(w / usable) lines of its
                # own (PowerPoint character-wraps overlong words when
                # word_wrap is on)
                _flush()
                n = max(1, math.ceil(ww / usable))
                for _ in range(n):
                    line_heights.append(_line_height_pt(run, scale, reduction))
                is_first_line = False
                usable = usable_cont
                continue
            sw = ww + (_run_width_pt(" ", run, scale) if cur_words else 0.0)
            if cur_words and cur_w + sw > usable:
                _flush()
                is_first_line = False
                usable = usable_cont
                sw = ww  # first word on the new line: no leading space
            cur_words.append((word, run))
            cur_w += sw
        _flush()
        is_first_line = False

    return sum(line_heights) + p.space_after_pt


def required_height_pt(paras: Sequence[ParaSpec], width_in: float,
                       scale: float, reduction: float) -> float:
    width_pt = width_in * 72.0
    return sum(_para_height_pt(p, width_pt, scale, reduction) for p in paras)


def fit_scale(paras: Sequence[ParaSpec], width_in: float, height_in: float,
              min_pt: float = 9.0) -> FitResult:
    avail = height_in * 72.0
    if not paras or all(not p.runs or not any(r.text.strip() for r in p.runs) for p in paras):
        return FitResult(1.0, 0.0, True)
    if required_height_pt(paras, width_in, 1.0, 0.0) <= avail + EPS_PT:
        return FitResult(1.0, 0.0, True)

    M = max(r.size_pt for p in paras for r in p.runs)
    # The same scale multiplies every run in the frame uniformly, so the
    # floor must be derived from the smallest run that was authored AT OR
    # ABOVE min_pt -- bounding only the largest run's descent (the old
    # M-based floor) let any smaller-but-still-legible run in the same
    # frame get baked below min_pt once the shared scale applied.
    #
    # Runs authored BELOW min_pt to begin with (a citation superscript is
    # deliberately small typography, not a legibility bug) must be excluded
    # from this: including them makes min_pt / small > 1, and the
    # min(1.0, ...) clamp below then pins floor_scale at 1.0 -- disabling
    # autofit for the ENTIRE frame, body text included, just because one
    # run was already smaller than the floor by design. Such runs still
    # scale proportionally with everything else; they just don't get a say
    # in how far the frame is allowed to shrink.
    protected = [r.size_pt for p in paras for r in p.runs if r.size_pt >= min_pt - 1e-9]
    floor_scale = max(0.25, min(1.0, min_pt / min(protected))) if protected else 0.25

    scales: list[float] = []
    k = 1
    while True:
        candidate_size = M - 0.5 * k
        candidate_scale = candidate_size / M
        if candidate_scale < floor_scale:
            break
        scales.append(candidate_scale)
        k += 1
    if not scales:
        scales = [floor_scale]

    def _fits(s: float, r: float) -> bool:
        return required_height_pt(paras, width_in, s, r) <= avail + EPS_PT

    lo, hi = 0, len(scales) - 1  # scales is sorted descending (largest scale first)
    best_i = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if _fits(scales[mid], 0.20):
            best_i = mid
            hi = mid - 1
        else:
            lo = mid + 1

    if best_i is None:
        # even the smallest ladder entry fails at max line-spacing reduction
        return FitResult(scales[-1], 0.20, fits=False)

    s = scales[best_i]
    if not _fits(s, 0.20):
        # non-monotone corner: walk down the ladder from the chosen index
        for i in range(best_i, len(scales)):
            if _fits(scales[i], 0.20):
                s = scales[i]
                break
        else:
            return FitResult(scales[-1], 0.20, fits=False)

    for r in LNSPC_STEPS:
        if _fits(s, r):
            return FitResult(s, r, True)
    return FitResult(s, 0.20, True)
