"""Seed a showcase deck (every layout + chart + stats + citations) so the
real DeckViewer UI can be screenshotted. Uses DOCLOOM_STUDIO_HOME."""

import json

from _seed_common import seed_workspace
from docloom import Document, ensure_ids
from docloom_studio.db import execute, init_db, new_id, now

DECK = Document(
    title="The State of Async Work",
    subtitle="A 2026 field guide",
    authors=["docloom studio"],
    date="2026",
    slides=[
        {"layout": "title", "title": "The State of Async Work",
         "subtitle": "A 2026 field guide", "accent": "#2563EB"},
        {"layout": "section", "title": "Why it matters",
         "subtitle": "Distributed teams are the default now"},
        {"layout": "content", "title": "Three forces reshaping work",
         "blocks": [
             {"type": "bullets", "items": [
                 {"text": [{"text": "Remote-first hiring "},
                           {"text": "doubled since 2020", "bold": True},
                           {"text": ".", "cite": "gartner-2026"}]},
                 {"text": "Meeting fatigue is the #1 cited productivity drain"},
                 {"text": "Written-first culture compounds over time"},
             ]},
             {"type": "callout", "style": "success",
              "text": "Teams that default to async report 24% more deep-work hours."},
         ]},
        {"layout": "content", "title": "By the numbers",
         "blocks": [
             {"type": "stats", "items": [
                 {"label": "Deep-work hours", "value": "+24%", "delta": "vs sync teams"},
                 {"label": "Meeting time", "value": "-38%", "delta": "per week"},
                 {"label": "Retention", "value": "124%", "delta": "NRR"},
             ]},
         ]},
        {"layout": "content", "title": "Meeting load fell every quarter",
         "blocks": [
             {"type": "chart", "chart": "column", "title": "Hours in meetings / week",
              "labels": ["Q1", "Q2", "Q3", "Q4"],
              "series": [{"name": "Sync team", "values": [14.0, 13.5, 13.0, 12.8]},
                         {"name": "Async team", "values": [11.0, 8.5, 6.0, 5.2]}]},
         ]},
        {"layout": "two_column", "title": "Sync vs async standups",
         "blocks": [
             {"type": "heading", "level": 3, "text": "Daily sync"},
             {"type": "bullets", "items": [
                 {"text": "Fixed time zone"}, {"text": "Interrupts flow"},
                 {"text": "Verbal, ephemeral"}]},
         ],
         "right": [
             {"type": "heading", "level": 3, "text": "Async"},
             {"type": "bullets", "items": [
                 {"text": "Any time zone"}, {"text": "Protects focus"},
                 {"text": "Written, searchable"}]},
         ]},
        {"layout": "image_right", "title": "Written-first, always",
         "image": {"type": "image", "query": "team collaborating on a whiteboard"},
         "blocks": [
             {"type": "paragraph",
              "text": "The best async teams write things down once and reference "
                      "them forever — decisions, context, and rationale."},
             {"type": "bullets", "items": [
                 {"text": "One source of truth"},
                 {"text": "Onboarding without meetings"}]},
         ]},
        {"layout": "content", "title": "Adoption keeps climbing",
         "blocks": [
             {"type": "table",
              "header": ["Year", "Async-first teams", "Change"],
              "rows": [["2024", "31%", "—"], ["2025", "44%", "+13pts"],
                       ["2026", "58%", "+14pts"]],
              "caption": "Share of surveyed engineering orgs"},
         ]},
        {"layout": "quote", "blocks": [
            {"type": "quote", "text": "Make async boring and predictable.",
             "attribution": "Meridian Research"}]},
    ],
    sources=[
        {"id": "gartner-2026", "title": "Future of Work 2026",
         "publisher": "Gartner", "url": "https://example.com/gartner"},
    ],
)


def main() -> None:
    init_db()
    doc = ensure_ids(DECK)
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, workspace_id, created, updated) "
            "VALUES (?, ?, ?, ?, ?)", (nb, "Showcase", seed_workspace(), now(), now()))
    aid = new_id()
    payload = {"ir": doc.model_dump(exclude_none=True),
               "theme_name": "slate", "brand_kit_id": None}
    execute("INSERT INTO artifacts (id, notebook_id, kind, title, version, "
            "payload_json, created, updated) VALUES (?, ?, 'deck', ?, 1, ?, ?, ?)",
            (aid, nb, doc.title, json.dumps(payload), now(), now()))
    print(f"seeded notebook {nb} deck {aid}")


if __name__ == "__main__":
    main()
