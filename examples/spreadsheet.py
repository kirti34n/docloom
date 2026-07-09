"""Sample: a workbook (XLSX) from a docloom IR, with real formulas.

    python examples/spreadsheet.py   ->  budget.xlsx
"""
from docloom import Document, render

doc = Document(
    title="Team Budget",
    sheets=[
        {"name": "Q3 budget",
         "columns": [
             {"header": "Item", "width": 22},
             {"header": "Cost", "format": "#,##0"},
             {"header": "Notes"},
         ],
         "rows": [
             ["Tooling", 4200, "annual"],
             ["Contractors", 12000, "3 months"],
             ["Travel", 3500, "offsite"],
             ["Total", {"formula": "=SUM(B2:B4)"}, ""],
         ]},
    ],
)

if __name__ == "__main__":
    print(render(doc, "xlsx", "budget.xlsx"))
