param(
    [Parameter(Mandatory = $true)]
    [string]$RemoteHost,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^baseline-\d{8}T\d{6}Z$')]
    [string]$BackupId,

    [string]$RemoteRoot = '~/temple-backups',
    [string]$LocalRoot = 'C:\Backups\temple'
)

$ErrorActionPreference = 'Stop'
$destination = Join-Path $LocalRoot $BackupId
if (Test-Path -LiteralPath $destination) {
    throw "Destination already exists: $destination"
}

New-Item -ItemType Directory -Path $destination | Out-Null
$principal = "$env:USERDOMAIN\$env:USERNAME"
& icacls $destination /inheritance:r /grant:r "${principal}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to restrict local backup permissions'
}

$archiveName = "$BackupId.tar.gz"
$checksumName = "$archiveName.sha256"
$reportName = "$BackupId-baseline-report.json"
$restoreReportName = "$BackupId-restore-verification.json"

foreach ($name in @($archiveName, $checksumName, $reportName, $restoreReportName)) {
    & scp "${RemoteHost}:${RemoteRoot}/$name" $destination
    if ($LASTEXITCODE -ne 0) {
        throw "SCP failed for $name"
    }
}

$expectedLine = (Get-Content -LiteralPath (Join-Path $destination $checksumName) -Raw).Trim()
$expectedHash = ($expectedLine -split '\s+')[0].ToUpperInvariant()
$actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $destination $archiveName)).Hash
if ($actualHash -ne $expectedHash) {
    throw "Checksum mismatch: expected $expectedHash, received $actualHash"
}

[pscustomobject]@{
    BackupId = $BackupId
    Destination = $destination
    Sha256 = $actualHash
    Verified = $true
    ServerArchive = "$RemoteRoot/$archiveName"
}
