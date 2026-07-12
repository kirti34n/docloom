"""Seed an image asset + brand kit + a deck slide that uses the image."""

import json

from PIL import Image, ImageDraw

from _seed_common import newest_notebook
from docloom import Document, ensure_ids
from docloom_studio.db import execute, new_id, now
from docloom_studio.settings import data_dir, set_setting

nb = newest_notebook("Assets demo")

# a real gradient image so the thumbnail + slide look like a photo
aid = new_id()
adir = data_dir() / "assets" / aid
adir.mkdir(parents=True, exist_ok=True)
img = Image.new("RGB", (960, 640))
d = ImageDraw.Draw(img)
for y in range(640):
    t = y / 640
    d.line([(0, y), (960, y)], fill=(int(40 + t * 60), int(90 + t * 80), int(180 - t * 60)))
d.ellipse([620, 120, 860, 360], fill=(255, 210, 120))
img.save(adir / "team.png")
execute("INSERT INTO assets (id, type, filename, tags, created) "
        "VALUES (?, 'image', 'team.png', 'remote, team, collaboration', ?)",
        (aid, now()))

set_setting("brand.active", {"accent": "#e8590c", "logo_asset_id": None})

# a deck whose image_left slide uses the asset
doc = ensure_ids(Document(title="Remote Teams", slides=[
    {"layout": "title", "title": "Remote Teams"},
    {"layout": "image_left", "title": "How we work",
     "image": {"asset_id": aid, "path": f"asset://{aid}", "alt": "team"},
     "blocks": [{"type": "bullets", "items": [
         {"text": "Async by default"},
         {"text": "Written decisions"},
         {"text": "Any timezone"}]}]},
]))
art = new_id()
payload = {"ir": doc.model_dump(exclude_none=True), "theme_name": "pulse"}
execute("INSERT INTO artifacts (id, notebook_id, kind, title, version, "
        "payload_json, created, updated) VALUES (?, ?, 'deck', ?, 1, ?, ?, ?)",
        (art, nb, "Remote Teams", json.dumps(payload), now(), now()))
print("seeded asset", aid, "deck", art)
