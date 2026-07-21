#Requires -Version 5.1
# Fetch + vendor the draw.io (diagrams.net) web app for the studio's in-app,
# fully-offline diagram editor (Apache-2.0; served same-origin, zero external
# requests). The ~144MB extracted app is NOT committed -- this reproduces it,
# pinned + checksummed. Idempotent. Run from docloom-studio/.
$ErrorActionPreference = 'Stop'

$Version   = 'v30.3.14'
$WarSize   = 52472704
$WarSha256 = 'f46acbc76273bece778a39c4dda63261c10d7326160b3ffb231cb784e9b0a9eb'
$Url       = "https://github.com/jgraph/drawio/releases/download/$Version/draw.war"

$Here   = Split-Path -Parent $PSScriptRoot
$Vendor = Join-Path $Here 'server\docloom_studio\vendor\drawio'
$Stamp  = Join-Path $Vendor '.docloom-drawio-version'

if ((Test-Path (Join-Path $Vendor 'index.html')) -and (Test-Path $Stamp) -and ((Get-Content $Stamp) -eq $Version)) {
    Write-Host "draw.io $Version already vendored -> $Vendor"; exit 0
}

$Tmp = Join-Path $env:TEMP ("drawio_" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $Tmp | Out-Null
try {
    Write-Host "Downloading draw.io $Version ..."
    $War = Join-Path $Tmp 'draw.war'
    Invoke-WebRequest -Uri $Url -OutFile $War

    $sz = (Get-Item $War).Length
    if ($sz -ne $WarSize) { throw "draw.war size $sz != expected $WarSize" }
    $sha = (Get-FileHash $War -Algorithm SHA256).Hash.ToLower()
    if ($sha -ne $WarSha256) { throw "draw.war sha256 mismatch: $sha != $WarSha256" }

    Write-Host "Extracting ..."
    if (Test-Path $Vendor) { Remove-Item -Recurse -Force $Vendor }
    New-Item -ItemType Directory -Force $Vendor | Out-Null
    Expand-Archive -Path $War -DestinationPath $Vendor -Force
    Remove-Item -Recurse -Force (Join-Path $Vendor 'WEB-INF') -ErrorAction SilentlyContinue

    $offline = @"

// --- docloom-studio: force fully-offline, no cloud integrations (self-hosted) ---
urlParams['offline'] = '1'; urlParams['stealth'] = '1'; urlParams['local'] = '1';
urlParams['gapi'] = '0'; urlParams['db'] = '0'; urlParams['od'] = '0';
urlParams['gh'] = '0'; urlParams['gl'] = '0'; urlParams['tr'] = '0'; urlParams['pwa'] = '0';
"@
    Add-Content -Path (Join-Path $Vendor 'js\PreConfig.js') -Value $offline -Encoding utf8

    Set-Content -Path $Stamp -Value $Version -Encoding utf8
    Write-Host "Vendored draw.io $Version -> $Vendor (offline, ~144MB, not committed)"
}
finally { Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue }
