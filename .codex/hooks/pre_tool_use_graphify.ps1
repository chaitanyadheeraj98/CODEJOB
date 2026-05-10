param()

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$compactPath = Join-Path $repoRoot "graphify-out\GRAPH_REPORT_COMPACT.md"
$fullPath = Join-Path $repoRoot "graphify-out\GRAPH_REPORT.md"

if (Test-Path -LiteralPath $compactPath) {
  Write-Output "Graphify context: read graphify-out/GRAPH_REPORT_COMPACT.md first. Use graphify-out/GRAPH_REPORT.md only for ambiguous or cross-cutting tasks."
  exit 0
}

if (Test-Path -LiteralPath $fullPath) {
  Write-Output "Graphify context: compact report missing. Read graphify-out/GRAPH_REPORT.md before broad exploration."
  exit 0
}

Write-Output "Graphify context unavailable: graphify-out report files are missing. Use normal exploration and run scripts/refresh-graph-context.ps1."
