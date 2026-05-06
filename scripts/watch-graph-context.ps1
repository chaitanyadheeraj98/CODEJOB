param(
  [string]$RepoRoot = ".",
  [int]$DebounceSeconds = 12,
  [string]$GraphifyCommand = "graphify",
  [switch]$SkipGraphify
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$refreshScript = Join-Path $root "scripts\refresh-graph-context.ps1"
if (-not (Test-Path -LiteralPath $refreshScript)) {
  throw "Missing refresh script at '$refreshScript'."
}

$excludeFragments = @(
  "\.git\",
  "\graphify-out\",
  "\node_modules\",
  "\.venv\",
  "\venv\",
  "\dist\",
  "\build\",
  "\__pycache__\",
  "\.pytest_cache\"
)

function Should-IgnorePath {
  param([string]$PathValue)
  $normalized = $PathValue.Replace("/", "\").ToLowerInvariant()
  foreach ($frag in $excludeFragments) {
    if ($normalized.Contains($frag.ToLowerInvariant())) {
      return $true
    }
  }
  return $false
}

function Invoke-Refresh {
  param([string]$Reason)
  Write-Host ""
  Write-Host ("[{0}] Change detected: {1}" -f (Get-Date -Format "u"), $Reason) -ForegroundColor Cyan
  if ($SkipGraphify) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $refreshScript -SkipGraphify
  } else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $refreshScript -GraphifyCommand $GraphifyCommand
  }
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Refresh command failed. Watcher will continue running."
  } else {
    Write-Host ("[{0}] Graph context refreshed." -f (Get-Date -Format "u")) -ForegroundColor Green
  }
}

$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $root
$watcher.Filter = "*"
$watcher.IncludeSubdirectories = $true
$watcher.NotifyFilter = [System.IO.NotifyFilters]'FileName, DirectoryName, LastWrite, Size'
$watcher.EnableRaisingEvents = $true

$script:lastEventUtc = [DateTime]::MinValue
$script:pendingReason = $null
$script:refreshInProgress = $false

$action = {
  $fullPath = $Event.SourceEventArgs.FullPath
  if (Should-IgnorePath -PathValue $fullPath) {
    return
  }

  $nowUtc = [DateTime]::UtcNow
  $age = ($nowUtc - $script:lastEventUtc).TotalSeconds

  if ($age -ge $DebounceSeconds -and -not $script:refreshInProgress) {
    $script:lastEventUtc = $nowUtc
    $script:refreshInProgress = $true
    try {
      Invoke-Refresh -Reason "$($Event.SourceEventArgs.ChangeType) $fullPath"
    } finally {
      $script:refreshInProgress = $false
    }
    return
  }

  $script:pendingReason = "$($Event.SourceEventArgs.ChangeType) $fullPath"
}

$handlers = @()
$handlers += Register-ObjectEvent -InputObject $watcher -EventName Changed -Action $action
$handlers += Register-ObjectEvent -InputObject $watcher -EventName Created -Action $action
$handlers += Register-ObjectEvent -InputObject $watcher -EventName Deleted -Action $action
$handlers += Register-ObjectEvent -InputObject $watcher -EventName Renamed -Action $action

Write-Host "Watching '$root' for source changes..." -ForegroundColor Yellow
Write-Host "Debounce: $DebounceSeconds second(s)." -ForegroundColor Yellow
if ($SkipGraphify) {
  Write-Host "Mode: compact refresh only (-SkipGraphify)." -ForegroundColor Yellow
} else {
  Write-Host "Mode: full Graphify refresh using command '$GraphifyCommand'." -ForegroundColor Yellow
}
Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow

try {
  while ($true) {
    Start-Sleep -Seconds 2
    if ($script:pendingReason -and -not $script:refreshInProgress) {
      $age = ([DateTime]::UtcNow - $script:lastEventUtc).TotalSeconds
      if ($age -ge $DebounceSeconds) {
        $reason = $script:pendingReason
        $script:pendingReason = $null
        $script:lastEventUtc = [DateTime]::UtcNow
        $script:refreshInProgress = $true
        try {
          Invoke-Refresh -Reason $reason
        } finally {
          $script:refreshInProgress = $false
        }
      }
    }
  }
} finally {
  foreach ($h in $handlers) {
    try {
      Unregister-Event -SourceIdentifier $h.Name -ErrorAction SilentlyContinue
      $h | Remove-Job -Force -ErrorAction SilentlyContinue
    } catch {
    }
  }
  $watcher.Dispose()
}
