param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-RepoRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

function Read-TextSafe([string]$path) {
  if (-not (Test-Path -LiteralPath $path)) { return "" }
  try { return [System.IO.File]::ReadAllText($path) } catch { return "" }
}

function Get-HookPayloadText {
  $envCandidates = @(
    "CODEX_HOOK_PAYLOAD",
    "OPENAI_HOOK_PAYLOAD",
    "HOOK_PAYLOAD"
  )
  foreach ($name in $envCandidates) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
  }

  try {
    if (-not [Console]::IsInputRedirected) { return "" }
    $stdin = [Console]::In.ReadToEnd()
    if (-not [string]::IsNullOrWhiteSpace($stdin)) { return $stdin }
  } catch {
    # ignore
  }
  return ""
}

function Try-ParseJson([string]$raw) {
  if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
  try { return $raw | ConvertFrom-Json -Depth 100 } catch { return $null }
}

function Get-StringFieldDeep($obj, [string[]]$candidates) {
  if ($null -eq $obj) { return "" }

  if ($obj -is [string]) {
    return ""
  }

  if ($obj -is [System.Collections.IDictionary]) {
    foreach ($key in $candidates) {
      if ($obj.Contains($key) -and -not [string]::IsNullOrWhiteSpace([string]$obj[$key])) {
        return [string]$obj[$key]
      }
    }
    foreach ($value in $obj.Values) {
      $found = Get-StringFieldDeep $value $candidates
      if (-not [string]::IsNullOrWhiteSpace($found)) { return $found }
    }
    return ""
  }

  $props = $obj.PSObject.Properties
  if ($null -eq $props) { return "" }

  foreach ($key in $candidates) {
    $prop = $props | Where-Object { $_.Name -eq $key } | Select-Object -First 1
    if ($null -ne $prop -and -not [string]::IsNullOrWhiteSpace([string]$prop.Value)) {
      return [string]$prop.Value
    }
  }

  foreach ($prop in $props) {
    $found = Get-StringFieldDeep $prop.Value $candidates
    if (-not [string]::IsNullOrWhiteSpace($found)) { return $found }
  }
  return ""
}

function Get-RuleState {
  $repoRoot = Get-RepoRoot
  $statePath = Join-Path $repoRoot ".codex/hooks/rule_state.json"
  $defaultState = [pscustomobject]@{
    implement_mode = $false
    clarifications_complete = $false
  }

  if (-not (Test-Path -LiteralPath $statePath)) {
    $defaultJson = $defaultState | ConvertTo-Json
    [System.IO.File]::WriteAllText($statePath, $defaultJson)
    return $defaultState
  }

  $raw = Read-TextSafe $statePath
  $parsed = Try-ParseJson $raw
  if ($null -eq $parsed) { return $defaultState }

  $implementMode = $false
  $clarified = $false
  try { $implementMode = [bool]$parsed.implement_mode } catch { $implementMode = $false }
  try { $clarified = [bool]$parsed.clarifications_complete } catch { $clarified = $false }

  return [pscustomobject]@{
    implement_mode = $implementMode
    clarifications_complete = $clarified
  }
}

function Is-MutatingShellCommand([string]$command) {
  $text = ($command ?? "").Trim().ToLowerInvariant()
  if ([string]::IsNullOrWhiteSpace($text)) { return $false }

  $patterns = @(
    '\bapply_patch\b',
    '\bgit\s+(add|commit|push|pull|merge|rebase|cherry-pick|tag)\b',
    '\bnpm\s+(install|update|uninstall|run\s+build)\b',
    '\bpnpm\s+(install|update|add|remove|run\s+build)\b',
    '\byarn\s+(add|remove|install|upgrade|build)\b',
    '\bpip\s+install\b',
    '\bpoetry\s+add\b',
    '\buv\s+add\b',
    '\bdocker\s+(build|compose\s+up)\b',
    '\bmkdir\b',
    '\bnew-item\b',
    '\bmove-item\b',
    '\bcopy-item\b',
    '\bremove-item\b',
    '\bset-content\b',
    '\badd-content\b',
    '\bout-file\b',
    '>>',
    '>',
    '\bdel\b',
    '\berase\b',
    '\brm\b',
    '\bmv\b',
    '\bcp\b'
  )

  foreach ($pattern in $patterns) {
    if ($text -match $pattern) { return $true }
  }
  return $false
}

function Is-MutatingTool([string]$toolName, [string]$shellCommand) {
  $name = ($toolName ?? "").Trim().ToLowerInvariant()
  if ($name -eq "functions.apply_patch") { return $true }
  if ($name -eq "functions.shell_command") {
    return (Is-MutatingShellCommand $shellCommand)
  }
  return $false
}

$payloadRaw = Get-HookPayloadText
$payload = Try-ParseJson $payloadRaw

$toolName = Get-StringFieldDeep $payload @("tool_name", "recipient_name", "tool")
$shellCommand = Get-StringFieldDeep $payload @("command", "script")

if (-not (Is-MutatingTool $toolName $shellCommand)) {
  exit 0
}

$state = Get-RuleState
if ($state.implement_mode -and $state.clarifications_complete) {
  exit 0
}

Write-Error @"
Blocked by local enforcement rules:
1) Always implement when user says implement.
2) Do not touch code until clarifications from both sides are complete.
3) Do not break rules 1 and 2.

Current rule_state:
- implement_mode: $($state.implement_mode)
- clarifications_complete: $($state.clarifications_complete)

To allow mutations, set both to true in .codex/hooks/rule_state.json.
"@
exit 1
