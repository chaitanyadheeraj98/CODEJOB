# Temp Document

## Conversion 1

The Nvoids sync itself is working. The backend log shows `POST /external-feeds/nvoids/sync?batch_limit=10` returning `200 OK` twice on June 15, 2026, and the dashboard is refreshing `GET /candidates?...state=needs_review...` right after that with no crash.

The real problem is downstream of sync:

1. `sync_nvoids()` counts created `ExternalOpportunity` rows, not queued `RecruiterEmail` candidates.
   In [backend/app/external_feeds/service.py](/D:/My%20Websites/CodeJob/backend/app/external_feeds/service.py), `created_count += 1` happens after the external row is stored, even if `_enqueue_needs_review_candidate(...)` later returns `False`.

2. `_enqueue_needs_review_candidate(...)` silently drops Nvoids items before they ever reach `Needs Review` when any of these fail:
   - no recruiter email
   - no employer-pool CC email
   - duplicate external message id
   - qualification/preparation result is not `needs_review`
   That check is here in [backend/app/external_feeds/service.py](/D:/My%20Websites/CodeJob/backend/app/external_feeds/service.py): if `preparation.outcome != "needs_review"`, it just `return False`.

3. Those dropped Nvoids items are not being persisted into `failed` or `failed_mapping`, so they effectively vanish from the queue UI.
   That matches your screenshot perfectly:
   - Recent Runs says `fetched=203 created=10 deduped=58 skipped_location=135 failed=9`
   - `Needs Review = 0`
   - `Failed Mapping = 0`

4. Most of the feed is being filtered very early anyway.
   The Nvoids sync query is hardcoded to Java/Spring matching and location-filtered:
   - query builder: `(locations) and java and spring* not(*js)`
   - plus `row_matches_locations(...)`
   So `skipped_location=135` is expected from the current logic, not a crash.

One important correction: the selected date is probably not the blocker here. In [backend/app/main.py](/D:/My%20Websites/CodeJob/backend/app/main.py), non-Gmail sources are filtered by `created_at`, not `gmail_received_at`, so June 15 synced Nvoids rows should still be visible if they were actually turned into `RecruiterEmail` candidates.

So the brief diagnosis is:

- Nvoids sync is succeeding.
- The imported opportunities are being created.
- They are then getting rejected or skipped during `_enqueue_needs_review_candidate(...)`.
- Because the Nvoids path does not persist those skipped outcomes into a visible candidate bucket, the dashboard makes it look like “sync is not working” even though the fetch/import part is fine.

The most likely immediate blockers are:

- missing recruiter email on many detail pages
- missing employer-pool CC email
- qualification rejection in `prepare_candidate_for_queue(...)`

If you want, I can next do a second read-only pass to narrow which of those three is the dominant blocker in the running container path.

## Conversion 2

I did the second read-only pass against the live container, and the dominant blocker is now pretty clear.

What the live backend is actually doing on June 15, 2026:

- `88` Nvoids opportunities were imported today into `external_opportunities`
- `39` of those had a non-empty `recruiter_email`
- `49` had no recruiter email at all
- `16` became `RecruiterEmail` rows
- `13` of those are currently in `needs_review`
- `3` were created but ended up `rejected`

That means the biggest single blocker is:

- `missing recruiter email` on the Nvoids detail page

Why I’m confident:

- `_enqueue_needs_review_candidate(...)` immediately returns `False` if `extract_email_address(item.recruiter_email or "")` is empty.
- In the live DB, `49 / 88` imported Nvoids rows today already fail right there.
- Sample recent imported rows like `Java Developer IRVINE CA Remote...`, `Hotlist Available IT Consultants...`, `Looking for GCP AI Engineer...` all have empty `recruiter_email`.

What is not the main blocker:

- `missing employer-pool CC` is not the issue right now.
  I checked the live employer pool:
  - there are `90` employer-number-backed sender rows available
  - for today’s Nvoids rows with recruiter email, `0` would fail because of “no alternate CC available”
- So the CC pool is healthy.

What is the secondary blocker:

- the remaining rows with recruiter email but no queued candidate are very likely being rejected in `prepare_candidate_for_queue(...)`
- Given current live settings:
  - `accepted_locations=''`
  - `must_have_skills=''`
  - `qualification_threshold=0.6`
  - `feature_ai_enabled=0`
  - `feature_semantic_enabled=1`
  - `nvoids_locations='texas,remote'`
- and given the Nvoids path hardcodes routing as safe,
- the only realistic downstream blocker left is qualification/scoring outcome not reaching `needs_review`

So the brief diagnosis is:

1. Nvoids sync is working.
2. The main loss happens because many detail pages do not yield a recruiter email.
3. Among rows that do have a recruiter email, the next loss is qualification rejection, not CC mapping.
4. Employer-pool CC is not the current bottleneck.

The exact code path behind that is in [backend/app/external_feeds/service.py](/D:/My%20Websites/CodeJob/backend/app/external_feeds/service.py):

- early drop on missing recruiter email
- early drop on duplicate
- early drop if queue preparation outcome is not `needs_review`

If you want, I can next do one more read-only pass focused only on the secondary bucket and tell you why those recruiter-email-present rows are failing qualification.

## Conversion 3

I did the secondary-bucket pass, and the picture is fairly clear even without forcing the full live scorer to finish.

What I confirmed about the `23` Nvoids rows that had a recruiter email but still never became queue candidates:

- They are not failing because of missing CC.
  I checked the live employer pool:
  - `90` employer-backed sender rows exist
  - `0` of today’s recruiter-email-present Nvoids rows would fail the “no alternate CC available” check

- They are not mostly being blocked by strict policy settings.
  Live settings are currently:
  - `accepted_locations = ''`
  - `must_have_skills = ''`
  - `qualification_threshold = 0.6`
  - `feature_ai_enabled = 0`
  - `feature_semantic_enabled = 1`
  - policy location strictness = `balanced`
  So there is no aggressive must-have or location hard-filter configured.

- Only a minority of the non-queued rows clearly look like F2F/local-only policy cases:
  - `face to face`: `3`
  - `inperson / in-person`: `6`
  - `local only`: `3`
  These likely overlap, so this is not the dominant secondary blocker.

What does stand out is dirty input quality:

- `9` of the non-queued email-present rows still contain obvious `job description` / HTML-heavy body noise.
- Sample unqueued rows have very weak or polluted roles like:
  - `Job Description`
  - `Urgent Hiring`
  - `Urgent Open Roles !! Share Relevant Profiles as per the Roles !!`
- Their stored `raw_body` still includes a lot of HTML boilerplate, not a clean JD text block.

That means the dominant secondary blocker is most likely:

- qualification rejection caused by weak/noisy semantic inputs, not routing

Why that conclusion fits the code:

- `_enqueue_needs_review_candidate(...)` only creates a `RecruiterEmail` if `prepare_candidate_for_queue(...)` returns `outcome == "needs_review"`
- with current settings, the main remaining reasons for a recruiter-email-present row to fail are:
  - `ai_score < threshold`
  - F2F policy block
- and the data says only a small subset look like F2F/in-person cases
- while many look like poor parse-quality cases with generic titles plus HTML-heavy bodies

So the current ranking of blockers is:

1. `Missing recruiter email` on Nvoids detail pages
2. `Low qualification score from dirty/weak parsed content`
3. `A smaller subset of F2F / in-person / local-only cases`
4. `Not CC pool related`

One note: I tried to run the exact live semantic scorer on representative rows inside the container, but it was too heavy and timed out even on single-row checks. So I can’t honestly claim the exact numeric reject reason per row from the live scorer. But the read-only evidence strongly points to noisy Nvoids body/title parsing as the main secondary failure mode.

## Conversion 4

Yes, something important did change in this branch, but not in the part you might first suspect.

**What stayed the same from `main`**

- The core Nvoids sync skeleton is still the same:
  - fetch search rows
  - filter by `nvoids_locations`
  - fetch detail page
  - parse external post
  - store `ExternalOpportunity`
  - try `_enqueue_needs_review_candidate(...)`
- The early drop gates in `_enqueue_needs_review_candidate(...)` are also the same as `main`:
  - no recruiter email -> drop
  - duplicate external message id -> drop
  - no employer-pool CC -> drop
  - `prepare_candidate_for_queue(...)` outcome not `needs_review` -> drop

So the queue-entry contract itself was not fundamentally loosened or broken in this branch.

**What changed in `semantic-embeddings`**

1. **Nvoids parser got much more opinionated**
   - In `main`, [backend/app/external_feeds/parser.py](/D:/My%20Websites/CodeJob/backend/app/external_feeds/parser.py) mostly did:
     - `body = title + location + raw_html/raw_body`
     - basic email/phone extraction
     - tiny keyword skill extraction
   - In this branch, that parser now:
     - extracts a canonical detail-page title
     - builds a cleaned Nvoids body
     - strips HTML/footer/cloudflare noise
     - normalizes skills through taxonomy
   - This is an intended improvement, not a regression by itself.

2. **Nvoids queueing now uses the newer multi-resume semantic matcher**
   - In `main`, Nvoids queue prep scored only against the single active resume:
     - `scoring_resume=active_resume`
     - `draft_resume=active_resume`
   - In this branch, it now:
     - loads all enabled resumes
     - runs `select_best_resume_match(...)`
     - picks `selected_resume`
     - pins `resume_asset_id` / `resume_file_name`
   - That is a major behavioral change in qualification flow.

3. **Role is now forcibly preserved from the Nvoids canonical title**
   - In `main`, queue prep trusted the generic parser result for role.
   - In this branch, `parsed_overrides={"role": item.role}` was added so Nvoids keeps its canonical title through queue prep.
   - That should help ugly-role cases, not hurt them.

4. **AI draft ownership changed**
   - In this branch, AI no longer owns greeting/signature/Nvoids header; backend composes those.
   - That only affects draft formatting after qualification.
   - It does **not** explain why a candidate never enters `needs_review`.

**What I think is actually happening**
The most important branch-level change for queue behavior is not “Nvoids sync broke.” It is:

- **semantic qualification got stricter/different because this branch now runs the newer resume-selection + semantic-scoring path**

So compared with `main`, this branch is much more likely to change which Nvoids items pass the threshold, even if both toggles are on.

Given your current live data, the branch difference probably looks like this:

- `main`:
  - simpler parser
  - single active resume
  - older semantic behavior
  - more forgiving / flatter qualification outcomes

- `semantic-embeddings`:
  - cleaner Nvoids title/body
  - taxonomy-normalized parsing
  - multi-resume matching
  - newer scoring inputs and newer embedding behavior
  - more ways for a post to miss `needs_review` if the parsed JD or selected resume does not score well enough

**Most important practical point**
If you remember Nvoids “working as intended” on `main` with:

- `Enable AI Features = ON`
- `Enable Semantic Matching = ON`

that does **not** mean the same toggles on this branch will behave the same, because the semantic path behind those toggles is no longer the same path.

So my brief conclusion is:

- No, the Nvoids feature was not left untouched in this branch.
- The sync scaffold is basically the same.
- The **meaningful change** is the newer semantic/multi-resume qualification path.
- The parser changes are mostly beneficial.
- The thing most likely causing branch-to-branch behavior drift is the changed scoring/matching pipeline, not the Nvoids fetch itself.

If you want, I can next do one very focused comparison of just:

- `main` Nvoids queue qualification inputs
vs
- `semantic-embeddings` Nvoids queue qualification inputs

and tell you exactly which scoring-side differences are most likely reducing pass-through to `needs_review`.

## Conversion 5

Yes. The focused comparison shows the biggest drift is in the **qualification inputs**, and one important clarification is:

- `Enable AI Features` does **not** control whether a Nvoids item reaches `needs_review`
- it only controls whether the draft body is AI-generated or rules-only **after** the item already passed qualification

For Nvoids queue pass-through, the toggle that really matters is:

- `Enable Semantic Matching`

So if you remember “both toggles on” working in `main`, the real queue-side difference is almost entirely the **semantic/scoring pipeline behind that semantic toggle**.

**What `main` was doing**

1. `parse_external_post(...)`
   - simpler Nvoids body/title parsing
   - basic skill extraction
2. `prepare_candidate_for_queue(...)`
   - generic `parse_email(...)`
3. `compute_blended_ai_score(...)`
   - keyword score from simple `ai_assist_score`
   - semantic score against **one active resume**
4. `semantic_text_for_resume(...)`
   - used full extracted resume context from the resume file

That made `main` relatively flatter and more forgiving.

What this branch is doing:

1. `parse_external_post(...)`
   - canonical Nvoids title extraction
   - cleaned body extraction
   - taxonomy-normalized skill text
2. `parse_email(...)`
   - now section-aware and taxonomy-driven
   - role-family filtering and noise suppression
3. `select_best_resume_match(...)`
   - scores **all enabled resumes**
   - picks the best one
4. `compute_blended_ai_score(...)`
   - no longer just simple keyword overlap
   - now uses **intent-weighted** scoring
   - includes:
     - specialization score
     - foundation score
     - role alignment score
5. `semantic_text_for_resume(...)`
   - now prefers manual `resume.skills_text`
   - only falls back to full resume extraction if manual skills are missing
6. embedding runtime
   - `main` could use hash / gemini / openrouter / sbert fallback chain
   - this branch forces runtime to `sbert -> hash`

**Why that matters for Nvoids**
These are the scoring-side differences most likely reducing pass-through in `semantic-embeddings`:

1. **Resume-side semantic input changed**
   - `main`: full resume context
   - current branch: mostly manual `skills_text`
   - if your stored resume skills are incomplete, noisy, or too narrow, the match gets weaker than it was in `main`

2. **Scoring is more selective now**
   - `main`: flatter keyword + semantic blend
   - current branch: intent-weighted matching with role-family logic
   - that can reject borderline Nvoids JDs that `main` would have let through

3. **Multi-resume selection adds another decision layer**
   - `main`: score against one active resume
   - current branch: choose best enabled resume first, then score with that
   - if the “best” selected resume is not actually the strongest practical fallback for a noisy Nvoids post, the final score can drop

4. **Embedding behavior changed**
   - current branch now uses local `sbert -> hash`
   - even with the same toggle settings, semantic similarity numbers will not match `main` if `main` was effectively using a different provider/model path

**Most important conclusion**
Yes, something important changed in this branch.

The Nvoids fetch/import flow is still basically the same, but the **qualification engine behind semantic matching is not the same engine anymore**.

So the likely reason `main` felt “working as intended” is:

- it was qualifying Nvoids posts with
  - simpler parser inputs
  - single active resume
  - broader full-resume semantic context
  - older flatter scoring

while `semantic-embeddings` is qualifying them with

- cleaner but more structured JD inputs
- manual resume skills
- best-resume selection
- stricter intent-weighted semantic scoring
- a different embedding runtime

And one more key point: if the issue is “Nvoids rows are not entering `needs_review`,” the strongest branch-specific suspect is **not** the AI toggle. It is the **new semantic resume-matching/scoring path**.

## Conversion 6

I checked the live backend behavior and the current code path. The issue is real, but it is not the exact one you suspected.

What is happening now:

- Nvoids sync is intermittently failing at the backend route level with real `502` responses. The live container logs show:
  - `POST /external-feeds/nvoids/sync?batch_limit=10 ... 502 Bad Gateway`
- That `502` is not an external gateway layer by itself. In [backend/app/main.py](/abs/path/not/available) the Nvoids route wraps any exception as:
  - `nvoids_sync_failed: {exc}`
- Your UI message `The read operation timed out` matches the current collector design in [backend/app/external_feeds/collector.py](/abs/path/not/available):
  - all Nvoids fetches use `httpx.Client(timeout=20.0)`
  - if the search page request times out, the whole sync aborts
  - only detail-page failures are handled softly; search-page failure is not

The role/title logic is not stealing recruiter emails:

- The first meaningful row after `Home` is only used for the canonical Nvoids title in [backend/app/external_feeds/parser.py](/abs/path/not/available) via `extract_nvoids_detail_title(...)`.
- Recruiter email is extracted separately from the detail HTML through `parse_job_detail_contacts(detail_html)`.
- So the backend is not trying to read `To/CC` only from that first title row.

Semantics are running:

- The log file you pointed to shows many lines like:
  - `Embedding latency provider=sbert ...`
- So semantic embeddings are definitely executing.
- Current live settings also show:
  - `feature_semantic_enabled = true`
  - `feature_ai_enabled = false`
- That means semantic matching is on, but AI drafting is off right now. So if you expected AI-generated drafts, that will not happen with the current settings.

Why items are “disappearing” instead of landing in Needs Review or Failed Mapping:

- In [backend/app/external_feeds/service.py](/abs/path/not/available), `_enqueue_needs_review_candidate(...)` silently returns `False` when:
  - no recruiter email
  - duplicate external message id
  - no employer-pool CC
  - `prepare_candidate_for_queue(...).outcome != "needs_review"`
- It does not create a `failed_mapping` candidate for those Nvoids rows.
- So for Nvoids, many rows can end up stored only as `ExternalOpportunity` records and never appear in either `Needs Review` or `Failed Mapping`.

What that means in plain English:

1. One problem is transport/runtime:
   - Nvoids sync sometimes dies on a 20-second read timeout before finishing.
2. Another problem is qualification/queueing behavior:
   - even when rows are fetched, Nvoids rows that do not fully qualify are often dropped quietly instead of being surfaced in `Failed Mapping`.
3. Another important detail is settings:
   - AI drafting is currently off, so only fallback/rules behavior can produce drafts right now.
4. Your first-row role extraction idea is not the current contact-extraction bug:
   - title extraction and recruiter-email extraction are separate in this branch.

Most likely root-cause summary:

- `502 / nvoids_sync_failed`:
  - caused by Nvoids fetch timeout, likely at search-page or non-soft-failed fetch level
- `no drafts in Needs Review`:
  - combination of `feature_ai_enabled=false` plus Nvoids rows not passing queue prep
- `not showing in Failed Mapping`:
  - current Nvoids enqueue path drops non-qualifying rows instead of persisting them as failed candidates

If you want, I can next do one more read-only pass focused only on the exact timeout path and tell you whether the dominant failure is:

1. search-page timeout,
2. detail-page timeout burst,
3. or qualification drop after successful fetch.

## Conversion 7

I added temporary Nvoids diagnostics in the backend without changing the queueing behavior.

What changed:

- `backend/app/external_feeds/collector.py`
  - logs search-page fetch start/success/failure
  - logs detail-page fetch start/success/failure
- `backend/app/external_feeds/service.py`
  - logs sync start and final summary
  - logs each parsed row’s role/email/confidence
  - logs why a row is skipped:
    - `no_recruiter_email`
    - `duplicate_candidate`
    - `no_cc_pool_match`
    - `queue_preparation_outcome`
  - logs successful enqueue with score/resume/draft source
- `backend/app/main.py`
  - logs the full sync exception context before returning `502 nvoids_sync_failed: ...`

Verification:

- I couldn’t use `uv` or `.pyc` output because of Windows permission/cache issues.
- I did run a no-write Python parse check on the edited files, and all three compile successfully.

What to do next:

- Rebuild/restart the backend container so the running image picks up these logs.
- Then run Nvoids sync again and check for these log markers:
  - `nvoids_fetch_search_failed`
  - `nvoids_fetch_detail_failed`
  - `nvoids_enqueue_skip`
  - `nvoids_enqueue_success`
  - `nvoids_sync_complete`
  - `nvoids_sync_endpoint_failed`

Once you have the next log output, I can pinpoint whether the dominant blocker is fetch timeout, recruiter-email extraction gaps, or queue-preparation rejection.

## Conversion 8

The fresh run shows the dominant problem much more clearly now.

What happened in this run:

- The sync endpoint itself did not fail this time.
- Backend returned `200 OK` for:
  - `POST /external-feeds/nvoids/sync?batch_limit=10`
- The run summary in the UI matches that:
  - `fetched=300`
  - `created=4`
  - `deduped=95`
  - `skipped_location=201`
  - `failed=12`

What the actual error is:

- The new logs show repeated `httpx.ReadTimeout: The read operation timed out`
- All the visible failures are from:
  - `backend/app/external_feeds/collector.py`
  - inside `fetch_detail_page(...)`
- Example failing URLs:
  - `job_details.jsp?id=3448990...`
  - `job_details.jsp?id=3448883...`
  - `job_details.jsp?id=3448814...`

So the main error in this run is:

- Nvoids detail pages are timing out after the current `20.0` second HTTP timeout.
- This is a detail-page timeout burst, not a search-page timeout.
- It is also not a backend “gateway” logic bug in this run, because the route completed successfully with `200 OK`.

What this means for the empty `Needs Review`:

- Most rows were eliminated before queueing:
  - `201` were filtered out by location
  - `95` were deduped
  - `12` had detail fetch failures
- Only `4` new external rows were created.
- Important: `created=4` does not mean `4` candidates reached `Needs Review`.
- It only means `4` `ExternalOpportunity` rows were stored.
- Those `4` still must pass recruiter-email extraction, CC selection, and queue preparation to become `Needs Review`.

What this run does not suggest:

- It does not suggest semantics are broken.
- It does not suggest the canonical title row is stealing recruiter emails.
- It does not show a search-page failure as the dominant blocker.

Most likely current diagnosis:

1. Primary blocker:
   - Nvoids detail-page instability / timeout
2. Secondary blocker:
   - very aggressive location filtering is removing most rows
3. Remaining hidden blocker for `Needs Review`:
   - the small number of created rows are probably failing later in enqueue/qualification, but this specific log slice mostly proves the timeout side first

One important interpretation:

- `failed=12` here is sync/detail-fetch failure count inside the Nvoids scrape run.
- It is not the dashboard `Failed Mapping` candidate bucket.
- That is why you can see failures in the run summary without seeing new rows in `Failed Mapping`.

If you want, I can do one more read-only pass now focused only on the `4 created` rows and tell you exactly why they did not become `Needs Review`:

- `no_recruiter_email`
- `duplicate_candidate`
- `no_cc_pool_match`
- or `queue_preparation_outcome` rejection

## Conversion 9

I checked the 4 newly created rows from the latest Nvoids run. They all failed for the same reason:

- `no_recruiter_email`

What I verified:

- Latest Nvoids scrape run:
  - `run_id=78`
  - `fetched=300`
  - `created=4`
  - `deduped=95`
  - `skipped_location=201`
  - `failed=12`
- The 4 created `ExternalOpportunity` rows are:
  - `nvoids:3449634`
  - `nvoids:3449498`
  - `nvoids:3449497`
  - `nvoids:3448480`
- All 4 have:
  - `recruiter_email=""`
  - `recruiter_phone=""`

That means they never reached:

- duplicate-candidate check
- CC pool check
- semantic scoring / queue qualification
- draft generation

Because in `ExternalFeedService._enqueue_needs_review_candidate(...)`, the very first gate is:

- if no recruiter email -> return `False`

So for these 4 rows, the drop reason is not:

- `duplicate_candidate`
- `no_cc_pool_match`
- `queue_preparation_outcome`

It is earlier than all of those.

Why this is happening:

- The stored bodies show recruiter-like content such as:
  - `From`
  - recruiter names
  - `Reply to`
- but the actual email address is not being extracted into `recruiter_email`
- so the contact parser is not successfully resolving the contact blocks from these Nvoids detail pages

One row is especially bad:

- `nvoids:3448480`
- role: `A collection of search strings`
- that is clearly not a real job title
- so this branch is also still ingesting some Nvoids page noise as if it were a valid listing

So the exact conclusion for this run is:

1. The 4 created rows did not become `Needs Review` because all 4 were missing `recruiter_email`.
2. Semantics did not reject these 4, because the enqueue path never got that far.
3. The dominant queue blocker for these created rows is contact extraction failure.
4. There is also secondary parser noise, because at least one created row is not a real listing.

If you want, I can next do one more read-only pass focused only on `parse_job_detail_contacts(...)` behavior against these Nvoids detail-page patterns and tell you exactly which contact pattern the current parser is missing.

## Conversion 10

I found the exact contact pattern the current parser is missing.

The main miss is Cloudflare-protected recruiter emails.

What the Nvoids detail pages actually contain:

- the email row is like:
  - `Email: <a ...><span class="__cf_email__" data-cfemail="...">[email protected]</span></a>`
- so the visible text is only:
  - `[email protected]`
- the real email address is hidden in:
  - `data-cfemail=...`

Why the current parser misses it:

- `parse_job_detail_contacts(...)` calls `_extract_first_email(lines)`
- `_extract_first_email(...)` only uses `_EMAIL_RE`
- `_EMAIL_RE` only matches real addresses like `name@domain.com`
- it does not decode Cloudflare’s `data-cfemail`
- so for these pages, recruiter email stays empty

There is a second pattern miss too:

- recruiter name extraction expects:
  - `From: some name`
- but these pages often become:
  - `From`
  - `Diwakar,`
  - `Abridge Info Systems Inc`
  - `Reply to`
- so `_FROM_LINE_RE = ^from:\s*(.+)$` does not match
- that is why recruiter name also stays empty

And a third one:

- phone extraction expects either:
  - a labeled phone line
  - or a phone close to `From:`
- but these pages are not structured that way after text extraction
- so recruiter phone is also often missed

There is also one separate parser-quality issue:

- row `nvoids:3448480` with role `A collection of search strings`
- that is not a real job
- it came from search-results noise being treated as a listing
- so `parse_listing_rows(...)` is also a bit too permissive for some Nvoids result pages

So the exact answer is:

1. The current parser is missing Cloudflare email-obfuscation decoding.
2. It also assumes `From:` is on one line, but Nvoids often splits it across lines.
3. It also assumes phone appears in a labeled/near-From block, which is often not true.
4. Separately, the listing-row parser is letting at least one non-job search artifact through.

That is why those 4 created rows never reached `Needs Review`: the recruiter contact extractor never produced a usable recruiter email.
