#!/usr/bin/env bash
# SessionStart hook: provisions dependencies for Claude Code web sessions.
#
# Runs only in the remote (web) container, gated on CLAUDE_CODE_REMOTE=true,
# so it is a no-op on local machines. Idempotent and non-interactive, so it is
# safe to re-run on every session start.
set -euo pipefail

# Only provision in the Claude Code web environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Resolve the repo root (two levels up from .claude/hooks/).
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

echo "[session-start] Installing Python packages (docloom + docloom-studio, editable)..."
# Resolve both packages in one pass so docloom-studio's docloom[pdf]>=0.2
# requirement is satisfied by the local ./docloom checkout instead of PyPI.
uv pip install --system \
  -e "./docloom[pdf,mcp,dev]" \
  -e "./docloom-studio[dev]"

echo "[session-start] Installing frontend dependencies (docloom-studio/web)..."
npm --prefix docloom-studio/web install

echo "[session-start] Provisioning complete."
