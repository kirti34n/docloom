# docloom studio

![Python](https://img.shields.io/badge/python-3.11%2B-4F46E5)
![Node](https://img.shields.io/badge/node-22%2B-4F46E5)
![License](https://img.shields.io/badge/license-MIT-0D9488)

A free, local-first AI document studio: think NotebookLM crossed with Gamma. Add sources to a
notebook, chat with answers that cite the evidence, and generate editable decks, documents,
spreadsheets, diagrams, and infographics, exported through [docloom](../docloom) to real
PPTX/DOCX/XLSX/PDF/HTML/MD, plus two-host podcast audio overviews synthesized straight to `.wav`
(podcasts never go through docloom's renderers). No account with a third party, no cloud
dependency: a local model runs it fully offline.

![docloom studio: a notebook with sources, grounded chat, and one-click guides](../docs/assets/guides.png)

## How it fits together

docloom studio owns sources, retrieval, chat, and generation; the docloom engine turns whatever
gets generated into a validated document and renders it deterministically.

![Architecture: studio orchestrates sources and generation, the engine renders the validated IR](../docs/assets/architecture.png)

### Visual diagram editor — real draw.io, self-hosted and offline

Diagrams generate as the engine's coordinate-free `Diagram` IR, and you edit them in **the real
[draw.io](https://www.drawio.com) editor, embedded in the studio and running fully offline** — no
cloud, no CDN, no account (verified: it boots with zero external network requests). Generation solves
the layout for you (`native`, or the compact Graphviz **`dot`** backend for dense graphs) and seeds
it straight into draw.io as mxGraph XML; from there you have draw.io's complete toolset. Every edit
writes back a `render.svg` through the same path decks already bake, so the diagram you edit is
exactly the one your presentation ships.

![The real draw.io editor, self-hosted offline in the studio, seeded with a docloom architecture diagram](../docs/assets/diagram-editor.png)

The ~144MB draw.io app is fetched once by `scripts/fetch-drawio.*` (pinned + checksummed, Apache-2.0),
not committed — the launcher self-heals it on first run. Older diagrams saved as
[D2](https://d2lang.com) source still open in the legacy text editor.

## Features

- **Notebooks** with your own sources (file, URL, or pasted text) or agent web research: the agent
  plans searches, fetches pages, and keeps them as cited sources, no API key required
- **Grounded chat**: embeddings + ranking retrieve the relevant chunks, and every answer cites
  where it came from
- **One-click guides**: study guide, briefing, FAQ, timeline, and mind map, each a grounded
  generation from your sources
- **Six artifact kinds**: presentations, documents, spreadsheets, architecture diagrams (edited on
  an in-app visual canvas, see "How it fits together" above), infographics, and two-host podcast
  audio overviews
- **A brand kit** (logo, accent color, fonts) applied to every generation and every export
- **Local-first**: SQLite by default and nothing to configure; Postgres is a `DOCLOOM_DB_URL` away
  for a multi-node deployment

## Quickstart

One command brings the whole app up — it installs dependencies, builds the frontend, and starts
the server (API + SPA) on one port, then opens a browser. Run it from the **repository root**:

```powershell
# Windows (PowerShell)
.\studio.ps1
```

```bash
# macOS / Linux / Git Bash
./studio.sh
```

It needs [`uv`](https://docs.astral.sh/uv), [Node 22+](https://nodejs.org), and npm on `PATH`.
The first run takes a few minutes (venv + npm install + web build); every run after that skips
straight to launch. Useful flags: `-Rebuild` / `--rebuild` (force a fresh web build),
`-Port 9000` / `--port 9000`, `-NoBrowser` / `--no-browser`, `-Setup` / `--setup` (force a
dependency reinstall). On the first visit, register an account; everything you create lives in a
workspace scoped to your login.

The launcher also **verifies the SVG rasterizer (resvg) on every start**. Without it, generated
diagrams, charts, and infographics export as silent blanks — a capability gap no test catches —
so the script installs it if a partial setup left it out.

<details>
<summary>What the one command does (equivalent manual steps)</summary>

```bash
git clone https://github.com/kirti34n/docloom.git
cd docloom

# from docloom-studio/, create the studio venv and install both packages editable:
uv venv
uv pip install -e "../docloom[pdf,diagrams,dotlayout]"  # the render engine. PPTX/DOCX/XLSX and
                                               # HTML/MD need NO extra (their libs are core / pure
                                               # Python); only these three add capability: [pdf] =
                                               # Typst (PDF), [diagrams] = the resvg rasterizer
                                               # (charts/diagrams as real pictures in PPTX/DOCX,
                                               # and non-blank studio diagram/infographic exports),
                                               # [dotlayout] = the compact Graphviz dot layout for
                                               # complex diagrams. Installing the engine WITHOUT the
                                               # extras (`-e ../docloom` alone) satisfies the
                                               # dependency by NAME and silently omits them -- which
                                               # is why the launcher re-checks resvg + pygraphviz on
                                               # every start.
uv pip install -e "."                          # the studio backend. Extras: [dev] (pytest),
                                               # [ingest] (EPUB/YouTube sources), [podcast]
                                               # (kokoro + soundfile audio), [postgres].

cd web && npm install && npm run build && cd ..

python -m docloom_studio.main                  # http://127.0.0.1:8899
```
</details>

The first run creates its data directory (SQLite DB, uploaded sources, exports) under
`%LOCALAPPDATA%\docloom-studio` (Windows) or `~/docloom-studio` (macOS/Linux); set
`DOCLOOM_STUDIO_HOME` to point it somewhere else.

## Configuring a model

Open **Settings** in the running app and pick a provider. Generation and embeddings are
configured separately, and the model list is fetched live from whichever base URL you set.

| Provider | Notes |
| --- | --- |
| **Ollama** (default) | Fully offline. Install [Ollama](https://ollama.com), then `ollama pull qwen3.5:9b` and `ollama pull nomic-embed-text`. |
| **llama.cpp server** | The most reliable local option: real JSON-schema enforcement instead of a prompt-injected schema. |
| **LM Studio** | Enable its local server and point the base URL at it. |
| **OpenAI** / **Anthropic** | Paste an API key; nothing local to install. |
| **Google Gemini** | Paste an API key (a free tier is available); schema-enforced structured output. |

## Docker

The image needs both `docloom/` and `docloom-studio/` in its build context, since the studio
depends on the engine and the engine isn't published to PyPI. Build from the **repository root**
(the parent of both directories), not from inside `docloom-studio/`:

```bash
docker build -t docloom-studio -f docloom-studio/Dockerfile .
docker run -p 8899:8899 -v docloom-data:/data docloom-studio
```

Data (the SQLite DB, uploaded assets, exports) lives under `/data` in the container; the volume
mount above persists it across restarts.

## Tests

```bash
pytest -q                                  # from docloom-studio/, backend
cd web && npm run lint && npx vitest run   # frontend
```

## Repository

See the [root README](../README.md) for the engine (`docloom/`) this app is built on, and
[`examples/`](../examples/) for runnable samples of what gets rendered.

## License

MIT.
