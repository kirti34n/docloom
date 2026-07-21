"""Unit tests for docloom.render.textfit: the measured-fit module that
underlies pptx.py's post-layout autofit pass (see test_render_quality.py's
pptx overflow/floor/untouched tests for the integration side of this fix).
Covers the pure fit_scale/required_height_pt API: fitting text is left
alone, overflowing text is shrunk along the half-point ladder, an absurd
overflow clamps at the legibility floor and reports fits=False, an
unresolvable font family degrades to the err-small fallback estimate
instead of raising, and hard line breaks add real lines."""

from __future__ import annotations

from docloom.render.textfit import FitResult, ParaSpec, RunSpec, fit_scale, required_height_pt


def _para(words: str, size_pt: float, family: str = "Arial") -> ParaSpec:
    return ParaSpec(runs=(RunSpec(text=words, family=family, size_pt=size_pt),))


def test_fit_scale_returns_one_for_fitting_text():
    para = _para("one two three", 14)
    res = fit_scale([para], width_in=6.0, height_in=2.0)
    assert res == FitResult(1.0, 0.0, True)


def test_fit_scale_shrinks_overflow():
    # 20 words at 14pt in a 4in x 0.6in box overflows at full size (needs
    # 63pt against a 43.2pt box) but fits comfortably once shrunk.
    words = " ".join(f"word{i}" for i in range(20))
    para = _para(words, 14)
    res = fit_scale([para], width_in=4.0, height_in=0.6)
    assert 9 / 14 <= res.scale < 1.0
    assert res.fits is True
    need = required_height_pt([para], 4.0, res.scale, res.lnspc_reduction)
    assert need <= 0.6 * 72 + 0.75  # EPS_PT


def test_fit_scale_clamps_at_floor():
    words = " ".join(f"word{i}" for i in range(2000))
    para = _para(words, 14)
    res = fit_scale([para], width_in=2.0, height_in=0.5, min_pt=9.0)
    assert res.fits is False
    assert res.lnspc_reduction == 0.20
    # dominant fitted size is the first (largest) ladder entry >= min_pt,
    # i.e. the floor itself when the box is this hopelessly small
    assert round(res.scale * 14, 3) == 9.0


def test_unresolvable_family_never_raises():
    huge = " ".join(f"word{i}" for i in range(2000))
    huge_para = _para(huge, 14, family="NoSuchFontFamilyXYZ")
    res = fit_scale([huge_para], width_in=2.0, height_in=0.5)
    assert res.scale < 1.0  # egregious overflow still shrinks via the fallback estimate

    tiny_para = _para("hi there", 14, family="NoSuchFontFamilyXYZ")
    res_tiny = fit_scale([tiny_para], width_in=6.0, height_in=2.0)
    assert res_tiny.scale == 1.0  # fallback errs small: never shrinks borderline text


def test_hard_breaks_add_lines():
    with_breaks = _para("a\nb\nc", 14)
    without_breaks = _para("a b c", 14)
    h_with = required_height_pt([with_breaks], width_in=6.0, scale=1.0, reduction=0.0)
    h_without = required_height_pt([without_breaks], width_in=6.0, scale=1.0, reduction=0.0)
    assert h_with > h_without


def test_lnspc_reduction_is_the_smallest_step_that_fits():
    words = " ".join(f"word{i}" for i in range(60))
    para = _para(words, 14)
    res = fit_scale([para], width_in=4.0, height_in=1.0)
    need_no_reduction = required_height_pt([para], 4.0, res.scale, 0.0)
    if need_no_reduction <= 1.0 * 72 + 0.75:
        assert res.lnspc_reduction == 0.0
    else:
        # some reduction was necessary at the chosen scale; confirm it's
        # the smallest step in the ladder that actually makes it fit
        assert res.lnspc_reduction in (0.10, 0.20)
        smaller_steps = [r for r in (0.0, 0.10) if r < res.lnspc_reduction]
        for r in smaller_steps:
            assert required_height_pt([para], 4.0, res.scale, r) > 1.0 * 72 + 0.75
