"""Semantic theme tokens. Renderers map tokens to native mechanisms
(PPTX colors/fonts, Typst set rules, XLSX formats, CSS variables) and must
never hard-code literal colors — that is what keeps all formats on-brand."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class Theme(BaseModel):
    primary: str = "#1D4ED8"
    accent: str = "#0E9F6E"
    background: str = "#FFFFFF"
    surface: str = "#F3F4F6"
    text: str = "#111827"
    muted: str = "#6B7280"
    font_heading: str = "Arial"
    font_body: str = "Georgia"

    @field_validator("primary", "accent", "background", "surface", "text", "muted")
    @classmethod
    def _hex(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("#"):
            v = "#" + v
        if len(v) != 7 or any(c not in "0123456789abcdefABCDEF" for c in v[1:]):
            raise ValueError(f"expected #RRGGBB hex color, got {v!r}")
        return v.upper()

    @classmethod
    def load(cls, path: str | Path) -> "Theme":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8-sig"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.model_dump(), indent=2), encoding="utf-8"
        )


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def contrast_ratio(color_a: str, color_b: str) -> float:
    """WCAG 2.x contrast ratio between two #RRGGBB colors (1.0 to 21.0)."""

    def luminance(color: str) -> float:
        def channel(c: int) -> float:
            s = c / 255.0
            return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

        r, g, b = (channel(c) for c in hex_to_rgb(color))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    la, lb = luminance(color_a), luminance(color_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


DEFAULT = Theme()
