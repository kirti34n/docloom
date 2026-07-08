"""Seed a diagram artifact (Mermaid flowchart) into the newest notebook."""

import json

from docloom_studio.db import execute, new_id, now, query_one

nb = query_one("SELECT id FROM notebooks ORDER BY created DESC LIMIT 1")["id"]
mermaid = """flowchart LR
  U[User] --> FE[React SPA]
  FE --> API[FastAPI]
  API --> LLM[Local LLM]
  API --> DB[(SQLite)]
  API --> EMB[Embeddings]
  EMB --> VEC[Vector store]
  API --> REND[docloom render]
  REND --> OUT[PPTX / PDF / XLSX]"""

aid = new_id()
payload = {"mermaid_src": mermaid, "excalidraw_scene": None,
           "canvas_dirty": False, "render": None}
execute(
    "INSERT INTO artifacts (id, notebook_id, kind, title, version, payload_json, "
    "created, updated) VALUES (?, ?, 'diagram', 'System architecture', 1, ?, ?, ?)",
    (aid, nb, json.dumps(payload), now(), now()),
)
print("seeded diagram", aid)
