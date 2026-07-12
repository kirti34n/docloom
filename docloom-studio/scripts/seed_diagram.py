"""Seed a diagram artifact (D2 architecture diagram) into the newest notebook."""

import json

from _seed_common import newest_notebook
from docloom_studio.db import execute, new_id, now

nb = newest_notebook("Diagram demo")
d2 = """direction: right

classes: {
  svc: { style: { fill: "#EEF2FF"; stroke: "#4F46E5"; stroke-width: 2; border-radius: 12; font-color: "#1E1B4B" } }
  store: { style: { fill: "#ECFDF5"; stroke: "#0D9488"; stroke-width: 2; font-color: "#0F766E" } }
  out: { style: { fill: "#F1F5F9"; stroke: "#64748B"; stroke-width: 2; border-radius: 12; font-color: "#0F172A" } }
}

user: User { class: svc; shape: person }
spa: React SPA { class: svc }
api: FastAPI { class: svc }
llm: Local LLM { class: svc }
db: SQLite { class: store; shape: cylinder }
emb: Embeddings { class: svc }
vec: Vector store { class: store; shape: cylinder }
rend: docloom render { class: svc }
out: PPTX / PDF / XLSX { class: out; shape: document }

user -> spa -> api
api -> llm
api -> db
api -> emb -> vec
api -> rend -> out
"""

aid = new_id()
payload = {"source": d2, "render": None}
execute(
    "INSERT INTO artifacts (id, notebook_id, kind, title, version, payload_json, "
    "created, updated) VALUES (?, ?, 'diagram', 'System architecture', 1, ?, ?, ?)",
    (aid, nb, json.dumps(payload), now(), now()),
)
print("seeded D2 diagram", aid)
