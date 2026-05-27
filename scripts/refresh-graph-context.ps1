param(
  [string]$GraphifyCommand = "graphify",
  [switch]$SkipGraphify,
  [switch]$FailOnStale
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$fullReportPath = "graphify-out/GRAPH_REPORT.md"
$compactReportPath = "graphify-out/GRAPH_REPORT_COMPACT.md"
$compactBuilderScript = "scripts/build-graphify-compact.ps1"

function Get-LatestCommitUtc {
  try {
    $epoch = (git log -1 --format=%ct 2>$null)
    if (-not $epoch) {
      return $null
    }
    $seconds = [int64]$epoch.Trim()
    return [DateTimeOffset]::FromUnixTimeSeconds($seconds).UtcDateTime
  } catch {
    return $null
  }
}

if (-not $SkipGraphify) {
  Write-Output "Running Graphify refresh command: $GraphifyCommand"
  Invoke-Expression $GraphifyCommand
}

if (-not (Test-Path -LiteralPath $fullReportPath)) {
  throw "Missing $fullReportPath after Graphify step."
}

if (-not (Test-Path -LiteralPath $compactBuilderScript)) {
  throw "Missing compact builder script: $compactBuilderScript"
}

try {
  $builderOutput = & powershell -NoProfile -ExecutionPolicy Bypass -File $compactBuilderScript -InputPath $fullReportPath -OutputPath $compactReportPath 2>&1
  $builderExitCode = $LASTEXITCODE
  if ($builderOutput) {
    $builderOutput | Write-Output
  }
  if ($builderExitCode -ne 0) {
    $originalErrorText = ($builderOutput | Out-String).Trim()
    throw ("Compact report rebuild failed after retry attempts. Possible file lock on '{0}'. Close any open preview/editor tabs for Graphify report files, stop concurrent Graphify watchers, and rerun this command. Original error: {1}" -f $compactReportPath, $originalErrorText)
  }
} catch {
  throw ("Compact report rebuild failed after retry attempts. Possible file lock on '{0}'. Close any open preview/editor tabs for Graphify report files, stop concurrent Graphify watchers, and rerun this command. Original error: {1}" -f $compactReportPath, $_.Exception.Message)
}

if (-not (Test-Path -LiteralPath $compactReportPath)) {
  throw "Compact report was not generated: $compactReportPath"
}

$latestCommitUtc = Get-LatestCommitUtc
$fullReportUtc = (Get-Item -LiteralPath $fullReportPath).LastWriteTimeUtc
$compactReportUtc = (Get-Item -LiteralPath $compactReportPath).LastWriteTimeUtc

$isStale = $false
if ($latestCommitUtc) {
  if ($fullReportUtc -lt $latestCommitUtc -or $compactReportUtc -lt $latestCommitUtc) {
    $isStale = $true
    Write-Warning ("Graph context appears stale. Latest commit UTC: {0:u}; full report UTC: {1:u}; compact report UTC: {2:u}" -f $latestCommitUtc, $fullReportUtc, $compactReportUtc)
    Write-Warning "Suggested action: rerun Graphify now or run this script without -SkipGraphify."
  }
}

Write-Output "Graph context refresh complete."
Write-Output "Full report:    $fullReportPath"
Write-Output "Compact report: $compactReportPath"
if ($latestCommitUtc) {
  Write-Output ("Latest commit UTC: {0:u}" -f $latestCommitUtc)
}

if ($FailOnStale -and $isStale) {
  throw "Graph context is stale and -FailOnStale was specified."
}
