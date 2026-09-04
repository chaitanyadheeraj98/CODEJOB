# end_client remediation — production run, 2026-09-04

These files are the only record of a destructive production write, so the
applied run and this note are **committed** rather than left on one machine.
Dry-run files are ignored (`backend/.gitignore`) - they are reproducible by
re-running the script, and they accumulate on every invocation.

## The run

| | |
|---|---|
| Applied at | `2026-09-04T03:00:42Z` |
| Repository HEAD | `6b58c21` (`feature/assistant-v4`) |
| Database | `codejob-postgres`, `codejob` |
| Command | `python -m scripts.clean_end_client_backfill --apply` |
| Executed in | the running `codejob-backend` container, no restart |

The `git_commit` field inside the JSON files reads `unknown` because `git` is
not installed in the backend image and the run predates the `GIT_COMMIT`
environment-variable fallback added to `_git_commit()` in the same change. The
commit is recorded here instead. Later runs should pass `GIT_COMMIT` explicitly.

The script and `app/services/end_client_validation.py` were copied into the
running container with `docker cp` so the run could proceed without a restart —
`docker-compose` runs `alembic upgrade head` on backend boot, and a restart was
not warranted for a data cleanup. Those copies disappear at the next image
build, when the committed versions take over. **The committed code and the code
that ran are identical apart from the `GIT_COMMIT` fallback**, which affects
only what the audit file records about itself, not which rows are selected.

## Result

```
Populated end_client rows : 76
Cleared                   : 27   (25 nvoids, 2 gmail)
Preserved unchanged       : 49

By rule:  14 compound_tail_fragment · 6 html_markup · 2 email_address
          2 eligibility_constraint · 1 prose_marker · 1 trailing_stopword
          1 label_or_clause

Tracked applications: no invalid values — nothing written there.
```

Verified afterwards against the database: 49 populated, 0 matching any
invalidity rule. A follow-up `--dry-run` reported 0 to clear, confirming
idempotence on real data.

## The files

Only `*-apply.json` and this note are committed. The dry runs below exist on
the machine that ran them and are listed for completeness.

| File | What it is |
|---|---|
| `20260904T023856Z-dryrun.json` | first dry run, 28 rows — **superseded** |
| `20260904T023903Z-dryrun.json` | identical re-run of the above |
| `20260904T024022Z-dryrun.json` | dry run after the validator was corrected, 27 rows — **this is what was approved** |
| `20260904T030042Z-apply.json` | **the applied run.** 27 rows, each with `before`, `after`, `reason`, `length_before` |
| `20260904T030100Z-dryrun.json` | post-apply verification, 0 rows |

The first two dry runs flagged 28 rows because the validator then capped values
at 80 characters and 8 words. That would have destroyed a real client —
`State of OR (OHA/ODHS - Oregon Health Authority/ Oregon Department of Human
Services)`, 85 characters and 13 tokens. The caps were replaced with a
prose-marker rule before anything was applied, and that value is now a
regression test in `tests/test_end_client_validation.py`. It survives in
production.

## Recovering a cleared value

Every cleared string is in `changes[]` of the apply file, keyed by `row_id`.

```bash
python - <<'PY'
import json
d = json.load(open("20260904T030042Z-apply.json", encoding="utf-8"))
for c in d["changes"]:
    print(c["row_id"], repr(c["before"]), c["reason"])
PY
```

Restoring one would reinstate a value the validator rejects, and the writers
will not reproduce it. Nothing here is worth restoring: every entry is a
sentence fragment, markup, an unsubscribe address, or an eligibility constraint.
The file exists so that claim can be checked rather than taken on trust.
