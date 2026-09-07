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
  getOutreachMessage,
  getWhyThisResume,
  lookupVariantToken,
  updateResumeSubmissionStatus,
} from './api'
import type { ApplicationOutreachMessage, ManualApplicationInput, ResumeSubmissionStatus, VariantLookupResult, WhyThisResume } from './types'

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

const pct = (value: number | null, scale = 100) => value == null ? null : `${Math.round(value * scale)}%`

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
          return <article className="submissionCard" key={row.id}>
            <div><h3>{row.job_title_snapshot || 'Untitled role'}</h3><p>{row.recruiter_name_snapshot} · {row.recruiter_company_snapshot} · {row.end_client_snapshot}</p><p className="subtle">{email || 'No email'}{phone ? ` · ${phone}` : ''}{linkedIn ? <> · <a href={linkedIn} target="_blank" rel="noreferrer">LinkedIn</a></> : null}</p></div>
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
              <button type="button" onClick={() => toggleWhy(row)} aria-expanded={expandedGapId === row.id}>Why this resume</button>
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
          </article>
        })}
        {!loading && !rows.length ? <p className="subtle">No submissions match these filters.</p> : null}
      </div>
    </section>
  )
}
