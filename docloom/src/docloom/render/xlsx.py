"""XLSX renderer: doc.sheets (or Table blocks as a fallback) to an Excel
workbook via xlsxwriter, with theme-driven header/body formatting."""

from __future__ import annotations

import warnings
from pathlib import Path

import xlsxwriter

from . import RenderError
from ..ir import (
    Chart,
    Column,
    Document,
    Formula,
    RichText,
    Sheet,
    Table,
    cited_ids,
    normalize_table,
    report_blocks,
    source_numbers,
    spans,
)
from ..theme import Theme

_FORBIDDEN = set("[]:*?/\\")
_XLS_STRMAX = 32767  # Excel's per-cell character limit


def _write_str(ws, row: int, col: int, text: str, fmt) -> None:
    # write_string silently truncates past _XLS_STRMAX (returns -2, no
    # exception): warn here so callers get a signal instead of lost text.
    if len(text) > _XLS_STRMAX:
        warnings.warn(
            f"cell text truncated to {_XLS_STRMAX} characters (Excel's limit)",
            stacklevel=2,
        )
        text = text[:_XLS_STRMAX]
    ws.write_string(row, col, text, fmt)


def _sheet_name(raw: str, used: set[str]) -> str:
    # strip("'") after truncating: Excel forbids leading/trailing apostrophes
    name = "".join(c for c in raw if c not in _FORBIDDEN).strip()[:31].strip("'") or "Sheet"
    candidate, n = name, 2
    while candidate.lower() in used:
        suffix = f" {n}"
        candidate = name[: 31 - len(suffix)] + suffix
        n += 1
    used.add(candidate.lower())
    return candidate


def _cell_text(rt: RichText, numbers: dict[str, int]) -> str:
    parts: list[str] = []
    for sp in spans(rt):
        parts.append(sp.text)
        if sp.cite and sp.cite in numbers:
            parts.append(f" [{numbers[sp.cite]}]")
    return "".join(parts)


def _tables_as_sheets(doc: Document) -> list[Sheet]:
    numbers = source_numbers(doc)
    tables = [b for b in report_blocks(doc) if isinstance(b, Table)]
    sheets: list[Sheet] = []
    for i, table in enumerate(tables, start=1):
        header, rows = normalize_table(table.header, table.rows)
        sheets.append(
            Sheet(
                name=table.caption or f"Table {i}",
                columns=[Column(header=_cell_text(h, numbers)) for h in header],
                rows=[[_cell_text(cell, numbers) for cell in row] for row in rows],
            )
        )
    return sheets


def _charts_as_sheets(doc: Document) -> list[Sheet]:
    charts = [b for b in report_blocks(doc) if isinstance(b, Chart)]
    return [
        Sheet(
            name=chart.title or f"Chart {i}",
            columns=[Column(header="")] + [Column(header=lbl) for lbl in chart.labels],
            rows=[[s.name, *s.values] for s in chart.series],
        )
        for i, chart in enumerate(charts, start=1)
    ]


def _sources_sheet(doc: Document) -> Sheet:
    numbers = source_numbers(doc)
    lines: list[list[str | None]] = []
    seen: set[str] = set()
    for src in doc.sources:
        if src.id in seen:  # duplicate id: numbers keeps the first, so skip the rest
            continue
        seen.add(src.id)
        line = f"{numbers[src.id]}. {src.title}"
        if src.publisher:
            line += " \u2014 " + src.publisher
        if src.date:
            line += f" ({src.date})"
        if src.url:
            line += f", {src.url}"
        lines.append([line])
    return Sheet(name="Sources", columns=[Column(header="Sources", width=90)], rows=lines)


def render(doc: Document, theme: Theme, out_path: Path) -> Path:
    sheets = list(doc.sheets) or _tables_as_sheets(doc) + _charts_as_sheets(doc)
    if not sheets:
        raise RenderError("document has no sheets or tables; add sheets[] to render xlsx")
    if doc.sources and cited_ids(doc):
        sheets.append(_sources_sheet(doc))

    # nan_inf_to_errors: non-finite floats become Excel #NUM!/#DIV/0! instead
    # of xlsxwriter raising TypeError
    workbook = xlsxwriter.Workbook(str(out_path), {"nan_inf_to_errors": True})
    header_fmt = workbook.add_format(
        {
            "bold": True,
            "font_name": theme.font_heading,
            "font_size": 11,
            "font_color": theme.background,
            "bg_color": theme.primary,
        }
    )
    body = {"font_name": theme.font_body, "font_size": 11, "font_color": theme.text}
    body_fmt = workbook.add_format(body)

    used: set[str] = set()
    for sheet in sheets:
        ws = workbook.add_worksheet(_sheet_name(sheet.name, used))
        # effective width = widest row, so undeclared trailing cells still render
        ncols = max(len(sheet.columns), max((len(r) for r in sheet.rows), default=0))
        if ncols == 0:
            continue
        columns = list(sheet.columns) + [Column(header="")] * (ncols - len(sheet.columns))
        col_fmts = []
        for c, col in enumerate(columns):
            fmt = (
                workbook.add_format(dict(body, num_format=col.format))
                if col.format
                else body_fmt
            )
            col_fmts.append(fmt)
            ws.set_column(c, c, col.width if col.width is not None else 12, fmt)
            _write_str(ws, 0, c, col.header, header_fmt)
        ws.set_row(0, 20)
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, len(sheet.rows), ncols - 1)
        for r, row in enumerate(sheet.rows, start=1):
            for c, cell in enumerate(row[:ncols]):
                fmt = col_fmts[c]
                if isinstance(cell, Formula):
                    if cell.formula.strip():
                        ws.write_formula(r, c, cell.formula, fmt)
                    else:
                        ws.write_blank(r, c, None, fmt)
                elif cell is None:
                    ws.write_blank(r, c, None, fmt)
                elif isinstance(cell, bool):
                    ws.write_boolean(r, c, cell, fmt)
                elif isinstance(cell, str):
                    _write_str(ws, r, c, cell, fmt)
                else:
                    try:
                        ws.write_number(r, c, cell, fmt)
                    except (OverflowError, TypeError):
                        # ints outside float range (or non-finite) cannot be a number cell
                        ws.write_string(r, c, str(cell), fmt)
    workbook.close()
    return out_path
