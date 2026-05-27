# Graphify-First Codex Context

This repository uses a two-tier Graphify context strategy for Codex:

- Full report: `graphify-out/GRAPH_REPORT.md`
- Compact default report: `graphify-out/GRAPH_REPORT_COMPACT.md`

## Why

The compact report is the default routing context so Codex can target relevant files with fewer exploratory reads.  
For ambiguous or cross-cutting tasks, Codex escalates to the full report.

## Repo-Local Codex Setup

- Hook config: `.codex/hooks.json`
- Hook script: `.codex/hooks/pre_tool_use_graphify.ps1`
- Optional config pointer: `.codex/config.toml`

The `PreToolUse` hook prints a reminder to:
1. read `GRAPH_REPORT_COMPACT.md` first
2. use `GRAPH_REPORT.md` only when needed

## Refresh Commands

Generate compact report from existing full report:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-graphify-compact.ps1
```

Run Graphify + compact build + freshness check:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/refresh-graph-context.ps1
```

Watch mode (auto-refresh on file changes):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/watch-graph-context.ps1
```

Watch mode with a custom Graphify command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/watch-graph-context.ps1 -GraphifyCommand "graphify --root ."
```

Watch mode if you already run a separate Graphify watcher:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/watch-graph-context.ps1 -SkipGraphify
```

If Graphify was already run and you only want compact rebuild + stale warning:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/refresh-graph-context.ps1 -SkipGraphify
```

If you want stale graphs to fail CI/local checks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/refresh-graph-context.ps1 -SkipGraphify -FailOnStale
```

## Troubleshooting

- If compact rebuild reports access denied or sharing violation, close any open preview/editor tabs for `graphify-out/GRAPH_REPORT.md` and `graphify-out/GRAPH_REPORT_COMPACT.md`.
- Stop concurrent Graphify watcher sessions before rerunning refresh commands.
- Rerun:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/refresh-graph-context.ps1 -SkipGraphify
```

## Behavioral Contract

- `GRAPH_REPORT.md` is the full graph truth source.
- `GRAPH_REPORT_COMPACT.md` is the default Codex context file.
- For planning/search tasks, prefer the compact report before broad recursive exploration.
- For backend+dashboard or otherwise cross-module changes, escalate to the full report.
- If graph files are stale relative to recent commits, refresh before major refactors.

## Verification Scenarios

1. Ask “where is X implemented?”
   - Expected: compact report is consulted first, then targeted file reads.
2. Ask for a cross-cutting change (backend + dashboard).
   - Expected: fallback to full report.
3. Remove or rename graph reports.
   - Expected: hook warns and Codex falls back to normal exploration.
4. Run stale check with `-SkipGraphify` after new commits.
   - Expected: stale warning and suggested refresh command.
