# docloom workspace

A combined workspace for two related projects that ship together.

## Projects

- **docloom** (`docloom/`): the document output layer for AI apps. Your LLM emits a validated JSON schema and docloom deterministically renders PPTX, DOCX, XLSX, PDF, and HTML, with a linter that catches broken slides before anyone sees them.
- **docloom-studio** (`docloom-studio/`): a free, local-first AI document studio. Notebooks built from your own sources or agent research generate editable presentations, documents, diagrams, and infographics, all exported through docloom to real Office and PDF files.

docloom-studio builds on docloom: it depends on `docloom[pdf]>=0.2`, and in this workspace that dependency resolves to the local `docloom/` checkout rather than PyPI.

## Layout

```
docloom/          library: the deterministic rendering engine (Python)
docloom-studio/   app: FastAPI backend + React (Vite) frontend
.claude/          Claude Code config (SessionStart provisioning hook)
```

## Quickstart

Install both Python packages together (editable) so docloom-studio resolves docloom from the local checkout:

```bash
pip install -e "./docloom[pdf,mcp,dev]" -e "./docloom-studio[dev]"
```

Run the library tests:

```bash
cd docloom && pytest -q
```

Run the studio (FastAPI backend plus Vite frontend):

```bash
docloom-studio                                        # FastAPI server
cd docloom-studio/web && npm install && npm run dev   # Vite dev server
```

## Claude Code web sessions

`.claude/hooks/session-start.sh` provisions everything a fresh Claude Code web session needs: both Python packages editable in one resolve, plus the frontend `npm install`. It is gated on `CLAUDE_CODE_REMOTE=true`, so it runs only in the web container and is a no-op on your local machine.
