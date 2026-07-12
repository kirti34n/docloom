"""Add a couple of ready text sources to the newest notebook (for UI shots)."""

import json

from _seed_common import newest_notebook
from docloom_studio.db import execute, new_id, now

nb = newest_notebook("Sources demo")
rows = [
    ("Async work study",
     "Teams that default to async report 24 percent more deep-work hours."),
    ("Meeting costs",
     "The average knowledge worker spends 38 percent of the week in meetings."),
]
for title, text in rows:
    sid = new_id()
    execute(
        "INSERT INTO sources (id, notebook_id, kind, title, status, context_mode, "
        "meta_json, created) VALUES (?, ?, 'text', ?, 'ready', 'full', ?, ?)",
        (sid, nb, title, json.dumps({"text": text}), now()),
    )
print("sources added to", nb)
