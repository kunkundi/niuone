param([Parameter(Mandatory = $true)][string]$Root)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WebDir = Join-Path $Root "web"
$DistDir = Join-Path $WebDir "dist"
$Stamp = Join-Path $DistDir ".niuone-build"
$PnpmVersion = "11.15.1"
$InstalledLock = Join-Path $WebDir "node_modules\.pnpm\lock.yaml"
$LocalVite = Join-Path $WebDir "node_modules\.bin\vite.cmd"
$Inputs = @(
    (Join-Path $WebDir "src"),
    (Join-Path $WebDir "index.html"),
    (Join-Path $WebDir "package.json"),
    (Join-Path $WebDir "pnpm-lock.yaml"),
    (Join-Path $WebDir "pnpm-workspace.yaml"),
    (Join-Path $WebDir "vite.config.js"),
    (Join-Path $Root "frontend\dashboard.css"),
    (Join-Path $Root "frontend\admin.css")
)

$NeedsBuild = -not (Test-Path -LiteralPath (Join-Path $DistDir "index.html")) -or -not (Test-Path -LiteralPath $Stamp)
if (-not $NeedsBuild) {
    $StampTime = (Get-Item -LiteralPath $Stamp).LastWriteTimeUtc
    foreach ($InputPath in $Inputs) {
        if (-not (Test-Path -LiteralPath $InputPath)) { continue }
        $Newest = Get-ChildItem -LiteralPath $InputPath -Recurse -File |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -First 1
        if ($Newest -and $Newest.LastWriteTimeUtc -gt $StampTime) {
            $NeedsBuild = $true
            break
        }
    }
}

if (-not $NeedsBuild) { exit 0 }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js 22.12+ is required to build the Vue frontend."
}

if (Get-Command pnpm -ErrorAction SilentlyContinue) {
    & pnpm --dir $WebDir install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & pnpm --dir $WebDir run build
} elseif (
    (Test-Path -LiteralPath $LocalVite) -and
    (Test-Path -LiteralPath $InstalledLock) -and
    ((Get-FileHash -LiteralPath (Join-Path $WebDir "pnpm-lock.yaml") -Algorithm SHA256).Hash -eq
        (Get-FileHash -LiteralPath $InstalledLock -Algorithm SHA256).Hash)
) {
    Write-Host "Using the existing locked frontend dependencies."
    Push-Location $WebDir
    try {
        & $LocalVite build
    } finally {
        Pop-Location
    }
} elseif (Get-Command npx -ErrorAction SilentlyContinue) {
    & npx --yes "pnpm@$PnpmVersion" --dir $WebDir install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & npx --yes "pnpm@$PnpmVersion" --dir $WebDir run build
} else {
    throw "pnpm or npx is required to build the Vue frontend."
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

New-Item -ItemType File -Path $Stamp -Force | Out-Null
