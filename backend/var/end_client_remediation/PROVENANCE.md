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

---

# vendor-as-end-client remediation — production run, 2026-09-08

A second, larger write into the same two columns, by a different script and for a
different reason. `clean_end_client_backfill` above blanks what
`end_client_validation` refuses - markup, sentences, prose. It cannot help here,
because `Horizon Softech Inc` is a perfectly well-formed company name that is
wrong for a reason no syntactic check can see: it names the wrong company.

## The run

| | |
|---|---|
| Applied at | `2026-09-08T12:03:52Z` |
| Repository HEAD | `433fabb` (`feat/nvoids-end-client`), recorded correctly in the JSON this time |
| Database | `codejob-postgres`, `codejob` |
| Command | `GIT_COMMIT=$(git rev-parse HEAD) python -m scripts.blank_vendor_as_end_client --apply` |
| Executed in | the running `codejob-backend` container, script copied in and MD5-matched (`486228ca13490bdf31019f0c1c457b40`) first |

## What it changed and why

`create_manual_application` required a non-blank end client, so
`create_application_from_recruiter_email` satisfied it the only way it could:

```python
manual_end_client=(email.end_client or '').strip() or recruiter_company,
```

Every send whose posting did not name a client therefore recorded the staffing
firm that sent the mail, or the literal `Unknown` when that was unknown too. The
column is a search filter and is interpolated into follow-up mail, so a follow-up
to the user's own employer read "your client Horizon Softech Inc".

The selection rule was changed immediately before this run. It used to require
`end_client_snapshot == recruiter_company_snapshot` - reading the substitution off
the row itself, which is only sound while nothing else rewrites that column. The
recruiter-identity remediation eight hours earlier set it to `Unknown` on 725
rows and took the signature with it. The rule now compares against the source
email's own `company`, the value the fallback actually copied, and hand-logged
rows keep the row-local pair as their trigger and are still only ever reported.
`tests/test_blank_vendor_as_end_client.py` covers both paths.

## Result

```
Populated end_client_snapshot rows : 3403
Cleared                            : 3357   (2,813 "Unknown" + 544 stated names)
Left for a human decision          :    0
```

Most frequent cleared values after `Unknown`: `Horizon Softech Inc` (44),
`Tanisha Systems` (9), `RPA TECHNOLOGY INC` (8), `Vdart Inc` (8), then a long
tail. A follow-up `--dry-run` reported 46 populated rows and 0 to clear,
confirming idempotence. Those 46 are the survivors: 43 of them are clients the
posting itself named, which is now the only kind of end client the database
asserts.

## The files

| File | What it is |
|---|---|
| `20260908T120352Z-vendor-as-end-client-apply.json` | **the applied run.** 3,357 rows, each with `before`, `after`, `reason` and the row's `recruiter_company` |
| `20260908T120130Z-...-dryrun.json`, `20260908T120400Z-...-dryrun.json` | the approved dry run and the post-apply verification (ignored) |
| `pre-apply-20260908T120326Z.sql` | data-only dump of `applications` taken immediately before the write (ignored - rollback material, not a record) |

Unlike the September 4 run, some of what was destroyed here *was* worth keeping
in the sense that it is a real company name. It was simply never this column's
company. Every string is recoverable from `changes[]` by `application_id`.
