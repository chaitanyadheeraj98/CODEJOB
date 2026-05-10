param(
  [string]$InputPath = "graphify-out/GRAPH_REPORT.md",
  [string]$OutputPath = "graphify-out/GRAPH_REPORT_COMPACT.md",
  [int]$MaxSectionLines = 25,
  [int]$MaxTotalLines = 220
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $InputPath)) {
  throw "Graphify full report not found at '$InputPath'. Run Graphify first."
}

$inputItem = Get-Item -LiteralPath $InputPath
$lines = Get-Content -LiteralPath $InputPath -Encoding utf8

if (-not $lines -or $lines.Count -eq 0) {
  throw "Graphify full report at '$InputPath' is empty."
}

$includeHeadingPatterns = @(
  "overview",
  "summary",
  "architecture",
  "module",
  "dependency",
  "entry",
  "api",
  "endpoint",
  "database",
  "schema",
  "model",
  "flow",
  "backend",
  "dashboard",
  "frontend",
  "script",
  "test",
  "risk",
  "todo"
)

$result = [System.Collections.Generic.List[string]]::new()
$result.Add("# GRAPH_REPORT_COMPACT")
$result.Add("")
$result.Add("Generated from: $InputPath")
$result.Add("Source last modified (UTC): $($inputItem.LastWriteTimeUtc.ToString("u"))")
$result.Add("Strategy: Use this compact report first. Escalate to GRAPH_REPORT.md only for ambiguous or cross-cutting tasks.")
$result.Add("")

$firstHeadingIndex = ($lines | Select-String -Pattern "^\s*#").LineNumber | Select-Object -First 1
if (-not $firstHeadingIndex) {
  $firstHeadingIndex = 1
}

$prefaceTake = [Math]::Min(40, $lines.Count)
$result.Add("## Preface Snapshot")
for ($i = 0; $i -lt $prefaceTake; $i++) {
  $result.Add($lines[$i])
}
$result.Add("")

$selectedCount = 0
$lineIndex = 0
while ($lineIndex -lt $lines.Count -and $result.Count -lt $MaxTotalLines) {
  $line = $lines[$lineIndex]
  if ($line -match "^\s*#{1,6}\s+(.+)$") {
    $headingText = $Matches[1].ToLowerInvariant()
    $matchesPattern = $false
    foreach ($pattern in $includeHeadingPatterns) {
      if ($headingText -like "*$pattern*") {
        $matchesPattern = $true
        break
      }
    }

    if ($matchesPattern) {
      $result.Add($line)
      $lineIndex++
      $sectionLineCount = 0

      while ($lineIndex -lt $lines.Count -and $lines[$lineIndex] -notmatch "^\s*#{1,6}\s+" -and $sectionLineCount -lt $MaxSectionLines -and $result.Count -lt $MaxTotalLines) {
        $result.Add($lines[$lineIndex])
        $lineIndex++
        $sectionLineCount++
      }

      $result.Add("")
      $selectedCount++
      continue
    }
  }
  $lineIndex++
}

if ($selectedCount -eq 0) {
  $result.Add("## Fallback Snapshot")
  $fallbackTake = [Math]::Min($MaxTotalLines - $result.Count, $lines.Count)
  for ($i = 0; $i -lt $fallbackTake; $i++) {
    $result.Add($lines[$i])
  }
  $result.Add("")
}

$result.Add("## Usage Rules")
$result.Add("- Start with this compact report for routing and file targeting.")
$result.Add("- Read graphify-out/GRAPH_REPORT.md only when task scope is unclear, cross-module, or requires deeper detail.")
$result.Add("- If this file is stale relative to recent commits, refresh graph context before major refactors.")

$outputDir = Split-Path -Parent $OutputPath
if ($outputDir -and -not (Test-Path -LiteralPath $outputDir)) {
  New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

$result -join "`n" | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output "Wrote compact graph context to $OutputPath"
