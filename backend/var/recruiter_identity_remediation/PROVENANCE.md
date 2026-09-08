# recruiter identity remediation — production run, 2026-09-08

These files are the only record of a destructive production write, so the applied
run and this note are **committed** rather than left on one machine. Dry runs are
ignored (`backend/.gitignore`) - they are reproducible by re-running the script,
and they accumulate on every invocation.

## The run

| | |
|---|---|
| Applied at | `2026-09-08T04:21:28Z` |
| Repository HEAD | `433fabb` (`feat/nvoids-end-client`) |
| Database | `codejob-postgres`, `codejob` |
| Command | `python -m scripts.resnapshot_recruiter_identity --apply` |
| Executed in | the running `codejob-backend` container |

The `git_commit` field inside the JSON reads `unknown`: `git` is not installed in
the backend image and the run was made without the `GIT_COMMIT` environment
variable that `_git_commit()` falls back to. The commit is recorded here instead.
The script was copied into the container with `docker compose cp` and both copies
were confirmed identical by MD5 (`b295e0e1e1cd541af0333dece9c2cb1c`) before the
run, so the committed code and the code that ran are the same file.

## What it changed and why

Auto-logging a send snapshotted `parseaddr(email.sender)` - the person who
*forwarded* the requirement rather than the recruiter the resume was sent to.
`resolved_recruiter_email` had held the right answer on every row since the run
that produced it; nothing read it. On a forwarded requirement the two are
different people, which they were on 1,770 of the 3,386 tracked sends.

The script recomputes each auto-logged row through
`recruiter_identity_service.recruiter_identity_for` and rewrites only what
differs. Hand-logged rows are skipped: the user's own typing is not the script's
to overrule. A row naming a third party - neither the sender nor the resolved
recruiter - is reported and left alone.

Contacts are scanned first, and the set losing a company is threaded into the
application pass. Without that ordering, pass 1 would have re-stamped onto the
card the very company pass 2 was about to remove from the contact; email 8027 is
the case that exposed it.

## Result

```
sends scanned      : 3445        contacts scanned : 423
sends changed      : 2018        (1,973 applications + 45 appts_applications)
contacts changed   :  157
flagged for review :    0
```

A follow-up `--dry-run` reported 0 sends and 0 contacts, confirming idempotence
on real data. Verified afterwards in Postgres: application 6646 (source email
8027) reads `lalitha.y@metasisinfo.com` where it read `Alekya`, and contact 1022
is no longer filed under `RPATECHNOLOGY INC` - a firm on a different domain that
the mail never attributed to her.

The 157 blanked companies move those contacts into the flagged set in the
premium-numbers inventory, which counts a missing or `Unknown` company as needing
review. That is the designed route for "we do not know", and it was stated before
the run rather than discovered after it.

## The files

| File | What it is |
|---|---|
| `20260908T042128Z-recruiter-identity-apply.json` | **the applied run.** Every row with its before and after value, keyed by `row_id` |
| `20260908T042146Z-recruiter-identity-dryrun.json` | post-apply verification, 0 rows (ignored, listed for completeness) |
| `pre-apply-20260908T042109Z.sql` | data-only dump of `applications`, `appts_applications`, `premium_number_contacts` taken immediately before the write (ignored - rollback material, not a record) |

## Recovering an overwritten value

```bash
python - <<'PY'
import json
d = json.load(open("20260908T042128Z-recruiter-identity-apply.json", encoding="utf-8"))
for c in d["applications"]:
    before, after = c["fields"].get("recruiter_name_snapshot", [None, None])
    print(c["table"], c["row_id"], repr(before), "->", repr(after))
PY
```
