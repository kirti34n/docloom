"""docloom test bench: a local web UI for driving the full pipeline.

    .venv/Scripts/python examples/webui.py        (then open http://127.0.0.1:8765)

Generate a document with a local Ollama model (optional), edit the JSON, lint
it, render it to any format, and preview the results. Needs starlette+uvicorn
(installed with `pip install "docloom[mcp]"` or `pip install starlette uvicorn`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from docloom import (
    AUTHORING_GUIDE, DEFAULT, Document, Theme, lint, llm_schema,
    parse_llm_output, render,
)
from docloom.render import FORMATS, RenderError, slug

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out_ui"
OUT.mkdir(exist_ok=True)
OLLAMA = "http://localhost:11434"

SYSTEM_PROMPT = (
    AUTHORING_GUIDE
    + "\nReturn ONLY one JSON object that validates against this JSON Schema. "
      "No markdown fences, no commentary, no wrapper keys. slides, blocks, "
      "sheets, and sources are ARRAYS at the top level of the document.\n\n"
      "JSON Schema:\n" + json.dumps(llm_schema())
)


def _ollama(path: str, body: dict | None = None, timeout: int = 1800):
    req = urllib.request.Request(
        OLLAMA + path,
        json.dumps(body).encode("utf-8") if body else None,
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def api_models(request):
    try:
        tags = _ollama("/api/tags", timeout=5)
        names = [m["name"] for m in tags.get("models", [])
                 if "embed" not in m["name"]]
        return JSONResponse({"models": names})
    except Exception:
        return JSONResponse({"models": [], "offline": True})


def api_example(request):
    example = ROOT / "quarterly_report.json"
    return JSONResponse({"doc": example.read_text(encoding="utf-8-sig")})


async def api_generate(request):
    payload = await request.json()
    model, prompt = payload["model"], payload["prompt"]
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}]
    rounds, doc = [], None
    think: bool | None = False
    for round_no in range(1, 4):
        body = {"model": model, "messages": messages, "format": llm_schema(),
                "stream": False,
                "options": {"num_ctx": 16384,
                            "temperature": 0.3 + 0.15 * (round_no - 1)}}
        if think is not None:
            body["think"] = think
        t0 = time.time()
        try:
            resp = _ollama("/api/chat", body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if think is not None and "think" in detail.lower():
                think = None
                continue
            return JSONResponse({"rounds": rounds,
                                 "error": f"Ollama error: {detail[:300]}"})
        except Exception as e:
            return JSONResponse({"rounds": rounds,
                                 "error": f"Ollama unreachable: {e}"})
        raw = resp["message"]["content"]
        entry = {"round": round_no, "seconds": round(time.time() - t0, 1)}
        problem = None
        try:
            doc = parse_llm_output(raw)
            entry["status"] = "parsed"
            findings = lint(doc, DEFAULT)
            entry["lint"] = [f.model_dump() for f in findings]
            errors = [f for f in findings if f.severity == "error"]
            if errors:
                problem = "Fix these lint findings:\n" + "\n".join(
                    f"{f.severity} [{f.rule}] {f.where}: {f.message}"
                    for f in findings)
        except Exception as e:
            entry["status"] = "invalid"
            entry["error"] = str(e)[:400]
            problem = (f"That JSON failed: {str(e)[:700]}\n"
                       "Return the complete corrected JSON document.")
            doc = None
        rounds.append(entry)
        if problem is None:
            break
        messages += [{"role": "assistant", "content": raw[-3500:]},
                     {"role": "user", "content": problem}]
    result = {"rounds": rounds}
    if doc is not None:
        result["doc"] = json.loads(doc.model_dump_json(exclude_none=True))
    else:
        result["error"] = "No valid document after 3 rounds — see the round log."
    return JSONResponse(result)


def _load_doc_theme(payload) -> tuple[Document, Theme]:
    raw_doc = payload["doc"]
    doc = (Document.model_validate(raw_doc) if isinstance(raw_doc, dict)
           else Document.model_validate_json(raw_doc))
    raw_theme = payload.get("theme") or ""
    if isinstance(raw_theme, dict):
        theme = Theme.model_validate(raw_theme)
    elif raw_theme.strip():
        theme = Theme.model_validate_json(raw_theme)
    else:
        theme = DEFAULT
    return doc, theme


async def api_lint(request):
    try:
        doc, theme = _load_doc_theme(await request.json())
    except Exception as e:
        return JSONResponse({"error": str(e)[:600]})
    return JSONResponse({"findings": [f.model_dump() for f in lint(doc, theme)]})


async def api_render(request):
    payload = await request.json()
    try:
        doc, theme = _load_doc_theme(payload)
    except Exception as e:
        return JSONResponse({"error": str(e)[:600]})
    run_dir = OUT / time.strftime("run-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    files, errors = [], {}
    for fmt in payload.get("formats", ["pptx"]):
        if fmt not in FORMATS:
            errors[fmt] = "unknown format"
            continue
        try:
            path = render(doc, fmt, run_dir / (slug(doc.title) + FORMATS[fmt][1]),
                          theme)
            files.append({"name": path.name, "fmt": fmt,
                          "size": path.stat().st_size,
                          "url": f"/files/{run_dir.name}/{path.name}"})
        except RenderError as e:
            errors[fmt] = str(e)
        except Exception as e:
            errors[fmt] = f"{type(e).__name__}: {e}"
    return JSONResponse({"files": files, "errors": errors})


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>docloom test bench</title>
<style>
  :root {
    --primary: #1D4ED8; --accent: #0E9F6E; --danger: #B91C1C;
    --warn: #B45309; --ink: #1A1D23; --muted: #6B7280;
    --paper: #F7F7F5; --card: #FFFFFF; --line: #E4E4E0;
    --mono: Consolas, ui-monospace, monospace;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--paper); color: var(--ink);
         font: 14px/1.5 system-ui, sans-serif; }
  header { display: flex; align-items: baseline; gap: 12px;
           padding: 14px 22px; border-bottom: 2px solid var(--ink); }
  header h1 { font: 700 22px Georgia, serif; margin: 0; }
  header h1 em { color: var(--primary); font-style: normal; }
  header span { color: var(--muted); font-size: 13px; }
  main { display: grid; grid-template-columns: minmax(380px, 1fr) minmax(380px, 1fr);
         gap: 18px; padding: 18px 22px; max-width: 1400px; margin: 0 auto; }
  @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  section { background: var(--card); border: 1px solid var(--line);
            border-radius: 8px; padding: 14px 16px; margin-bottom: 18px; }
  h2 { font: 700 15px Georgia, serif; margin: 0 0 10px; }
  textarea { width: 100%; border: 1px solid var(--line); border-radius: 6px;
             padding: 8px; font-family: var(--mono); font-size: 12.5px;
             background: #FCFCFB; resize: vertical; }
  textarea:focus, select:focus, button:focus-visible
    { outline: 2px solid var(--primary); outline-offset: 1px; }
  select { padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px;
           background: #fff; font: inherit; max-width: 220px; }
  button { padding: 7px 14px; border: 1px solid var(--ink); border-radius: 6px;
           background: var(--ink); color: #fff; font: 600 13px system-ui;
           cursor: pointer; }
  button.secondary { background: #fff; color: var(--ink); }
  button:disabled { opacity: .5; cursor: wait; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
         margin-top: 10px; }
  label.fmt { font-family: var(--mono); font-size: 12.5px; display: inline-flex;
              gap: 4px; align-items: center; }
  /* pipeline rail: the loop itself, stages threaded on a line */
  .rail { display: flex; align-items: center; gap: 0; margin: 4px 0 14px; }
  .stage { font-family: var(--mono); font-size: 12px; padding: 4px 10px;
           border: 1.5px solid var(--line); border-radius: 999px;
           background: #fff; color: var(--muted); white-space: nowrap;
           transition: all .25s; }
  .stage.run { border-color: var(--primary); color: var(--primary); }
  .stage.ok { border-color: var(--accent); color: var(--accent); }
  .stage.fail { border-color: var(--danger); color: var(--danger); }
  .thread { flex: 0 0 26px; height: 2px; background: var(--line);
            transition: background .25s; }
  .thread.ok { background: var(--accent); }
  @media (prefers-reduced-motion: reduce) { .stage, .thread { transition: none; } }
  .log { font-family: var(--mono); font-size: 12px; color: var(--muted);
         white-space: pre-wrap; margin: 0; }
  ul.findings { list-style: none; padding: 0; margin: 0; }
  ul.findings li { font-family: var(--mono); font-size: 12.5px; padding: 5px 8px;
                   border-left: 3px solid var(--muted); margin-bottom: 5px;
                   background: #FAFAF8; }
  ul.findings li.error { border-color: var(--danger); }
  ul.findings li.warning { border-color: var(--warn); }
  .files a { display: inline-block; font-family: var(--mono); font-size: 12.5px;
             margin: 0 8px 8px 0; padding: 6px 10px; border: 1px solid var(--line);
             border-radius: 6px; color: var(--primary); text-decoration: none;
             background: #fff; }
  .files a:hover { border-color: var(--primary); }
  iframe { width: 100%; height: 480px; border: 1px solid var(--line);
           border-radius: 6px; background: #fff; }
  .hint { color: var(--muted); font-size: 12.5px; margin: 4px 0 0; }
  .err { color: var(--danger); font-family: var(--mono); font-size: 12.5px;
         white-space: pre-wrap; }
  details summary { cursor: pointer; font: 700 15px Georgia, serif; }
</style>
</head>
<body>
<header><h1><em>docloom</em> test bench</h1>
<span>generate &rarr; parse &rarr; lint &rarr; render, on your machine</span></header>
<main>
<div><!-- left: inputs -->
  <section>
    <h2>1 &middot; Generate with a local model</h2>
    <div class="row">
      <select id="model"></select>
      <span class="hint" id="ollamaState"></span>
    </div>
    <div class="row"><textarea id="prompt" rows="4">Create a 5-slide deck and a short report about why async standups beat meetings, plus one sheet 'Time saved' with columns Team, Hours/week (format "0.0"), and 3 rows. One source, cited once.</textarea></div>
    <div class="row">
      <button id="generate">Generate document</button>
      <span class="hint">local models can take a minute or two per round</span>
    </div>
    <pre class="log" id="genLog"></pre>
  </section>
  <section>
    <h2>2 &middot; Document JSON</h2>
    <textarea id="doc" rows="18" spellcheck="false"
      placeholder='{"title": "...", "slides": [...], "blocks": [...]}'></textarea>
    <div class="row">
      <button class="secondary" id="loadExample">Load example</button>
      <button class="secondary" id="lint">Lint</button>
      <button id="render">Render</button>
    </div>
    <div class="row" id="formats"></div>
  </section>
  <section>
    <details><summary>Theme (optional)</summary>
      <div class="row"><textarea id="theme" rows="10" spellcheck="false"></textarea></div>
      <p class="hint">Edit colors/fonts and re-render — every format follows the same tokens.</p>
    </details>
  </section>
</div>
<div><!-- right: results -->
  <section>
    <h2>Pipeline</h2>
    <div class="rail" id="rail">
      <span class="stage" data-s="generate">generate</span><span class="thread"></span>
      <span class="stage" data-s="parse">parse</span><span class="thread"></span>
      <span class="stage" data-s="lint">lint</span><span class="thread"></span>
      <span class="stage" data-s="render">render</span>
    </div>
    <h2>Lint findings</h2>
    <ul class="findings" id="findings"><li>Nothing linted yet.</li></ul>
  </section>
  <section>
    <h2>Rendered files</h2>
    <div class="files" id="files"><span class="hint">Render a document to see files here.</span></div>
    <div class="err" id="renderErr"></div>
    <div id="previewWrap" hidden><h2 id="previewTitle">Preview</h2><iframe id="preview" title="rendered document preview"></iframe></div>
  </section>
</div>
</main>
<script>
const $ = id => document.getElementById(id);
const stages = {};
document.querySelectorAll('.stage').forEach(s => stages[s.dataset.s] = s);
const threads = document.querySelectorAll('.thread');

function setStage(name, state) {
  stages[name].className = 'stage' + (state ? ' ' + state : '');
  const order = ['generate', 'parse', 'lint', 'render'];
  order.forEach((s, i) => {
    if (i < threads.length)
      threads[i].className = 'thread' +
        (stages[s].className.includes('ok') ? ' ok' : '');
  });
}
function resetStages() { Object.keys(stages).forEach(s => setStage(s, '')); }

async function post(url, body) {
  const r = await fetch(url, {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  return r.json();
}

function showFindings(findings) {
  const ul = $('findings'); ul.innerHTML = '';
  if (!findings.length) {
    ul.innerHTML = '<li style="border-color:var(--accent)">clean - no findings</li>';
    return;
  }
  for (const f of findings) {
    const li = document.createElement('li');
    li.className = f.severity;
    li.textContent = `${f.severity}  [${f.rule}]  ${f.where}: ${f.message}`;
    ul.appendChild(li);
  }
}

// models
fetch('/api/models').then(r => r.json()).then(d => {
  const sel = $('model');
  if (!d.models.length) {
    $('ollamaState').textContent = d.offline
      ? "Ollama isn't running - paste or load a document below instead."
      : 'No chat models installed.';
    $('generate').disabled = true;
    return;
  }
  d.models.forEach(m => sel.add(new Option(m, m)));
});

// formats
for (const f of ['pptx','docx','xlsx','pdf','html','md']) {
  const l = document.createElement('label');
  l.className = 'fmt';
  l.innerHTML = `<input type="checkbox" value="${f}" checked> ${f}`;
  $('formats').appendChild(l);
}

$('loadExample').onclick = async () => {
  const d = await (await fetch('/api/example')).json();
  $('doc').value = JSON.stringify(JSON.parse(d.doc), null, 2);
  resetStages();
};

$('generate').onclick = async () => {
  const btn = $('generate'); btn.disabled = true;
  resetStages(); setStage('generate', 'run');
  $('genLog').textContent = 'generating with ' + $('model').value + ' ...';
  try {
    const d = await post('/api/generate',
      {model: $('model').value, prompt: $('prompt').value});
    $('genLog').textContent = (d.rounds || []).map(r =>
      `round ${r.round}: ${r.status || 'error'} (${r.seconds}s)` +
      (r.error ? `\\n  ${r.error}` : '') +
      (r.lint ? `  lint: ${r.lint.length} finding(s)` : '')).join('\\n');
    if (d.doc) {
      setStage('generate', 'ok'); setStage('parse', 'ok');
      $('doc').value = JSON.stringify(d.doc, null, 2);
      const last = d.rounds[d.rounds.length - 1];
      showFindings(last.lint || []);
      setStage('lint', (last.lint || []).some(f => f.severity === 'error') ? 'fail' : 'ok');
    } else {
      setStage('generate', 'fail');
      $('genLog').textContent += '\\n' + (d.error || '');
    }
  } catch (e) { setStage('generate', 'fail'); $('genLog').textContent = String(e); }
  btn.disabled = false;
};

$('lint').onclick = async () => {
  resetStages(); setStage('parse', 'run');
  const d = await post('/api/lint', {doc: $('doc').value, theme: $('theme').value});
  if (d.error) {
    setStage('parse', 'fail');
    showFindings([{severity: 'error', rule: 'parse', where: 'document', message: d.error}]);
    return;
  }
  setStage('parse', 'ok');
  setStage('lint', d.findings.some(f => f.severity === 'error') ? 'fail' : 'ok');
  showFindings(d.findings);
};

$('render').onclick = async () => {
  const btn = $('render'); btn.disabled = true;
  setStage('render', 'run'); $('renderErr').textContent = '';
  const formats = [...document.querySelectorAll('#formats input:checked')]
    .map(i => i.value);
  const d = await post('/api/render',
    {doc: $('doc').value, theme: $('theme').value, formats});
  btn.disabled = false;
  if (d.error) { setStage('render', 'fail'); $('renderErr').textContent = d.error; return; }
  const wrap = $('files'); wrap.innerHTML = '';
  for (const f of d.files) {
    const a = document.createElement('a');
    a.href = f.url; a.textContent = `${f.name} (${(f.size/1024).toFixed(1)} KB)`;
    if (f.fmt === 'html' || f.fmt === 'pdf') {
      a.onclick = ev => { ev.preventDefault(); $('previewWrap').hidden = false;
        $('previewTitle').textContent = 'Preview - ' + f.name;
        $('preview').src = f.url; };
    } else { a.download = f.name; }
    wrap.appendChild(a);
  }
  const errs = Object.entries(d.errors || {});
  $('renderErr').textContent = errs.map(([f, m]) => `${f}: ${m}`).join('\\n');
  setStage('render', d.files.length ? 'ok' : 'fail');
  const pv = d.files.find(f => f.fmt === 'html') || d.files.find(f => f.fmt === 'pdf');
  if (pv) { $('previewWrap').hidden = false;
    $('previewTitle').textContent = 'Preview - ' + pv.name; $('preview').src = pv.url; }
};

// prefill theme
$('theme').value = JSON.stringify(JSON.parse('__THEME__'), null, 2);
</script>
</body>
</html>"""


def index(request):
    return HTMLResponse(PAGE.replace("__THEME__", json.dumps(DEFAULT.model_dump())))


app = Starlette(routes=[
    Route("/", index),
    Route("/api/models", api_models),
    Route("/api/example", api_example),
    Route("/api/generate", api_generate, methods=["POST"]),
    Route("/api/lint", api_lint, methods=["POST"]),
    Route("/api/render", api_render, methods=["POST"]),
    Mount("/files", StaticFiles(directory=OUT), name="files"),
])

if __name__ == "__main__":
    import uvicorn

    print("docloom test bench: http://127.0.0.1:8765")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
