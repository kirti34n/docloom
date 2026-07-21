#Requires -Version 5.1
<#
.SYNOPSIS
    One-command bring-up for docloom studio: dependencies, web build, and server.

.DESCRIPTION
    Brings the whole app up on http://127.0.0.1:8899 and opens a browser.
    Idempotent and fast on the common path: if the venv and web build already
    exist it just launches. On a fresh checkout it creates the studio
    virtualenv, installs the engine + studio (editable), builds the frontend,
    then starts the single-process server that serves both the API and the SPA.

    It ALWAYS verifies the SVG rasterizer (resvg) is importable, because
    without it every generated diagram, chart, and infographic exports as a
    silent blank -- a capability gap no test catches. This self-heal makes that
    impossible to reintroduce by a partial `pip install`.

.PARAMETER Rebuild
    Force a fresh web build even if web/dist already exists.

.PARAMETER Setup
    Force dependency (re)install even if the studio venv already exists.

.PARAMETER Port
    Port to serve on (default 8899).

.PARAMETER NoBrowser
    Do not open a browser window on start.

.EXAMPLE
    .\studio.ps1
    Bring the app up (build only if needed) and open it.

.EXAMPLE
    .\studio.ps1 -Rebuild -Port 9000
    Rebuild the frontend and serve on port 9000.
#>
[CmdletBinding()]
param(
    [switch]$Rebuild,
    [switch]$Setup,
    [int]$Port = 8899,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$Root   = $PSScriptRoot
$Studio = Join-Path $Root 'docloom-studio'
$Web    = Join-Path $Studio 'web'
$VenvPy = Join-Path $Studio '.venv\Scripts\python.exe'

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "xx  $m" -ForegroundColor Red; exit 1 }

# --- 0. prerequisites ------------------------------------------------------
foreach ($tool in 'uv', 'node', 'npm') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Die "$tool is not on PATH. Install it first (uv: https://docs.astral.sh/uv, node 22+: https://nodejs.org)."
    }
}

# --- 1. studio venv + Python dependencies ----------------------------------
# The studio runs from its own venv, which carries docloom installed editable.
# We install the engine with the [pdf,diagrams] extras explicitly: the studio's
# own `docloom[pdf,diagrams]` dependency is treated as already-satisfied by an
# editable docloom, so an earlier `pip install -e ../docloom` without the extra
# silently omits resvg -- exactly the blank-export trap this script guards.
if ($Setup -or -not (Test-Path $VenvPy)) {
    Step 'Creating studio virtualenv and installing dependencies (first run: a few minutes)...'
    uv venv --directory $Studio
    if ($LASTEXITCODE -ne 0) { Die 'uv venv failed.' }
    uv pip install --python $VenvPy -e "$Root\docloom[pdf,diagrams]"
    if ($LASTEXITCODE -ne 0) { Die 'installing the docloom engine failed.' }
    uv pip install --python $VenvPy -e "$Studio"
    if ($LASTEXITCODE -ne 0) { Die 'installing docloom-studio failed.' }
}

# --- 2. self-heal the SVG rasterizer (never let exports blank silently) -----
Step 'Verifying SVG rasterizer (diagram / chart / infographic export)...'
$hasResvg = (& $VenvPy -c "import importlib.util as u; print(1 if u.find_spec('resvg_py') else 0)").Trim()
if ($hasResvg -ne '1') {
    Warn 'resvg not installed -- installing so diagrams/charts/infographics do not export blank.'
    uv pip install --python $VenvPy "resvg-py>=0.3.3"
    if ($LASTEXITCODE -ne 0) { Die 'installing resvg-py failed.' }
}

# --- 3. web frontend build (served by the server on one port) --------------
$IndexHtml = Join-Path $Web 'dist\index.html'
if ($Rebuild -or -not (Test-Path $IndexHtml)) {
    if (-not (Test-Path (Join-Path $Web 'node_modules'))) {
        Step 'Installing web dependencies (npm install)...'
        npm --prefix $Web install
        if ($LASTEXITCODE -ne 0) { Die 'npm install failed.' }
    }
    Step 'Building web frontend (tsc -b + vite build)...'
    npm --prefix $Web run build
    if ($LASTEXITCODE -ne 0) { Die 'web build failed.' }
} else {
    Step 'Web build present (pass -Rebuild to force a fresh build).'
}

# --- 4. launch -------------------------------------------------------------
$env:DOCLOOM_STUDIO_PORT = "$Port"
if ($NoBrowser) { $env:DOCLOOM_STUDIO_NO_BROWSER = '1' }
Step "Starting docloom studio on http://127.0.0.1:$Port  (Ctrl+C to stop)"
& $VenvPy -m docloom_studio.main
