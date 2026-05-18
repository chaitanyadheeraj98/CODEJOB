param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("plan", "implement", "status")]
  [string]$Mode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$statePath = Join-Path $repoRoot ".codex/hooks/rule_state.json"

if (-not (Test-Path -LiteralPath $statePath)) {
  [System.IO.File]::WriteAllText($statePath, '{ "implement_mode": false, "clarifications_complete": false }')
}

$state = @{
  implement_mode = $false
  clarifications_complete = $false
}

try {
  $raw = [System.IO.File]::ReadAllText($statePath)
  $parsed = $raw | ConvertFrom-Json -Depth 10
  $state.implement_mode = [bool]$parsed.implement_mode
  $state.clarifications_complete = [bool]$parsed.clarifications_complete
} catch {
  # Keep defaults if parse fails.
}

switch ($Mode) {
  "plan" {
    $state.implement_mode = $false
    $state.clarifications_complete = $false
  }
  "implement" {
    $state.implement_mode = $true
    $state.clarifications_complete = $true
  }
  "status" {
    # no-op
  }
}

$json = ($state | ConvertTo-Json)
[System.IO.File]::WriteAllText($statePath, $json)

Write-Output ("Rule mode: {0}" -f $Mode.ToUpperInvariant())
Write-Output ("implement_mode={0}" -f $state.implement_mode)
Write-Output ("clarifications_complete={0}" -f $state.clarifications_complete)
