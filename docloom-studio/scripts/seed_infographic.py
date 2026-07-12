"""Seed an infographic artifact into the newest notebook."""

import json

from _seed_common import newest_notebook
from docloom_studio.db import execute, new_id, now

nb = newest_notebook("Infographic demo")
payload = {
    "style": "list",
    "antv": {
        "template": "list-column-vertical-icon-arrow",
        "data": {
            "title": "Why async work wins",
            "lists": [
                {"label": "Deep work", "desc": "24% more focus hours"},
                {"label": "Fewer meetings", "desc": "meeting load down a third"},
                {"label": "Any timezone", "desc": "no scheduling overhead"},
                {"label": "Searchable", "desc": "decisions written, not spoken"},
            ],
        },
    },
    "render": None,
}
aid = new_id()
execute(
    "INSERT INTO artifacts (id, notebook_id, kind, title, version, payload_json, "
    "created, updated) VALUES (?, ?, 'infographic', 'Why async work wins', 1, ?, ?, ?)",
    (aid, nb, json.dumps(payload), now(), now()),
)
print("seeded infographic", aid)
