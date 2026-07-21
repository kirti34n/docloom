#!/usr/bin/env bash
# One-command bring-up for docloom studio (Git Bash / macOS / Linux).
# On Windows prefer studio.ps1 in PowerShell. Do NOT run this under WSL against
# a node_modules installed from Windows: rollup/esbuild ship native binaries per
# platform and the build will fail with MODULE_NOT_FOUND. Build where you
# installed.
#
# Flags: --rebuild (force web build)  --setup (force dep reinstall)
#        --port N   --no-browser
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDIO="$ROOT/docloom-studio"
WEB="$STUDIO/web"

REBUILD=0; SETUP=0; PORT=8899; NO_BROWSER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --rebuild) REBUILD=1 ;;
    --setup) SETUP=1 ;;
    --port) shift; PORT="$1" ;;
    --no-browser) NO_BROWSER=1 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

step() { printf '==> %s\n' "$1"; }
die()  { printf 'xx  %s\n' "$1" >&2; exit 1; }

for t in uv node npm; do
  command -v "$t" >/dev/null 2>&1 || die "$t is not on PATH (uv: https://docs.astral.sh/uv, node 22+: https://nodejs.org)."
done

# Windows venvs live under Scripts/, POSIX under bin/.
if [ -x "$STUDIO/.venv/Scripts/python.exe" ]; then
  VENV_PY="$STUDIO/.venv/Scripts/python.exe"
else
  VENV_PY="$STUDIO/.venv/bin/python"
fi

# 1. venv + deps (install the engine WITH [pdf,diagrams] explicitly; see studio.ps1).
if [ "$SETUP" = "1" ] || [ ! -x "$VENV_PY" ]; then
  step "Creating studio virtualenv and installing dependencies (first run: a few minutes)..."
  uv venv --directory "$STUDIO"
  if [ -x "$STUDIO/.venv/Scripts/python.exe" ]; then VENV_PY="$STUDIO/.venv/Scripts/python.exe"; else VENV_PY="$STUDIO/.venv/bin/python"; fi
  uv pip install --python "$VENV_PY" -e "$ROOT/docloom[pdf,diagrams]"
  uv pip install --python "$VENV_PY" -e "$STUDIO"
fi

# 2. self-heal the SVG rasterizer so exports never silently blank.
step "Verifying SVG rasterizer (diagram / chart / infographic export)..."
if [ "$("$VENV_PY" -c 'import importlib.util as u; print(1 if u.find_spec("resvg_py") else 0)')" != "1" ]; then
  printf '!!  resvg not installed -- installing so diagrams/charts/infographics do not export blank.\n'
  uv pip install --python "$VENV_PY" "resvg-py>=0.3.3"
fi

# 3. web build.
if [ "$REBUILD" = "1" ] || [ ! -f "$WEB/dist/index.html" ]; then
  [ -d "$WEB/node_modules" ] || { step "Installing web dependencies (npm install)..."; npm --prefix "$WEB" install; }
  step "Building web frontend (tsc -b + vite build)..."
  npm --prefix "$WEB" run build
else
  step "Web build present (pass --rebuild to force a fresh build)."
fi

# 4. launch.
export DOCLOOM_STUDIO_PORT="$PORT"
[ "$NO_BROWSER" = "1" ] && export DOCLOOM_STUDIO_NO_BROWSER=1
step "Starting docloom studio on http://127.0.0.1:$PORT  (Ctrl+C to stop)"
exec "$VENV_PY" -m docloom_studio.main
