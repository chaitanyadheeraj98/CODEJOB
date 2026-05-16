# Codex Rule Modes

This repo enforces local pre-tool rules via:

- `.codex/hooks/pre_tool_use_enforce_rules.ps1`
- `.codex/hooks/rule_state.json`

## Quick mode switch

From repo root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\.codex\hooks\set-rule-mode.ps1 -Mode plan
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\.codex\hooks\set-rule-mode.ps1 -Mode implement
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\.codex\hooks\set-rule-mode.ps1 -Mode status
```

## Meaning

- `plan`: blocks mutating tool calls.
- `implement`: allows mutating tool calls.
- `status`: prints current state without changing mode.
