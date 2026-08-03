[CmdletBinding()]
param(
    [string]$SourceBaseUrl = "https://www.sv-innokenty.ru/",
    [Parameter(Mandatory = $true)]
    [string]$PreprodRoot,
    [string]$BundleId = "",
    [int]$MaxMediaMb = 200,
    [int]$MediaWorkers = 4,
    [switch]$SkipDependencySync,
    [switch]$SkipBrowserInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not [System.IO.Path]::IsPathRooted($PreprodRoot)) {
    throw "PreprodRoot must be an absolute Windows path"
}
$PreprodRoot = [System.IO.Path]::GetFullPath($PreprodRoot)
$ExpectedProjectDir = [System.IO.Path]::GetFullPath((Join-Path $PreprodRoot "release-checkout"))
if (-not [string]::Equals($ProjectDir, $ExpectedProjectDir, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Run this script only from the dedicated <PreprodRoot>\release-checkout, never from the development checkout"
}
$OutputRoot = [System.IO.Path]::GetFullPath((Join-Path $PreprodRoot "source-bundles"))
$RuntimeRoot = [System.IO.Path]::GetFullPath((Join-Path $PreprodRoot "runtime"))
$env:UV_CACHE_DIR = Join-Path $RuntimeRoot "uv-cache"
$env:UV_PROJECT_ENVIRONMENT = Join-Path $RuntimeRoot "venv"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $RuntimeRoot "playwright-browsers"
if (-not $BundleId) {
    $BundleId = "public-source-{0}" -f (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
}
if ($BundleId -notmatch '^public-source-[0-9]{8}T[0-9]{6}Z$') {
    throw "BundleId must match public-source-YYYYMMDDTHHMMSSZ"
}
if ($SourceBaseUrl -ne "https://www.sv-innokenty.ru/") {
    throw "The current importer is contracted for https://www.sv-innokenty.ru/ only"
}
if ($MaxMediaMb -lt 1 -or $MediaWorkers -lt 1) {
    throw "MaxMediaMb and MediaWorkers must be positive"
}

foreach ($Command in @("uv", "git", "tar")) {
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "Required command is missing: $Command"
    }
}

$OutputDrive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($OutputRoot))
$MinimumFreeBytes = 15GB
if (-not $OutputDrive.IsReady -or $OutputDrive.AvailableFreeSpace -lt $MinimumFreeBytes) {
    throw "At least 15 GiB of free space is required on the source-bundle drive"
}
$BundleDir = Join-Path $OutputRoot $BundleId
$Archive = "$BundleDir.tar.gz"
$Checksum = "$Archive.sha256"
if ((Test-Path $BundleDir) -or (Test-Path $Archive) -or (Test-Path $Checksum)) {
    throw "Bundle destination already exists: $BundleId"
}

New-Item -ItemType Directory -Path (Join-Path $BundleDir "media\legacy") -Force | Out-Null

function Invoke-Checked {
    param([string]$Name, [scriptblock]$Action)
    Write-Host "==> $Name"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

Push-Location $ProjectDir
try {
    $GitSha = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $GitSha -notmatch '^[0-9a-f]{40}$') {
        throw "Unable to determine Git SHA"
    }
    $GitBranch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $GitBranch -ne "main") {
        throw "Source bundles must be built from branch main"
    }
    $OriginMainSha = (& git rev-parse origin/main).Trim()
    if ($LASTEXITCODE -ne 0 -or $OriginMainSha -ne $GitSha) {
        throw "Local main must exactly match origin/main"
    }
    $WorkingTreeChanges = (& git status --porcelain --untracked-files=normal)
    if ($LASTEXITCODE -ne 0 -or $WorkingTreeChanges) {
        throw "The Git working tree must be clean so the bundle can be reproduced from its Git SHA"
    }
    if (-not $SkipDependencySync) {
        Invoke-Checked "Synchronize Python dependencies" { uv sync --group dev --group crawl }
    }
    if (-not $SkipBrowserInstall) {
        Invoke-Checked "Install Playwright Chromium" { uv run playwright install chromium }
    }

    $Crawl = Join-Path $BundleDir "legacy-crawl-checkpoint.json"
    $MediaManifest = Join-Path $BundleDir "legacy-media-manifest.json"
    $Plan = Join-Path $BundleDir "full-import-plan.json"
    $Structured = Join-Path $BundleDir "structured-sections.json"
    $BundledRouteTemplate = Join-Path $BundleDir "current-sections.json"
    $StructuredDryRun = Join-Path $BundleDir "structured-import-dry-run.json"
    $InventoryJson = Join-Path $BundleDir "legacy-inventory.json"
    $InventoryMd = Join-Path $BundleDir "legacy-inventory.md"
    $MissingCsv = Join-Path $BundleDir "missing-legacy-media.csv"
    $MissingMd = Join-Path $BundleDir "missing-legacy-media.md"
    $KnownRoutes = Join-Path $BundleDir "known-public-routes.json"

    Copy-Item -LiteralPath "outputs\legacy-inventory.json" -Destination $KnownRoutes
    Invoke-Checked "Fresh read-only Playwright crawl" {
        uv run python -m server.legacy_crawl --base-url $SourceBaseUrl --output $Crawl --fresh --seed-inventory $KnownRoutes
    }
    Invoke-Checked "Rebuild structured sections from the fresh crawl" {
        uv run python scripts/build_structured_source.py --crawl $Crawl --route-template current-sections.json --output $Structured --bundle-route-template $BundledRouteTemplate
    }
    Invoke-Checked "Build legacy inventory" {
        uv run python -m server.legacy_inventory --crawl $Crawl --sections $Structured --json $InventoryJson --markdown $InventoryMd
    }
    Invoke-Checked "Mirror all discovered media" {
        uv run python -m server.media_mirror --source $Crawl --destination (Join-Path $BundleDir "media\legacy") --manifest $MediaManifest --execute --max-mb $MaxMediaMb --workers $MediaWorkers
    }
    Invoke-Checked "Build full import plan" {
        uv run python -m server.full_import --source $Crawl --media-manifest $MediaManifest --plan $Plan
    }
    Write-Host "==> Validate structured import plan"
    $StructuredDryRunLines = & uv run python -m server.importer --source $Structured --media-manifest $MediaManifest --dry-run
    if ($LASTEXITCODE -ne 0) {
        throw "Structured import dry-run failed with exit code $LASTEXITCODE"
    }
    [System.IO.File]::WriteAllText(
        $StructuredDryRun,
        (($StructuredDryRunLines -join [Environment]::NewLine) + [Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false)
    )
    Invoke-Checked "Build missing-media report" {
        uv run python -m server.media_report --plan $Plan --manifest $MediaManifest --csv $MissingCsv --markdown $MissingMd
    }
    Invoke-Checked "Create immutable bundle manifest" {
        uv run python scripts/validate_source_bundle.py create $BundleDir --source-url $SourceBaseUrl --git-sha $GitSha
    }
    Invoke-Checked "Verify source bundle before packing" {
        uv run python scripts/validate_source_bundle.py verify $BundleDir
    }
    Invoke-Checked "Create source bundle archive" {
        tar -C $BundleDir -czf $Archive .
    }
    $ArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
    $ChecksumLine = "$ArchiveHash  $([System.IO.Path]::GetFileName($Archive))`n"
    [System.IO.File]::WriteAllText($Checksum, $ChecksumLine, [System.Text.UTF8Encoding]::new($false))

    [ordered]@{
        ok = $true
        bundle_id = $BundleId
        source_url = $SourceBaseUrl
        git_sha = $GitSha
        preprod_root = $PreprodRoot
        release_checkout = $ProjectDir
        runtime_root = $RuntimeRoot
        archive = $Archive
        checksum = $Checksum
        sha256 = $ArchiveHash
    } | ConvertTo-Json
}
finally {
    Pop-Location
}
