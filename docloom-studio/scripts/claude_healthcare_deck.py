"""Regenerate 'The Future of AI in Healthcare' with Claude as the model.

Same assembly -> lint -> save -> render path as the real pipeline; only the
'model' is different (me, authoring the IR directly instead of local Ollama).
Saved as a NEW artifact so the broken v2 stays for comparison."""

import json
import os

from docloom import Document, ensure_ids, lint, render
from docloom_studio.db import execute, new_id, now
from docloom_studio.irx import bake, studio_theme, to_docloom_theme
from docloom_studio.assets import apply_brand

NOTEBOOK = "LMLk7SyrR_oB"

slides = [
    {"layout": "title", "title": "The Future of AI in Healthcare",
     "subtitle": "From pilot projects to the point of care — what is real today, "
                 "what is hype, and what has to be true next."},

    {"layout": "section", "title": "The shift is already underway",
     "subtitle": "AI has moved past the demo — it reads scans, drafts notes, and "
                 "flags deteriorating patients inside the workflow every day."},

    {"layout": "content", "title": "Where AI already earns its keep",
     "blocks": [{"type": "bullets", "items": [
         {"text": "Radiology — triaging strokes, pulmonary nodules, and fractures ahead of the reading queue"},
         {"text": "Pathology — quantifying tumor cells and grading biopsies at scale"},
         {"text": "Ambient documentation — drafting the clinical note from the visit conversation"},
         {"text": "Drug discovery — narrowing candidate molecules from millions to a shortlist"},
         {"text": "Operations — predicting no-shows, staffing, and sepsis risk hours earlier"}]}]},

    {"layout": "content", "title": "The numbers behind the shift",
     "blocks": [
         {"type": "stats", "items": [
             {"label": "FDA-authorized AI/ML medical devices", "value": "~1,000", "delta": "up from ~50 in 2015"},
             {"label": "Share that are imaging/radiology", "value": "~76%"},
             {"label": "Documentation time saved by ambient scribes", "value": "~50%", "delta": "clinician-reported"}],
          },
         {"type": "paragraph",
          "text": "Adoption is real but uneven — concentrated where the signal "
                  "is visual and the ground truth is clear."}]},

    {"layout": "two_column", "title": "Promise and peril, side by side",
     "blocks": [
         {"type": "heading", "level": 3, "text": "The promise"},
         {"type": "bullets", "items": [
             {"text": "Faster, earlier diagnosis"},
             {"text": "Fewer missed findings and errors"},
             {"text": "Care reaching where specialists are scarce"},
             {"text": "Clinicians freed from the keyboard"}]}],
     "right": [
         {"type": "heading", "level": 3, "text": "The peril"},
         {"type": "bullets", "items": [
             {"text": "Bias when training data misses populations"},
             {"text": "Validated retrospectively, not prospectively"},
             {"text": "Clinical LLMs that hallucinate with confidence"}]}]},

    {"layout": "content", "title": "AI-enabled device authorizations keep climbing",
     "blocks": [
         {"type": "chart", "chart": "column",
          "title": "FDA-authorized AI/ML medical devices per year",
          "labels": ["2018", "2019", "2020", "2021", "2022", "2023"],
          "series": [{"name": "Authorizations", "values": [60, 95, 110, 130, 140, 170]}],
          "caption": "Illustrative trend; radiology dominates every year."}]},

    {"layout": "content", "title": "What has to be true for AI to earn trust",
     "blocks": [
         {"type": "numbered", "items": [
             {"text": "Prospective validation on real patients, not just held-out data"},
             {"text": "Transparency about what the model was trained on and where it fails"},
             {"text": "Equity checks across age, sex, and skin tone"},
             {"text": "A clinician in the loop with the authority to override"},
             {"text": "Monitoring for drift after deployment"}]},
         {"type": "callout", "style": "info",
          "text": "The bottleneck is no longer accuracy on a benchmark — it is "
                  "trust, evidence, and accountability in the clinic."}]},

    {"layout": "quote", "title": "The real question",
     "blocks": [{"type": "quote",
                 "text": "AI will not replace physicians — but it will reshape "
                         "what a physician spends their day doing."}]},

    {"layout": "content", "title": "The next five years",
     "blocks": [
         {"type": "bullets", "items": [
             {"text": "Ambient AI becomes the default way notes get written"},
             {"text": "Multimodal foundation models read images, text, and labs together"},
             {"text": "Real-world evidence, not just trials, drives approval"},
             {"text": "Regulation shifts from one-time clearance to continuous monitoring"}]},
         {"type": "callout", "style": "success",
          "text": "Winners will be the teams that pair a capable model with "
                  "clinical evidence — not the flashiest demo."}]},
]

doc = ensure_ids(Document(title="The Future of AI in Healthcare", slides=slides))

# same lint gate the pipeline uses
theme_json = apply_brand(studio_theme("slate"))
theme = to_docloom_theme(theme_json)
findings = lint(doc, theme)
errs = [f for f in findings if f.severity == "error"]
warns = [f for f in findings if f.severity == "warning"]
print(f"LINT: {len(errs)} errors, {len(warns)} warnings")
for f in findings:
    print(f"  {f.severity} [{f.rule}] slide? {f.message[:80]}")

# save as a new artifact next to the broken one (replace prior Claude runs)
from docloom_studio.db import query_all

for old in query_all("SELECT id FROM artifacts WHERE notebook_id=? AND "
                     "title LIKE '%(Claude)%'", (NOTEBOOK,)):
    execute("DELETE FROM artifact_versions WHERE artifact_id=?", (old["id"],))
    execute("DELETE FROM artifacts WHERE id=?", (old["id"],))
aid = new_id()
payload = {"ir": doc.model_dump(exclude_none=True), "theme_name": "slate",
           "brand_kit_id": None}
execute("INSERT INTO artifacts (id, notebook_id, kind, title, version, "
        "payload_json, created, updated) VALUES (?, ?, 'deck', ?, 1, ?, ?, ?)",
        (aid, NOTEBOOK, "The Future of AI in Healthcare (Claude)",
         json.dumps(payload), now(), now()))
execute("INSERT INTO artifact_versions (artifact_id, version, payload_json, created) "
        "VALUES (?, 1, ?, ?)", (aid, json.dumps(payload), now()))
print("saved artifact", aid)

# render to real PPTX to prove the whole path
out = os.path.join(os.path.dirname(__file__), "..", "healthcare_claude.pptx")
render(bake(doc), "pptx", out, theme)
print("rendered", os.path.basename(out), os.path.getsize(out), "bytes")
