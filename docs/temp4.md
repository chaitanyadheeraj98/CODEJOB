## 1. High-level overview

The Nvoids parser should **not treat the full page as the job listing**.

It should treat the detail page as a known structure:

```txt id="4a71yu"
Main Nvoids table
├── Row 1: listing subject / title
├── Row 2: recruiter email
├── Row 3: actual job body / JD
├── Row 4: repeated email + View All
└── Row 5: posted timestamp
```

Everything outside this table should be ignored.

The correction is important: for the current Nvoids pages you showed, the recruiter email is usually plain text or a normal `mailto:` link. So the parser should **first extract the simple email from Row 2**, not assume Cloudflare decoding. Cloudflare decoding should only be a fallback for rare pages that actually use `data-cfemail`.

The `Job Details.pdf` confirms this page shape: `Home`, then the title, `Email: nupur.kumari@tanishasystems.com`, job body, repeated email/View All/timestamp, then footer noise like `job_kill`, admin timeout text, `Time Taken`, and footer `Location`.

---

## 2. Problem being fixed

Current Nvoids sync has two separate issues.

First, the parser can still miss recruiter emails or accidentally use noisy page text. Your diagnostic doc shows many imported Nvoids rows never reached `Needs Review` because `recruiter_email` was empty, and `_enqueue_needs_review_candidate(...)` returns early when there is no recruiter email.

Second, imported Nvoids rows can silently disappear from the UI because they are stored as `ExternalOpportunity`, but not always converted into visible `RecruiterEmail` candidates. The doc shows `created` means external rows created, not guaranteed `Needs Review` drafts.

So the fix has two parts:

```txt id="frnuwr"
1. Parse the Nvoids table correctly.
2. Record/drop reasons clearly when a row cannot be queued.
```

---

## 3. Backend files to change

Primary files:

```txt id="qo00w7"
backend/app/external_feeds/parser.py
backend/app/external_feeds/service.py
backend/app/external_feeds/types.py
backend/tests/test_external_feeds_parser.py
backend/tests/test_external_feeds_api.py
```

Optional, only if queue outcome tracking needs schema support:

```txt id="6efw1f"
backend/app/external_feeds/models.py
backend/app/models.py
backend/app/schemas.py
```

Do not change Gmail parsing, Gmail sync, Gmail queueing, or the general ATS scoring pipeline unless absolutely necessary.

---

## 4. Current code flow to preserve

Current flow should stay mostly the same:

```txt id="6fqju7"
Nvoids search page
↓
parse_listing_rows()
↓
fetch_detail_page()
↓
parse_external_post()
↓
store ExternalOpportunity
↓
_enqueue_needs_review_candidate()
↓
prepare_candidate_for_queue()
↓
score / draft / needs_review
```

The current branch already added `parsed_overrides`, which lets Nvoids preserve clean values such as role/location after generic `parse_email()` runs.

Queue prep currently does:

```txt id="adp93h"
parse_email()
↓
apply parsed_overrides
↓
hard filter
↓
semantic score
↓
routing
↓
draft
```

That override point is useful and should remain.

---

## 5. New parser design

### Add a structured parser result

Add a Nvoids-specific parsed object, for example:

```python id="g7p6mb"
ParsedNvoidsDetail:
    listing_subject: str
    recruiter_email: str
    body: str
    repeated_email: str
    posted_text: str
    role: str
    location: str
    raw_table_text: str
    parse_confidence: float
```

This object should represent only the real 5-row table.

---

## 6. How to identify the main Nvoids table

The parser should use BeautifulSoup and look for the table that has this shape:

```txt id="zpvxj6"
- contains job_details.jsp URL or bit.ly/source URL
- contains an Email row
- contains a large body/JD row
- contains View All or repeated recruiter email
- contains timestamp like 04:49 AM 17-Jun-26
```

Preferred approach:

```txt id="gm5qmp"
1. Find all tables.
2. Score each table.
3. Choose the table with strongest Nvoids listing signals.
4. Extract its rows/cells.
5. Ignore everything outside that table.
```

Reject tables or text blocks that only contain:

```txt id="xv8i1s"
Home
job_kill
Pages not loading
Time Taken
admin email
footer Location
```

---

## 7. How each row should map

### Row 1: listing subject

Example:

```txt id="5ng8x2"
Looking for GCP AI Engineer in Irving, TX, or Charlotte NC - Onsite at Irving, Texas, USA
```

Use this as:

```txt id="u6a4xw"
listing_subject
fallback role/title context
source subject
```

Do not let footer text override it.

---

### Row 2: recruiter email

Example from your screenshot:

```html id="1q0azc"
Email:
<a href="mailto:tanuja@digitaldhara.com">tanuja@digitaldhara.com</a>
```

Extraction priority should be:

```txt id="vrdcut"
1. mailto: href from Row 2
2. visible email text from Row 2
3. regex email from Row 2 text
4. repeated email from Row 4
5. Reply to email inside Row 3 body
6. Cloudflare data-cfemail fallback only if present
```

Important correction:

```txt id="ed2o4u"
Do not make Cloudflare decoding the main path.
For normal Nvoids pages, just read the mailto link or visible email.
```

---

### Row 3: listing body / JD

This is the main text used for scoring and draft generation.

Extract:

```txt id="g4ar4a"
job title
role line
location
duration
required skills
mandatory skills
job description
experience
remote/hybrid/onsite
interview mode
visa/work authorization
rate
recruiter signature if useful
```

Ignore inside Row 3 only if repeated noise appears:

```txt id="m5tpv2"
duplicate source URLs
tracking links
unsubscribe/remove notes
email protection artifacts
```

But keep real JD lines.

---

### Row 4: repeated email + View All

Use only for confirmation.

```txt id="y40xvz"
repeated_email = recruiter email backup
View All = ignore for job body
Posts from recruiter page = ignore for single listing extraction
```

Do not put `View All` in scoring text or draft text.

---

### Row 5: timestamp

Example:

```txt id="1mxl04"
04:49 AM 17-Jun-26
```

Map to:

```txt id="l7d8xa"
posted_text
posted_at
```

Use existing timestamp parsing or extend it if needed.

---

## 8. Role/title extraction rule

Recommended priority:

```txt id="xvd4m2"
1. Explicit "Job Title:" from Row 3
2. Explicit "Role:" from Row 3
3. Cleaned Row 1 listing subject
4. Search-result row.title fallback
```

For the screenshot example:

```txt id="k2edlt"
Role- GCP AI Engineer
```

should produce:

```txt id="yj0x7i"
GCP AI Engineer
```

For the PDF example:

```txt id="71d28b"
Job Title:
Application Architect - AWS Cloud Migration
```

should produce:

```txt id="6qhk5q"
Application Architect - AWS Cloud Migration
```

The full Row 1 subject should still be preserved in the body/context, but the draft role should be clean.

---

## 9. Location extraction rule

Use location only from the real listing table.

Priority:

```txt id="j39qrm"
1. Row 3 "Location:" line
2. Row 1 listing subject
3. search-page row.location fallback
```

Never use footer location.

The PDF has a footer `Location: Dallas, Texas` after `Time Taken`, but that is page/footer metadata, not the job location.

---

## 10. Clean body construction

Create a clean body like this:

```txt id="6wckbo"
{listing_subject}

Recruiter Email: {recruiter_email}

{row_3_body}

Posted: {posted_text}
Source: {source_url}
```

But for ATS/scoring, avoid putting metadata too heavily into the semantic body. Better scoring body:

```txt id="xzp8pi"
Title: {role}
Location: {location}

{clean_row_3_body}
```

Remove:

```txt id="1h4huh"
Home
View All
job_kill instructions
Pages not loading / server timeout
Time Taken
footer Location
admin contact email
duplicate Nvoids URLs
browser/PDF page headers
```

---

## 11. Service integration plan

In:

```txt id="sxk1cf"
backend/app/external_feeds/service.py
```

Current conceptual flow should become:

```python id="stoaaf"
detail = parse_nvoids_detail_table(
    detail_html=detail_html,
    fallback_title=row.title,
    fallback_location=row.location,
    source_url=detail_url,
)

parsed = parse_external_post(
    source_type="nvoids",
    source_url=detail_url,
    title=detail.role,
    location=detail.location,
    posted_text=detail.posted_text,
    raw_body=detail.clean_body,
    raw_html=detail_html,
)
```

Then store:

```txt id="74jmal"
ExternalOpportunity.role = detail.role
ExternalOpportunity.location = detail.location
ExternalOpportunity.recruiter_email = detail.recruiter_email
ExternalOpportunity.raw_body = detail.clean_body
ExternalOpportunity.raw_html = original detail_html
ExternalOpportunity.source_url = detail_url
ExternalOpportunity.posted_at = parsed timestamp
ExternalOpportunity.parse_confidence = detail.parse_confidence
```

Keep `raw_html` for debugging, but do not use full HTML/page noise for scoring/drafting.

---

## 12. Queue/scoring integration plan

When calling queue preparation for Nvoids, pass the clean body:

```txt id="559o8h"
subject = item.role or item.subject
body = item.raw_body
external_thread_id = item.source_url
```

Use parsed overrides:

```python id="bb0hct"
parsed_overrides = {
    "role": item.role,
    "location": item.location,
}
```

This keeps the current queue/scoring flow intact while protecting Nvoids values.

The ATS/scoring pipeline should receive:

```txt id="7mfpzv"
clean role
clean location
clean job body
clean skills text
source URL metadata
```

Not:

```txt id="bvl76j"
Home
View All
job_kill
Time Taken
footer Location
admin timeout messages
```

---

## 13. Draft generation integration plan

Fallback and AI drafts should use:

```txt id="52poas"
{{role}} = clean role
{{location}} = clean location
body/JD context = clean Row 3 body
Nvoids Listing = source URL
recruiter email = Row 2 email
```

The generated draft must not include:

```txt id="o5l2ml"
View All
job_kill
Pages not loading
Time Taken
footer Location
admin email
HTML tags
```

---

## 14. Queue visibility improvement

This should be handled after the parser cleanup, but it is important.

Current issue from the doc:

```txt id="p8al1u"
ExternalOpportunity rows can be created,
but if enqueue fails, they may not appear in Needs Review or Failed Mapping.
```

Plan:

```txt id="envchm"
1. Keep created_count as imported_count.
2. Add/log queued_count.
3. Add/log missing_email_count.
4. Add/log duplicate_candidate_count.
5. Add/log no_cc_count.
6. Add/log not_qualified_count.
7. Add/log detail_timeout_count.
```

If schema changes are allowed later, add:

```txt id="vxhwnw"
ExternalOpportunity.queue_status
ExternalOpportunity.queue_reason
ExternalOpportunity.linked_email_id
```

Mapping:

```txt id="6knjf7"
missing recruiter email → queue_failed / no_recruiter_email
duplicate candidate → queue_skipped / duplicate_candidate
not qualified → queue_skipped / not_qualified
needs_review → queue_queued / linked RecruiterEmail id
```

This prevents the dashboard from looking like sync “did nothing.”

---

## 15. Timeout handling plan

Nvoids detail pages can timeout because the collector uses a 20-second timeout for detail fetches.

Plan:

```txt id="n8bvs4"
1. Keep detail-page failures soft.
2. Count them as detail_timeout_count or failed_detail_count.
3. Do not abort the whole sync for one bad detail page.
4. Consider retry once with backoff for detail pages.
5. Keep search-page timeout as route failure unless fallback behavior is added.
```

---

## 16. Test plan

### Parser tests

File:

```txt id="gz9fgo"
backend/tests/test_external_feeds_parser.py
```

Add tests:

```txt id="t5eo8r"
1. Finds the main Nvoids table.
2. Extracts Row 1 subject.
3. Extracts Row 2 recruiter email from mailto.
4. Extracts Row 2 recruiter email from visible text.
5. Extracts Row 3 body.
6. Extracts Row 4 repeated email but ignores View All.
7. Extracts Row 5 timestamp.
8. Extracts role from "Role-" line.
9. Extracts role from "Job Title:" line.
10. Extracts location from Row 3 "Location:".
11. Does not use footer Location.
12. Removes job_kill footer.
13. Removes Pages not loading/admin footer.
14. Removes Time Taken.
15. Uses Cloudflare data-cfemail only as fallback when no normal email exists.
16. Rejects fake listing rows like "A collection of search strings".
```

---

### Service tests

File:

```txt id="7sz4ox"
backend/tests/test_external_feeds_api.py
```

Add tests:

```txt id="bvy1b4"
1. sync_nvoids stores recruiter_email from Row 2.
2. sync_nvoids stores clean role from Row 3 Job Title/Role.
3. sync_nvoids stores clean location from Row 3.
4. raw_body contains only cleaned listing text.
5. raw_html preserves full page HTML.
6. posted_at comes from Row 5.
7. source_url is preserved.
8. parse_confidence is high when all 5 rows are found.
```

---

### Draft regression tests

Add tests:

```txt id="0lv2se"
1. Needs Review draft subject uses clean role.
2. Draft body uses clean role/location.
3. Draft includes Nvoids Listing source URL.
4. Draft does not include View All.
5. Draft does not include job_kill.
6. Draft does not include Time Taken.
7. Draft does not include footer Location.
8. Draft does not include admin timeout text.
9. Missing recruiter email is logged or tracked with queue reason.
```

---

## 17. Regression cases from reference files

Use `Job Details.pdf` as the canonical 5-row table case:

```txt id="q1hj2n"
Expected recruiter_email:
nupur.kumari@tanishasystems.com

Expected role:
Application Architect - AWS Cloud Migration

Expected location:
Dallas TX (ONSITE)

Expected posted_text:
04:49 AM 17-Jun-26

Expected body contains:
AWS Outposts
Java 8 to Java 17/21 upgrades
Spring Boot modernization
CI/CD integration

Expected body does not contain:
job_kill
Pages not loading
Time Taken
footer Location
```

The diagnostic file `temp 3.md` should be used to cover the previous failure modes: missing recruiter email, silent queue drops, dirty/noisy body causing poor qualification, and misleading `created` count.

---

## 18. Acceptance criteria

Implementation is successful when:

```txt id="mydkt2"
1. Parser extracts the real 5-row Nvoids table.
2. Recruiter email is extracted from Row 2 mailto/plain text.
3. Cloudflare decoding is only fallback, not primary.
4. Role comes from Row 3 Role/Job Title or Row 1 fallback.
5. Location comes from Row 3 or Row 1, not footer.
6. Body comes from Row 3 only.
7. Timestamp comes from Row 5.
8. Header/footer/system noise is excluded.
9. ExternalOpportunity stores clean role/email/location/body.
10. Scoring receives clean listing text.
11. Draft generation receives clean listing text.
12. Nvoids Listing source URL is preserved.
13. Failed/skipped queue outcomes are logged or persisted with reasons.
14. Gmail sync behavior is unchanged.
```

---

## 19. Final implementation order

Recommended order for Codex:

```txt id="1aclom"
1. Add ParsedNvoidsDetail type.
2. Add main table finder.
3. Add 5-row extractor.
4. Add Row 2 email extractor using mailto/plain text first.
5. Add optional Cloudflare fallback only after normal extraction fails.
6. Add Row 3 role/location/body extraction.
7. Add Row 5 timestamp extraction.
8. Add footer/header noise remover.
9. Wire parsed detail into parse_external_post/service.py.
10. Keep parsed_overrides for Nvoids role/location.
11. Add queue outcome counters/logging.
12. Add parser tests.
13. Add sync/service tests.
14. Add draft regression tests.
```

Bottom line:

**The main fix is not “decode email.” The main fix is “understand the Nvoids 5-row table.” For normal Nvoids pages, Row 2 already has the recruiter email as plain text or `mailto:`. Extract that first, use Row 3 as the only JD body, ignore footer noise, and pass the cleaned result into the existing scoring and draft pipeline.**
