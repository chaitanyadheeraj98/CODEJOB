import { useCallback, useEffect, useState } from 'react'
import { formatVariantLabel } from './resumeDisplay'
import type { FilterValues } from '../../components/FilterSortBar'
import { submissionDefaultFilterValues, submissionFiltersToParams } from './submissionFilters'
import type { FormEvent } from 'react'

import {
  acceptApplicationSuggestion,
  addApplicationInterview,
  dismissApplicationSuggestion,
  listApplications,
  listApplicationSuggestions,
  sendApplicationMessage,
} from '../premium_numbers/api'
import type { ApplicationCard, ApplicationSuggestion, ResumeAssetOption } from '../premium_numbers/types'
import {
  createManualApplication,
  fetchCandidateSentDetails,
  getApplicationDetail,
  getOutreachMessage,
  getWhyThisResume,
  lookupVariantToken,
  updateResumeSubmissionStatus,
} from './api'
import type { ApplicationOutreachMessage, ManualApplicationInput, ResumeSubmissionStatus, VariantLookupResult, WhyThisResume } from './types'
import { renderContactDetailsGrid } from '../../App'
import type { SentItemDetails } from '../../App'

const STATUS_OPTIONS = ['viewed', 'shortlisted', 'offered', 'hired', 'rejected', 'withdrawn'] as const
const REJECTION_CATEGORIES = ['missing_skill', 'missing_experience', 'missing_domain_knowledge', 'email_positioning', 'rate_mismatch', 'other']
const STATUS_TONE: Record<string, 'active' | 'pending' | 'flagged' | 'neutral'> = {
  not_submitted: 'neutral',
  submitted: 'neutral',
  viewed: 'pending',
  shortlisted: 'pending',
  interview_scheduled: 'pending',
  offered: 'active',
  hired: 'active',
  rejected: 'flagged',
  withdrawn: 'flagged',
}

type Props = {
  apiBase: string
  resumes: ResumeAssetOption[]
  resumeAssetId?: number | null
  filterValues?: FilterValues
  sortValue?: string
}

const blankManual = (resumes: ResumeAssetOption[], resumeAssetId?: number | null): ManualApplicationInput => ({
  resume_asset_id: resumeAssetId ?? resumes.find((resume) => resume.is_current)?.id ?? resumes[0]?.id ?? 0,
  dedupe_key: crypto.randomUUID(),
  manual_recruiter_name: '',
  manual_recruiter_company: '',
  manual_recruiter_email: '',
  manual_recruiter_phone: '',
  manual_recruiter_linkedin_url: '',
  manual_job_title: '',
  manual_end_client: '',
  manual_jd_text: '',
  manual_source_note: '',
  submission_method: 'email',
  resume_submitted_at: new Date().toISOString().slice(0, 10),
})

type WhyState = { loading: boolean; data: WhyThisResume | null; error: string }
type DetailsState = {
  loading: boolean
  detail: ApplicationCard | null
  sourcing: SentItemDetails | null
  // Kept apart from `error`: the record loaded fine, only its source trail did.
  sourcingError: string
  error: string
}

const pct = (value: number | null, scale = 100) => value == null ? null : `${Math.round(value * scale)}%`
const trimmed = (value: string | null | undefined) => (value ?? '').trim()
const shortDate = (value: string | null) => value ? new Date(value).toLocaleDateString() : ''
/*
 * "Unknown" is what the auto-log path writes when it has nothing to write, so it
 * is the absence of an answer wearing the shape of one. `recordedEndClient`
 * below already refuses to repeat it; every other labelled field should too.
 */
const stated = (value: string | null | undefined) => {
  const text = trimmed(value)
  return text.toLowerCase() === 'unknown' ? '' : text
}
const variantCode = (resumeAssetId: number) => `R${String(resumeAssetId).padStart(2, '0')}`

/*
 * What this row actually records as the end client, or '' for "not identified".
 *
 * For most rows the honest answer is nothing. Until this change, auto-logging a
 * send copied the recruiter's own company into the end-client column whenever
 * the posting did not name one, so a value equal to the company is not a client
 * at all - it is the absence of one, written down in a shape indistinguishable
 * from a real answer. Neither that nor the literal "Unknown" is a claim worth
 * repeating on a card.
 */
export function recordedEndClient(row: Pick<ApplicationCard, 'end_client_snapshot' | 'recruiter_company_snapshot'>): string {
  const value = trimmed(row.end_client_snapshot)
  if (!value || value.toLowerCase() === 'unknown') return ''
  if (value.toLowerCase() === trimmed(row.recruiter_company_snapshot).toLowerCase()) return ''
  return value
}

/*
 * One labelled fact, with its snapshot and its live value distinguished.
 *
 * The `_snapshot` columns are frozen at submission; the `current_*` fields are
 * read live off the contact record. The card used to print them in one run-on
 * line, which is how a company recorded months ago came to sit above today's
 * email address as though the two described the same moment. Where they
 * disagree, say so rather than silently picking one.
 */
function Field({ label, snapshot, current }: { label: string; snapshot: string; current?: string }) {
  const frozen = stated(snapshot)
  const live = stated(current)
  if (!frozen && !live) return null
  const moved = Boolean(frozen && live && frozen.toLowerCase() !== live.toLowerCase())
  return (
    <span className="submissionField">
      <span className="submissionFieldLabel">{label}</span>
      <span>{frozen || live}</span>
      {moved ? (
        <span className="submissionFieldNow" title="Recorded at submission. The contact record says something different now.">
          → now {live}
        </span>
      ) : null}
    </span>
  )
}

function SkillRow({ label, skills, tone }: { label: string; skills: string[]; tone: 'have' | 'missing' }) {
  if (!skills.length) return null
  return (
    <div className="whyRow">
      <h5>{label}</h5>
      <div className="skillChips">
        {skills.map((skill) => <span className={`trackingChip trackingChip--${tone}`} key={skill}>{skill}</span>)}
      </div>
    </div>
  )
}

function WhyPanel({ state }: { state: WhyState | undefined }) {
  if (!state || state.loading) return <div className="whyPanel"><p className="subtle">Loading the scoring breakdown...</p></div>
  if (state.error) return <div className="whyPanel"><p className="errorText" role="alert">{state.error}</p></div>

  const why = state.data
  if (!why) return null
  if (!why.available) return <div className="whyPanel"><p className="subtle">{why.reason_unavailable}</p></div>

  const failed = why.mandatory_gate_status === 'fail'
  return (
    <div className="whyPanel">
      <div className={`whyVerdict ${failed ? 'fail' : 'pass'}`}>
        <strong>{why.variant_code}</strong> was sent
        {why.variant_label ? <span className="subtle"> · {formatVariantLabel(why.variant_label)}</span> : null}
        <span className="whyVerdictText">
          {failed
            ? ' — it failed the must-have check and was picked as the closest available, not a match.'
            : ' — it cleared every must-have for this posting.'}
        </span>
      </div>

      <div className="whyScores">
        {why.mandatory_coverage != null ? <div><dt>Must-haves met</dt><dd>{pct(why.mandatory_coverage)}</dd></div> : null}
        {why.role_family_fit != null ? <div><dt>Role fit</dt><dd>{pct(why.role_family_fit)}</dd></div> : null}
        {why.ats_score != null ? <div><dt>ATS</dt><dd>{Math.round(why.ats_score)}</dd></div> : null}
        {why.final_resume_score != null ? <div><dt>Overall</dt><dd>{pct(why.final_resume_score)}</dd></div> : null}
      </div>

      <SkillRow label="Missing — required" skills={why.missing_required} tone="missing" />
      <SkillRow label="Missing — priority" skills={why.missing_priority} tone="missing" />
      <SkillRow label="Matched" skills={why.matched_priority.length ? why.matched_priority : why.matched_required} tone="have" />

      {why.alternatives.length > 1 ? (
        <div className="whyRow">
          <h5>What else was considered</h5>
          <ul className="whyAlternatives">
            {why.alternatives.map((alt) => (
              <li key={alt.resume_file_name} className={alt.is_selected ? 'selected' : ''}>
                <strong>{alt.variant_code || alt.resume_file_name}</strong>
                {alt.final_resume_score != null ? <span className="subtle"> · {pct(alt.final_resume_score)}</span> : null}
                {alt.is_selected ? <span className="whyChosen">chosen</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

// Why this event exists, when the row itself does not say. A backwards status
// move is only permitted as a correction, and that is recorded in the metadata
// rather than the type, so without this a fix reads as a real change of outcome.
function eventTrigger(metadataJson: string | undefined): string {
  try {
    const parsed = JSON.parse(metadataJson || '{}') as { trigger?: unknown }
    return typeof parsed.trigger === 'string' ? parsed.trigger.replaceAll('_', ' ') : ''
  } catch {
    return ''
  }
}

function DetailsPanel({ state }: { state: DetailsState | undefined }) {
  if (!state || state.loading) return <div className="whyPanel"><p className="subtle">Loading the record...</p></div>
  if (state.error) return <div className="whyPanel"><p className="errorText" role="alert">{state.error}</p></div>

  const detail = state.detail
  if (!detail) return null
  const endClient = recordedEndClient(detail)
  const resumeSent = [
    variantCode(detail.resume_asset_id),
    detail.resume_file_name_snapshot,
    detail.resume_version_snapshot ? `v${detail.resume_version_snapshot}` : '',
  ].filter(Boolean).join(' · ')

  return (
    <div className="whyPanel submissionDetails">
      <div className="whyRow">
        <h5>Recorded at submission</h5>
        <dl className="submissionRecord">
          <div><dt>End client</dt><dd>{endClient || <span className="subtle">Not identified</span>}</dd></div>
          <div><dt>Location</dt><dd>{trimmed(detail.location_snapshot) || <span className="subtle">Not recorded</span>}</dd></div>
          <div><dt>Submitted</dt><dd>{shortDate(detail.resume_submitted_at) || <span className="subtle">Not recorded</span>}</dd></div>
          <div><dt>Method</dt><dd>{detail.submission_method || <span className="subtle">Not recorded</span>}</dd></div>
          <div><dt>Resume sent</dt><dd>{resumeSent}</dd></div>
          <div><dt>Record</dt><dd>{detail.record_id || <span className="subtle">None</span>}</dd></div>
        </dl>
      </div>

      <div className="whyRow">
        <h5>Where this came from</h5>
        {state.sourcing ? renderContactDetailsGrid(state.sourcing, {
          id: detail.id,
          role: detail.job_title_snapshot,
          location: detail.location_snapshot ?? '',
          resume_file_name: detail.resume_file_name_snapshot,
          ats_score: detail.ats_score,
          ats_summary: detail.ats_summary,
        }) : (
          // The endpoint refuses an email that never reached needs_review or
          // approved_sent, which is a handful of rows, not an error state.
          <p className="subtle">{state.sourcingError || 'The source email is no longer available.'}</p>
        )}
      </div>

      <div className="whyRow">
        <h5>Trail</h5>
        {detail.events.length ? (
          <ol className="submissionTrail">
            {detail.events.map((event) => {
              const trigger = eventTrigger(event.metadata_json)
              return (
                <li key={event.id}>
                  <span className="submissionTrailWhen">{new Date(event.occurred_at).toLocaleString()}</span>
                  <span className="submissionTrailWhat">{event.event_type.replaceAll('_', ' ')}</span>
                  <span className="subtle">by {event.event_source}{trigger ? ` · ${trigger}` : ''}</span>
                  {event.note ? <span className="submissionTrailNote">{event.note}</span> : null}
                </li>
              )
            })}
          </ol>
        ) : <p className="subtle">No events recorded.</p>}
      </div>

      {detail.rejection_detail_tags.length ? (
        <div className="whyRow">
          <h5>Rejection detail</h5>
          <ul className="submissionTags">
            {detail.rejection_detail_tags.map((tag) => (
              <li key={`${tag.category}:${tag.value}`}>
                {tag.category.replaceAll('_', ' ')}: {tag.value || 'unspecified'}
                <span className="subtle"> · {tag.source === 'ai' && !tag.confirmed_at ? 'AI, unconfirmed' : tag.source === 'ai' ? 'AI, confirmed' : 'you'}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

export default function SubmissionsTab({ apiBase, resumes, resumeAssetId = null, filterValues = submissionDefaultFilterValues, sortValue = 'newest' }: Props) {
  const [rows, setRows] = useState<ApplicationCard[]>([])
  const [suggestions, setSuggestions] = useState<ApplicationSuggestion[]>([])
  const [status, setStatus] = useState<ResumeSubmissionStatus | 'all'>('all')
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [manual, setManual] = useState<ManualApplicationInput | null>(null)
  const [expandedGapId, setExpandedGapId] = useState<number | null>(null)
  const [pendingRejection, setPendingRejection] = useState<{ id: number; force: boolean; category: string; value: string } | null>(null)
  const [outreach, setOutreach] = useState<ApplicationOutreachMessage | null>(null)
  // Rows the user has flagged as a correction, which permits a backwards status move.
  const [correcting, setCorrecting] = useState<Set<number>>(new Set())
  const [lookupToken, setLookupToken] = useState('')
  const [lookup, setLookup] = useState<VariantLookupResult | null>(null)
  const [lookupError, setLookupError] = useState('')
  const [lookupBusy, setLookupBusy] = useState(false)
  const [why, setWhy] = useState<Record<number, WhyState>>({})
  const [expandedDetailsId, setExpandedDetailsId] = useState<number | null>(null)
  const [details, setDetails] = useState<Record<number, DetailsState>>({})

  // Same lazy shape as toggleWhy: the list endpoint returns no events, and
  // asking it for them would cost four extra queries per card on every page
  // load to fill a panel almost no row ever opens.
  const toggleDetails = (row: ApplicationCard) => {
    if (expandedDetailsId === row.id) {
      setExpandedDetailsId(null)
      return
    }
    setExpandedDetailsId(row.id)
    if (details[row.id]?.detail) return
    setDetails((current) => ({ ...current, [row.id]: { loading: true, detail: null, sourcing: null, sourcingError: '', error: '' } }))
    const sourceEmailId = row.source_recruiter_email_id
    void Promise.all([
      getApplicationDetail(apiBase, row.id),
      // A missing source trail is normal - a hand-logged submission has no email,
      // and the endpoint refuses one that never reached needs_review or
      // approved_sent. Neither should take the whole panel down with it.
      sourceEmailId
        ? fetchCandidateSentDetails(apiBase, sourceEmailId).catch((reason: unknown) => reason as Error)
        : Promise.resolve(null),
    ])
      .then(([detail, sourcing]) => setDetails((current) => ({
        ...current,
        [row.id]: {
          loading: false,
          detail,
          sourcing: sourcing instanceof Error ? null : sourcing,
          sourcingError: sourcing instanceof Error ? sourcing.message : '',
          error: '',
        },
      })))
      .catch((reason) => setDetails((current) => ({
        ...current,
        [row.id]: { loading: false, detail: null, sourcing: null, sourcingError: '', error: (reason as Error).message },
      })))
  }

  const toggleWhy = (row: ApplicationCard) => {
    if (expandedGapId === row.id) {
      setExpandedGapId(null)
      return
    }
    setExpandedGapId(row.id)
    if (why[row.id]?.data) return
    setWhy((current) => ({ ...current, [row.id]: { loading: true, data: null, error: '' } }))
    getWhyThisResume(apiBase, row.id)
      .then((data) => setWhy((current) => ({ ...current, [row.id]: { loading: false, data, error: '' } })))
      .catch((reason) => setWhy((current) => ({ ...current, [row.id]: { loading: false, data: null, error: (reason as Error).message } })))
  }

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [applications, pending] = await Promise.all([
        listApplications({ apiBase, q: '', status: 'all', resumeAssetId, resumeSubmissionStatus: status, filters: submissionFiltersToParams(filterValues), sort: sortValue }),
        listApplicationSuggestions(apiBase),
      ])
      setRows(applications)
      setSuggestions(pending.filter((item) => ['new_variant_needed', 'email_positioning', 'skill_gap_pattern'].includes(item.suggestion_type)))
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setLoading(false)
    }
  }, [apiBase, filterValues, resumeAssetId, sortValue, status])

  useEffect(() => {
    const timer = window.setTimeout(() => { void load() }, 120)
    return () => window.clearTimeout(timer)
  }, [load])

  const replaceRow = (updated: ApplicationCard) => {
    setRows((current) => current.map((row) => row.id === updated.id ? updated : row))
  }

  const updateStatus = async (
    row: ApplicationCard,
    next: typeof STATUS_OPTIONS[number],
    force = false,
    tag?: { category: string; value: string },
  ) => {
    setBusyId(row.id)
    setError('')
    try {
      const updated = await updateResumeSubmissionStatus(apiBase, row.id, next, {
        force,
        rejection_detail_tags: next === 'rejected' && tag ? [{ category: tag.category, value: tag.value }] : [],
      })
      replaceRow(updated)
      setPendingRejection((current) => (current?.id === row.id ? null : current))
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const chooseStatus = (row: ApplicationCard, value: string, force: boolean) => {
    if (!value) return
    if (value === '__interview') {
      void logInterview(row)
      return
    }
    if (value === 'rejected') {
      setPendingRejection({ id: row.id, force, category: 'missing_skill', value: '' })
      return
    }
    void updateStatus(row, value as typeof STATUS_OPTIONS[number], force)
  }

  const confirmRejection = (row: ApplicationCard) => {
    if (!pendingRejection || pendingRejection.id !== row.id) return
    void updateStatus(row, 'rejected', pendingRejection.force, { category: pendingRejection.category, value: pendingRejection.value })
  }

  const cancelRejection = () => setPendingRejection(null)

  const logInterview = async (row: ApplicationCard) => {
    setBusyId(row.id)
    try {
      replaceRow(await addApplicationInterview(apiBase, row.id, {
        round_type: 'interview_1',
        scheduled_at: null,
        format: '',
        interviewer_names: '',
        sync_application_status: true,
      }))
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const submitManual = async (event: FormEvent) => {
    event.preventDefault()
    if (!manual) return
    setBusyId(-1)
    setError('')
    try {
      const created = await createManualApplication(apiBase, {
        ...manual,
        resume_submitted_at: manual.resume_submitted_at ? `${manual.resume_submitted_at}T12:00:00Z` : null,
      })
      setRows((current) => [created, ...current])
      setManual(null)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const acceptSuggestion = async (suggestion: ApplicationSuggestion) => {
    setBusyId(suggestion.application_id)
    try {
      if (suggestion.suggestion_type === 'email_positioning') {
        const messageId = Number(suggestion.payload.last_outreach_message_id)
        if (messageId) setOutreach(await getOutreachMessage(apiBase, suggestion.application_id, messageId))
      }
      replaceRow(await acceptApplicationSuggestion(apiBase, suggestion.id))
      setSuggestions((current) => current.filter((item) => item.id !== suggestion.id))
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const runLookup = async (event: FormEvent) => {
    event.preventDefault()
    if (!lookupToken.trim()) return
    setLookupBusy(true)
    setLookupError('')
    setLookup(null)
    try {
      setLookup(await lookupVariantToken(apiBase, lookupToken.trim()))
    } catch (reason) {
      setLookupError((reason as Error).message)
    } finally {
      setLookupBusy(false)
    }
  }

  return (
    <section className="resumeTrackingPanel">
      <h2 className="visuallyHidden">Submissions</h2>

      <form className="variantLookup" onSubmit={runLookup}>
        <label htmlFor="variantLookupInput">
          Trace a callback
          <span className="subtle"> — paste the marker from the sent mail (e.g. CJ-R14-8842)</span>
        </label>
        <div className="variantLookupRow">
          <input
            id="variantLookupInput"
            value={lookupToken}
            onChange={(event) => setLookupToken(event.target.value)}
            placeholder="CJ-R14-8842"
            aria-describedby={lookupError ? 'variantLookupError' : undefined}
          />
          <button type="submit" disabled={lookupBusy || !lookupToken.trim()}>{lookupBusy ? 'Looking up...' : 'Look up'}</button>
          {lookup || lookupError ? (
            <button type="button" className="linkButton" onClick={() => { setLookup(null); setLookupError(''); setLookupToken('') }}>Clear</button>
          ) : null}
        </div>
        {lookupError ? <p className="errorText" id="variantLookupError" role="alert">{lookupError}</p> : null}
        {lookup ? (
          <dl className="variantLookupResult">
            <div><dt>Resume sent</dt><dd><strong>{lookup.variant_code}</strong>{lookup.variant_label ? ` · ${formatVariantLabel(lookup.variant_label)}` : ''}</dd></div>
            <div><dt>Role</dt><dd>{lookup.role || lookup.subject || 'Not recorded'}</dd></div>
            <div><dt>Recruiter</dt><dd>{lookup.recruiter_email || 'Not recorded'}</dd></div>
            <div><dt>Sent</dt><dd>{lookup.sent_at ? new Date(lookup.sent_at).toLocaleDateString() : 'Not recorded'}</dd></div>
          </dl>
        ) : null}
      </form>

      <div className="resumeTrackingToolbar">
        <label>Status<select value={status} onChange={(event) => setStatus(event.target.value as ResumeSubmissionStatus | 'all')}><option value="all">All</option><option value="not_submitted">Not submitted</option><option value="submitted">Submitted</option>{STATUS_OPTIONS.map((value) => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}<option value="interview_scheduled">Interview scheduled</option></select></label>
        <button type="button" onClick={() => setManual(blankManual(resumes, resumeAssetId))}>Log submission</button>
      </div>

      {error ? <p className="errorText" role="alert">{error}</p> : null}
      {loading ? <p className="subtle">Loading submissions...</p> : null}

      {manual ? (
        <form className="resumeTrackingForm" onSubmit={submitManual}>
          <h3>Log a submission</h3>
          <label>Resume<select required value={manual.resume_asset_id} onChange={(event) => setManual({ ...manual, resume_asset_id: Number(event.target.value) })}>{resumes.map((resume) => <option key={resume.id} value={resume.id}>{`${resume.variant_code || `R${String(resume.id).padStart(2, '0')}`} - ${formatVariantLabel(resume.variant_label) || resume.file_name}`}</option>)}</select></label>
          <label>Recruiter name<input required value={manual.manual_recruiter_name} onChange={(event) => setManual({ ...manual, manual_recruiter_name: event.target.value })} /></label>
          <label>Company<input required value={manual.manual_recruiter_company} onChange={(event) => setManual({ ...manual, manual_recruiter_company: event.target.value })} /></label>
          <label>Email<input type="email" value={manual.manual_recruiter_email} onChange={(event) => setManual({ ...manual, manual_recruiter_email: event.target.value })} /></label>
          <label>Phone<input value={manual.manual_recruiter_phone} onChange={(event) => setManual({ ...manual, manual_recruiter_phone: event.target.value })} /></label>
          <label>LinkedIn<input type="url" value={manual.manual_recruiter_linkedin_url} onChange={(event) => setManual({ ...manual, manual_recruiter_linkedin_url: event.target.value })} /></label>
          <label>Job title<input required value={manual.manual_job_title} onChange={(event) => setManual({ ...manual, manual_job_title: event.target.value })} /></label>
          <label>End client <span className="field-optional">(optional)</span><input value={manual.manual_end_client} placeholder="Leave blank if not stated" onChange={(event) => setManual({ ...manual, manual_end_client: event.target.value })} /><small>The company the work is actually for. Leave blank if the posting does not say — blank records &ldquo;not identified&rdquo;, not &ldquo;no end client&rdquo;. Do not enter the recruiter&rsquo;s own company.</small></label>
          <label>Method<input value={manual.submission_method} onChange={(event) => setManual({ ...manual, submission_method: event.target.value })} placeholder="email" /></label>
          <label>Submission date<input type="date" value={manual.resume_submitted_at ?? ''} onChange={(event) => setManual({ ...manual, resume_submitted_at: event.target.value })} /></label>
          <label className="wideField">Job description<textarea value={manual.manual_jd_text} onChange={(event) => setManual({ ...manual, manual_jd_text: event.target.value })} /></label>
          <label className="wideField">Source note<textarea value={manual.manual_source_note} onChange={(event) => setManual({ ...manual, manual_source_note: event.target.value })} /></label>
          <div className="wideField buttonRow"><button type="submit" disabled={busyId === -1}>Save submission</button><button type="button" onClick={() => setManual(null)}>Cancel</button></div>
        </form>
      ) : null}

      {suggestions.length ? <section className="trackingSuggestions"><h3>Suggestions</h3>{suggestions.map((suggestion) => <article key={suggestion.id}><p>{suggestion.reason}</p><div className="buttonRow"><button type="button" onClick={() => void acceptSuggestion(suggestion)}>Accept</button><button type="button" onClick={() => dismissApplicationSuggestion(apiBase, suggestion.id).then(() => setSuggestions((current) => current.filter((item) => item.id !== suggestion.id)))}>Dismiss</button></div></article>)}</section> : null}

      {outreach ? <form className="resumeTrackingForm" onSubmit={(event) => { event.preventDefault(); void sendApplicationMessage(apiBase, outreach.application_id, { to: rows.find((row) => row.id === outreach.application_id)?.current_recruiter_email || rows.find((row) => row.id === outreach.application_id)?.manual_recruiter_email || '', subject: outreach.subject, body: outreach.body, message_kind: 'followup', include_resume: true, attachment_asset_ids: [], draft_source: 'revised', ai_model: outreach.ai_model }).then(({ application }) => { replaceRow(application); setOutreach(null) }).catch((reason) => setError((reason as Error).message)) }}><h3>Revise outreach</h3><label className="wideField">Subject<input value={outreach.subject} onChange={(event) => setOutreach({ ...outreach, subject: event.target.value })} /></label><label className="wideField">Body<textarea value={outreach.body} onChange={(event) => setOutreach({ ...outreach, body: event.target.value })} /></label><div className="wideField buttonRow"><button type="submit">Review complete — send</button><button type="button" onClick={() => setOutreach(null)}>Cancel</button></div></form> : null}

      <div className="submissionList">
        {rows.map((row) => {
          const email = row.is_manual_entry ? row.manual_recruiter_email : row.current_recruiter_email
          const phone = row.is_manual_entry ? row.manual_recruiter_phone : row.current_recruiter_phone_display
          const linkedIn = row.is_manual_entry ? row.manual_recruiter_linkedin_url : row.current_recruiter_linkedin_url
          const unconfirmed = row.rejection_detail_tags.filter((tag) => tag.source === 'ai' && !tag.confirmed_at)
          const endClient = recordedEndClient(row)
          return <article className="submissionCard" key={row.id}>
            <div>
              <h3>{row.job_title_snapshot || 'Untitled role'}</h3>
              <p className="submissionIdentity">
                <Field label="Recruiter" snapshot={row.recruiter_name_snapshot} current={row.current_recruiter_name} />
                <Field label="Company" snapshot={row.recruiter_company_snapshot} current={row.current_recruiter_company} />
                {/* Omitted rather than shown blank. "Not identified" belongs in the
                    details panel, where there is room to say what it means. */}
                {endClient ? <Field label="End client" snapshot={endClient} current={row.current_end_client} /> : null}
              </p>
              <p className="subtle">{email || 'No email'}{phone ? ` · ${phone}` : ''}{linkedIn ? <> · <a href={linkedIn} target="_blank" rel="noreferrer">LinkedIn</a></> : null}</p>
              <p className="subtle submissionMeta">{[
                trimmed(row.location_snapshot),
                shortDate(row.resume_submitted_at),
                row.submission_method,
                `${variantCode(row.resume_asset_id)} · ${row.resume_file_name_snapshot}`,
              ].filter(Boolean).join(' · ')}</p>
            </div>
            <span className={`statusBadge statusBadge--${STATUS_TONE[row.resume_submission_status] ?? 'neutral'}`}>{row.resume_submission_status.replaceAll('_', ' ')}</span>
            <div className="submissionActions">
              {row.resume_submission_status === 'not_submitted' ? <span className="subtle">Not yet submitted</span> : (
                <label>Update status
                  {/* Bound to the row's real status, so the choice sticks instead of
                      snapping back to a placeholder and looking like nothing happened. */}
                  <select
                    value={pendingRejection?.id === row.id ? 'rejected' : row.resume_submission_status}
                    disabled={busyId === row.id}
                    onChange={(event) => chooseStatus(row, event.target.value, correcting.has(row.id))}
                  >
                    {!STATUS_OPTIONS.includes(row.resume_submission_status as typeof STATUS_OPTIONS[number]) ? (
                      <option value={row.resume_submission_status} disabled>{row.resume_submission_status.replaceAll('_', ' ')} (current)</option>
                    ) : null}
                    {STATUS_OPTIONS.map((value) => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}
                    <option value="__interview">interview (log a round)</option>
                  </select>
                </label>
              )}
              <label className="correctionToggle" title="Allows moving a status backwards to fix a mistake">
                <input type="checkbox" checked={correcting.has(row.id)} onChange={(event) => setCorrecting((current) => { const next = new Set(current); if (event.target.checked) next.add(row.id); else next.delete(row.id); return next })} />
                <span>Correcting a mistake</span>
              </label>
              <button type="button" onClick={() => toggleWhy(row)} aria-expanded={expandedGapId === row.id}>{expandedGapId === row.id ? 'Hide' : 'Why this resume'}</button>
              {/* Gated on the source email the same way AppTSPage gates its own
                  button: without one there is no record to audit, and an empty
                  panel is a worse answer than a disabled control that says why. */}
              <button
                type="button"
                onClick={() => toggleDetails(row)}
                aria-expanded={expandedDetailsId === row.id}
                disabled={!row.source_recruiter_email_id}
                title={row.source_recruiter_email_id ? undefined : 'Logged by hand, so there is no source email to audit.'}
              >{expandedDetailsId === row.id ? 'Hide details' : 'View details'}</button>
            </div>
            {pendingRejection?.id === row.id ? (
              <div className="rejectionTagRow">
                <select aria-label="Rejection category" value={pendingRejection.category} onChange={(event) => setPendingRejection({ ...pendingRejection, category: event.target.value })}>{REJECTION_CATEGORIES.map((value) => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}</select>
                <input aria-label="Rejection detail" value={pendingRejection.value} onChange={(event) => setPendingRejection({ ...pendingRejection, value: event.target.value })} placeholder="Reason or skill" autoFocus />
                <button type="button" disabled={busyId === row.id} onClick={() => confirmRejection(row)}>Confirm rejection</button>
                <button type="button" disabled={busyId === row.id} onClick={cancelRejection}>Cancel</button>
              </div>
            ) : null}
            {unconfirmed.map((tag) => <p key={`${tag.category}:${tag.value}`} className="aiTagPrompt">AI suggests {tag.category.replaceAll('_', ' ')}: {tag.value || 'unspecified'} <button type="button" onClick={() => void updateResumeSubmissionStatus(apiBase, row.id, 'rejected', { force: true, rejection_detail_tags: [{ category: tag.category, value: tag.value }] }).then(replaceRow)}>Confirm</button></p>)}
            {expandedGapId === row.id ? <WhyPanel state={why[row.id]} /> : null}
            {expandedDetailsId === row.id ? <DetailsPanel state={details[row.id]} /> : null}
          </article>
        })}
        {!loading && !rows.length ? <p className="subtle">No submissions match these filters.</p> : null}
      </div>
    </section>
  )
}
