"""Seed a doc and a sheet artifact into the newest notebook (for UI shots)."""

import json

from docloom import Document, ensure_ids
from docloom_studio.db import execute, new_id, now, query_one

nb = query_one("SELECT id FROM notebooks ORDER BY created DESC LIMIT 1")["id"]


def add(kind: str, doc: Document, theme: str = "paper") -> None:
    doc = ensure_ids(doc)
    aid = new_id()
    payload = {"ir": doc.model_dump(exclude_none=True), "theme_name": theme}
    execute(
        "INSERT INTO artifacts (id, notebook_id, kind, title, version, "
        "payload_json, created, updated) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
        (aid, nb, kind, doc.title, json.dumps(payload), now(), now()),
    )
    print(f"seeded {kind} {aid}")


add("doc", Document(
    title="Async Work: A Practical Guide",
    blocks=[
        {"type": "heading", "level": 2, "text": "Why async wins"},
        {"type": "paragraph", "text": [
            {"text": "Teams that default to async report "},
            {"text": "24% more deep-work hours", "bold": True},
            {"text": ", and cut meeting load by more than a third.",
             "cite": "study-1"}]},
        {"type": "bullets", "items": [
            {"text": "Written-first culture compounds over time"},
            {"text": "Decisions become searchable, not ephemeral"},
            {"text": "Time zones stop being a constraint"}]},
        {"type": "callout", "style": "success",
         "text": "Start small: replace one daily standup with a written thread."},
        {"type": "heading", "level": 2, "text": "The trade-offs"},
        {"type": "paragraph", "text": "Async is not free — it demands "
         "discipline in writing and a tolerance for slower, deliberate replies."},
    ],
    sources=[{"id": "study-1", "title": "State of Async 2026",
              "publisher": "Meridian"}],
))

add("sheet", Document(
    title="Team Budget",
    sheets=[{
        "name": "Q1", "columns": [
            {"header": "Item"}, {"header": "Cost", "format": "$#,##0"},
            {"header": "Notes"}],
        "rows": [
            ["Tooling", 4200, "annual"],
            ["Contractors", 12000, "3 months"],
            ["Travel", 3500, "offsite"],
            ["Total", {"formula": "=SUM(B2:B4)"}, ""]],
    }],
))
