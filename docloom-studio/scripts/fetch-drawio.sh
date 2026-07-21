#!/usr/bin/env bash
# Fetch + vendor the draw.io (diagrams.net) web app for the studio's in-app,
# fully-offline diagram editor. draw.io is Apache-2.0; served same-origin from
# the FastAPI server (main.py mounts /drawio), it makes ZERO external requests.
# The ~144MB extracted app is NOT committed (see .gitignore) -- this script
# reproduces it, pinned + checksummed. Idempotent: re-running is a no-op once
# vendored. Run from docloom-studio/.
set -euo pipefail

VERSION="v30.3.14"
WAR_SIZE=52472704
WAR_SHA256="f46acbc76273bece778a39c4dda63261c10d7326160b3ffb231cb784e9b0a9eb"
URL="https://github.com/jgraph/drawio/releases/download/${VERSION}/draw.war"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$HERE/server/docloom_studio/vendor/drawio"
STAMP="$VENDOR/.docloom-drawio-version"

if [ -f "$VENDOR/index.html" ] && [ "$(cat "$STAMP" 2>/dev/null || true)" = "$VERSION" ]; then
  echo "draw.io $VERSION already vendored -> $VENDOR"; exit 0
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "Downloading draw.io $VERSION ..."
curl -fsSL -o "$TMP/draw.war" "$URL"

# supply-chain gate: size + sha256 must match the pin
sz=$(wc -c < "$TMP/draw.war")
[ "$sz" = "$WAR_SIZE" ] || { echo "draw.war size $sz != expected $WAR_SIZE" >&2; exit 1; }
sha=$( (sha256sum "$TMP/draw.war" 2>/dev/null || shasum -a 256 "$TMP/draw.war") | cut -d' ' -f1)
[ "$sha" = "$WAR_SHA256" ] || { echo "draw.war sha256 mismatch: $sha != $WAR_SHA256" >&2; exit 1; }

echo "Extracting ..."
rm -rf "$VENDOR"; mkdir -p "$VENDOR"
if command -v unzip >/dev/null 2>&1; then unzip -oq "$TMP/draw.war" -d "$VENDOR";
else python -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$TMP/draw.war" "$VENDOR"; fi
rm -rf "$VENDOR/WEB-INF"   # Java servlet layer + OAuth secrets; unused for static hosting

# force fully-offline defaults (belt-and-suspenders with the iframe URL params)
cat >> "$VENDOR/js/PreConfig.js" <<'PRECONFIG'

// --- docloom-studio: force fully-offline, no cloud integrations (self-hosted) ---
urlParams['offline'] = '1'; urlParams['stealth'] = '1'; urlParams['local'] = '1';
urlParams['gapi'] = '0'; urlParams['db'] = '0'; urlParams['od'] = '0';
urlParams['gh'] = '0'; urlParams['gl'] = '0'; urlParams['tr'] = '0'; urlParams['pwa'] = '0';
PRECONFIG

echo "$VERSION" > "$STAMP"
echo "Vendored draw.io $VERSION -> $VENDOR (offline, ~144MB, not committed)"
