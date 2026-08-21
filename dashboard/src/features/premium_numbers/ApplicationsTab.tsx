import { useEffect, useMemo, useRef, useState } from 'react'

import {
  ApplicationDuplicateConflictError,
  acceptApplicationSuggestion,
  addApplicationInterview,
  createApplicationEvent,
  deleteApplication,
  deleteApplicationInterview,
  dismissApplicationSuggestion,
  draftApplicationMessage,
  getApplication,
  getApplicationsDashboardSummary,
  listAttachmentOptions,
  listApplications,
  listApplicationSuggestions,
  requestApplicationRtr,
  runReminderSweepNow,
  sendApplicationMessage,
  submitApplicationToClient,
  updateApplication,
  updateApplicationInterview,
  updateApplicationRtr,
} from './api'
import type {
  ApplicationCard,
  ApplicationDashboardSummary,
  ApplicationDuplicateSummary,
  ApplicationInterview,
  ApplicationMessageKind,
  ApplicationStatus,
  ApplicationSuggestion,
  AttachmentAssetOption,
} from './types'

const PAGE_SIZE = 10
const STATUSES: ApplicationStatus[] = [
  'matched', 'contacted', 'recruiter_responded', 'resume_shared', 'rtr_requested', 'rtr_confirmed',
  'submitted_to_client', 'client_reviewing', 'interview_1', 'interview_2', 'final_interview', 'offer',
  'hired', 'rejected', 'withdrawn', 'no_response', 'position_closed', 'duplicate',
]
const EDITABLE_STATUSES = STATUSES.filter((value) => value !== 'submitted_to_client')
const CLOSED_STATUSES: ApplicationStatus[] = ['hired', 'rejected', 'withdrawn', 'no_response', 'position_closed', 'duplicate']
const CLOSED_REASON_CODES = [
  'rate_mismatch', 'skills_gap', 'client_freeze', 'position_filled', 'candidate_declined',
  'recruiter_unresponsive', 'other',
] as const
const INTERVIEW_ROUND_TYPES: ApplicationInterview['round_type'][] = [
  'recruiter_screen', 'interview_1', 'interview_2', 'final_interview', 'other',
]
const INTERVIEW_RESULTS: ApplicationInterview['result'][] = [
  'scheduled', 'completed', 'passed', 'failed', 'cancelled', 'rescheduled',
]

type ApplicationEdit = {
  next_action_type: string
  next_action_at: string
  closed_reason: string
  closed_reason_code: string
}

type EventDraft = {
  event_type: 'note' | 'email_linked' | 'call_note'
  note: string
  linked_recruiter_email_id: string
}

type RtrDraft = {
  role_scope: string
  end_client_scope: string
  expires_at: string
}

type RtrProofDraft = {
  proof_attachment_id: string
  proof_recruiter_email_id: string
}

type InterviewDraft = {
  round_type: ApplicationInterview['round_type']
  scheduled_at: string
  format: string
  interviewer_names: string
  sync_application_status: boolean
}

type ApplicationDraftEdit = {
  message_kind: ApplicationMessageKind
  to: string
  cc: string
  thread_id: string | null
  subject: string
  body: string
  source: string
  include_resume: boolean
  attachment_asset_ids: number[]
}

type ApplicationsTabProps = {
  apiBase: string
  refreshToken: number
  onToast: (message: string) => void
}

function label(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function dateTimeLabel(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '--'
}

function localDateTimeValue(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}

function emptyApplicationDraft(): ApplicationDraftEdit {
  return {
    message_kind: 'followup',
    to: '',
    cc: '',
    thread_id: null,
    subject: '',
    body: '',
    source: '',
    include_resume: true,
    attachment_asset_ids: [],
  }
}

function draftSourceLabel(source: string): string {
  if (source === 'deepseek') return 'AI-drafted'
  if (source === 'rules_only') return 'Template (AI unavailable)'
  if (source === 'ai_disabled') return 'Template'
  return ''
}

export default function ApplicationsTab({ apiBase, refreshToken, onToast }: ApplicationsTabProps) {
  const [rows, setRows] = useState<ApplicationCard[]>([])
  const [summary, setSummary] = useState<ApplicationDashboardSummary>({ due_today: 0, waiting_on_recruiter: 0, interviews: 0, closed_recent: 0, pending_suggestions: 0 })
  const [suggestions, setSuggestions] = useState<ApplicationSuggestion[]>([])
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<'all' | ApplicationStatus>('all')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [details, setDetails] = useState<Record<number, ApplicationCard>>({})
  const [attachments, setAttachments] = useState<AttachmentAssetOption[]>([])
  const [edits, setEdits] = useState<Record<number, Partial<ApplicationEdit>>>({})
  const [eventDrafts, setEventDrafts] = useState<Record<number, EventDraft>>({})
  const [rtrDrafts, setRtrDrafts] = useState<Record<number, RtrDraft>>({})
  const [rtrProofDrafts, setRtrProofDrafts] = useState<Record<number, RtrProofDraft>>({})
  const [interviewDrafts, setInterviewDrafts] = useState<Record<number, InterviewDraft>>({})
  const [interviewEdits, setInterviewEdits] = useState<Record<number, Partial<ApplicationInterview>>>({})
  const [draftEdits, setDraftEdits] = useState<Record<number, ApplicationDraftEdit>>({})
  const [duplicateConflicts, setDuplicateConflicts] = useState<Record<number, ApplicationDuplicateSummary[]>>({})
  const [error, setError] = useState('')
  const requestIdRef = useRef(0)

  const load = () => {
    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    setLoading(true)
    setError('')
    return Promise.all([
      listApplications({ apiBase, q: search, status }),
      getApplicationsDashboardSummary(apiBase),
      listApplicationSuggestions(apiBase),
    ])
      .then(([items, counts, pendingSuggestions]) => {
        if (requestId !== requestIdRef.current) return
        setRows(items)
        setSummary(counts)
        setSuggestions(pendingSuggestions)
        setPage(1)
      })
      .catch((reason) => {
        if (requestId === requestIdRef.current) setError((reason as Error).message)
      })
      .finally(() => {
        if (requestId === requestIdRef.current) setLoading(false)
      })
  }

  useEffect(() => {
    const timer = window.setTimeout(() => { load().catch(() => undefined) }, 150)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, refreshToken, search, status])

  useEffect(() => {
    listAttachmentOptions(apiBase)
      .then((items) => setAttachments(items.filter((item) => item.is_enabled)))
      .catch((reason) => setError((reason as Error).message))
  }, [apiBase])

  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const visible = useMemo(() => rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [page, rows])

  const refreshSummary = () => {
    getApplicationsDashboardSummary(apiBase).then(setSummary).catch(() => undefined)
  }

  const replaceRow = (updated: ApplicationCard) => {
    setRows((current) => current.map((row) => row.id === updated.id ? updated : row))
    setDetails((current) => current[updated.id] ? { ...current, [updated.id]: updated } : current)
  }

  const patchRow = async (
    id: number,
    patch: Partial<Pick<ApplicationCard, 'status' | 'next_action_type' | 'next_action_at' | 'closed_reason' | 'closed_reason_code'>>,
  ) => {
    setBusyId(id)
    setError('')
    try {
      replaceRow(await updateApplication(apiBase, id, patch))
      setEdits((current) => {
        const next = { ...current }
        delete next[id]
        return next
      })
      refreshSummary()
      onToast('Application saved')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const saveNextAction = (item: ApplicationCard) => {
    const edit = edits[item.id] ?? {}
    const nextActionType = edit.next_action_type ?? item.next_action_type ?? ''
    const nextActionAt = edit.next_action_at ?? localDateTimeValue(item.next_action_at)
    patchRow(item.id, {
      next_action_type: nextActionType.trim() || null,
      next_action_at: nextActionAt ? new Date(nextActionAt).toISOString() : null,
      closed_reason: (edit.closed_reason ?? item.closed_reason ?? '').trim() || null,
      ...(CLOSED_STATUSES.includes(item.status)
        ? { closed_reason_code: (edit.closed_reason_code ?? item.closed_reason_code ?? '').trim() || null }
        : {}),
    }).catch(() => undefined)
  }

  const toggleDetails = async (id: number) => {
    if (expandedId === id) {
      setExpandedId(null)
      return
    }
    setExpandedId(id)
    if (details[id]) return
    setBusyId(id)
    try {
      const detail = await getApplication(apiBase, id)
      setDetails((current) => ({ ...current, [id]: detail }))
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const addEvent = async (id: number) => {
    const draft = eventDrafts[id] ?? { event_type: 'note', note: '', linked_recruiter_email_id: '' }
    setBusyId(id)
    setError('')
    try {
      await createApplicationEvent(apiBase, id, {
        event_type: draft.event_type,
        note: draft.note,
        ...(draft.event_type === 'email_linked'
          ? { linked_recruiter_email_id: Number(draft.linked_recruiter_email_id) }
          : {}),
      })
      const detail = await getApplication(apiBase, id)
      setDetails((current) => ({ ...current, [id]: detail }))
      setEventDrafts((current) => ({
        ...current,
        [id]: { event_type: 'note', note: '', linked_recruiter_email_id: '' },
      }))
      onToast('Timeline updated')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const saveDetailMutation = async (
    id: number,
    action: () => Promise<ApplicationCard>,
    toast: string,
  ) => {
    setBusyId(id)
    setError('')
    try {
      const updated = await action()
      replaceRow(updated)
      setDetails((current) => ({ ...current, [id]: updated }))
      refreshSummary()
      onToast(toast)
      return updated
    } catch (reason) {
      setError((reason as Error).message)
      return null
    } finally {
      setBusyId(null)
    }
  }

  const requestRtr = async (item: ApplicationCard) => {
    const draft = rtrDrafts[item.id] ?? {
      role_scope: item.current_job_title || item.job_title_snapshot,
      end_client_scope: item.current_end_client || item.end_client_snapshot,
      expires_at: '',
    }
    const updated = await saveDetailMutation(
      item.id,
      () => requestApplicationRtr(apiBase, item.id, {
        role_scope: draft.role_scope,
        end_client_scope: draft.end_client_scope,
        expires_at: draft.expires_at ? new Date(draft.expires_at).toISOString() : null,
      }),
      'RTR requested',
    )
    if (updated) {
      setRtrDrafts((current) => {
        const next = { ...current }
        delete next[item.id]
        return next
      })
    }
  }

  const confirmRtr = async (applicationId: number, rtrId: number) => {
    const proof = rtrProofDrafts[applicationId] ?? {
      proof_attachment_id: '',
      proof_recruiter_email_id: '',
    }
    const updated = await saveDetailMutation(
      applicationId,
      () => updateApplicationRtr(apiBase, applicationId, rtrId, {
        status: 'confirmed',
        ...(proof.proof_attachment_id ? { proof_attachment_id: Number(proof.proof_attachment_id) } : {}),
        ...(proof.proof_recruiter_email_id ? { proof_recruiter_email_id: Number(proof.proof_recruiter_email_id) } : {}),
      }),
      'RTR confirmed',
    )
    if (updated) {
      setRtrProofDrafts((current) => {
        const next = { ...current }
        delete next[applicationId]
        return next
      })
    }
  }

  const setRtrStatus = (applicationId: number, rtrId: number, status: 'expired' | 'revoked') => {
    saveDetailMutation(
      applicationId,
      () => updateApplicationRtr(apiBase, applicationId, rtrId, { status }),
      `RTR marked ${status}`,
    ).catch(() => undefined)
  }

  const addInterview = async (applicationId: number) => {
    const draft = interviewDrafts[applicationId] ?? {
      round_type: 'interview_1',
      scheduled_at: '',
      format: '',
      interviewer_names: '',
      sync_application_status: true,
    }
    const updated = await saveDetailMutation(
      applicationId,
      () => addApplicationInterview(apiBase, applicationId, {
        round_type: draft.round_type,
        scheduled_at: draft.scheduled_at ? new Date(draft.scheduled_at).toISOString() : null,
        format: draft.format,
        interviewer_names: draft.interviewer_names,
        sync_application_status: draft.sync_application_status,
      }),
      'Interview round added',
    )
    if (updated) {
      setInterviewDrafts((current) => {
        const next = { ...current }
        delete next[applicationId]
        return next
      })
    }
  }

  const saveInterview = async (applicationId: number, interview: ApplicationInterview) => {
    const edit = interviewEdits[interview.id] ?? {}
    const updated = await saveDetailMutation(
      applicationId,
      () => updateApplicationInterview(apiBase, applicationId, interview.id, edit),
      'Interview updated',
    )
    if (updated) {
      setInterviewEdits((current) => {
        const next = { ...current }
        delete next[interview.id]
        return next
      })
    }
  }

  const removeInterview = (applicationId: number, interviewId: number) => {
    if (!window.confirm('Delete this interview round?')) return
    saveDetailMutation(
      applicationId,
      () => deleteApplicationInterview(apiBase, applicationId, interviewId),
      'Interview deleted',
    ).catch(() => undefined)
  }

  const submitToClient = async (applicationId: number, override = false) => {
    setBusyId(applicationId)
    setError('')
    try {
      const updated = await submitApplicationToClient(apiBase, applicationId, override)
      replaceRow(updated)
      setDetails((current) => ({ ...current, [applicationId]: updated }))
      setDuplicateConflicts((current) => {
        const next = { ...current }
        delete next[applicationId]
        return next
      })
      refreshSummary()
      onToast(override ? 'Duplicate warning acknowledged; application submitted' : 'Application submitted to client')
    } catch (reason) {
      if (reason instanceof ApplicationDuplicateConflictError) {
        setDuplicateConflicts((current) => ({ ...current, [applicationId]: reason.duplicates }))
      } else {
        setError((reason as Error).message)
      }
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (id: number) => {
    if (!window.confirm('Delete this tracked application? Its source opportunity and resume will be kept.')) return
    setBusyId(id)
    try {
      await deleteApplication(apiBase, id)
      setRows((current) => current.filter((row) => row.id !== id))
      setExpandedId((current) => current === id ? null : current)
      refreshSummary()
      onToast('Application deleted')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const acceptSuggestion = async (suggestion: ApplicationSuggestion) => {
    setBusyId(suggestion.application_id)
    setError('')
    try {
      const updated = await acceptApplicationSuggestion(apiBase, suggestion.id)
      replaceRow(updated)
      setSuggestions((current) => current.filter((item) => item.id !== suggestion.id))
      refreshSummary()
      onToast('Suggestion accepted')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const dismissSuggestion = async (suggestion: ApplicationSuggestion) => {
    setBusyId(suggestion.application_id)
    setError('')
    try {
      await dismissApplicationSuggestion(apiBase, suggestion.id)
      setSuggestions((current) => current.filter((item) => item.id !== suggestion.id))
      refreshSummary()
      onToast('Suggestion dismissed')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const updateDraft = (applicationId: number, patch: Partial<ApplicationDraftEdit>) => {
    setDraftEdits((current) => ({
      ...current,
      [applicationId]: { ...emptyApplicationDraft(), ...current[applicationId], ...patch },
    }))
  }

  const generateDraft = async (applicationId: number) => {
    const existing = draftEdits[applicationId] ?? emptyApplicationDraft()
    setBusyId(applicationId)
    setError('')
    try {
      const generated = await draftApplicationMessage(apiBase, applicationId, existing.message_kind)
      setDraftEdits((current) => ({
        ...current,
        [applicationId]: {
          ...existing,
          message_kind: generated.message_kind,
          to: generated.to,
          cc: generated.cc ?? '',
          thread_id: generated.thread_id,
          subject: generated.subject,
          body: generated.body,
          source: generated.source,
        },
      }))
      onToast('Draft ready for review')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const sendDraft = async (applicationId: number) => {
    const draft = draftEdits[applicationId]
    if (!draft?.to.trim() || !draft.body.trim()) return
    setBusyId(applicationId)
    setError('')
    try {
      const result = await sendApplicationMessage(apiBase, applicationId, {
        to: draft.to.trim(),
        cc: draft.cc.trim() || null,
        subject: draft.subject.trim(),
        body: draft.body,
        thread_id: draft.thread_id,
        message_kind: draft.message_kind,
        include_resume: draft.include_resume,
        attachment_asset_ids: draft.attachment_asset_ids,
      })
      replaceRow(result.application)
      setDetails((current) => ({ ...current, [applicationId]: result.application }))
      setDraftEdits((current) => {
        const next = { ...current }
        delete next[applicationId]
        return next
      })
      onToast('Message sent')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const checkReminders = async () => {
    setLoading(true)
    setError('')
    try {
      const created = await runReminderSweepNow(apiBase)
      setSuggestions((current) => [...created, ...current])
      refreshSummary()
      onToast(created.length ? `${created.length} reminder suggestion${created.length === 1 ? '' : 's'} added` : 'No new reminders')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="applicationsTab">
      <section className="applicationSuggestions" aria-label="Application suggestions">
        <div className="detailSectionHeader">
          <div><h3>Suggestions</h3><p className="subtle">Nothing changes until you accept.</p></div>
          <button type="button" onClick={checkReminders} disabled={loading}>Check reminders now</button>
        </div>
        {suggestions.length === 0 ? <p className="subtle">No pending suggestions.</p> : (
          <div className="applicationSuggestionList">
            {suggestions.map((suggestion) => (
              <article key={suggestion.id} className="applicationSuggestionRow">
                <div>
                  <span className="categoryChip">{label(suggestion.suggestion_type)}</span>
                  <strong>Application #{suggestion.application_id}</strong>
                  <p>{suggestion.reason}</p>
                  {suggestion.recruiter_email_id ? (
                    <a href={`${apiBase}/candidates/${suggestion.recruiter_email_id}`} target="_blank" rel="noreferrer">
                      Source email #{suggestion.recruiter_email_id}
                    </a>
                  ) : null}
                </div>
                <div className="rowBtns">
                  <button type="button" onClick={() => acceptSuggestion(suggestion)} disabled={busyId === suggestion.application_id}>Accept</button>
                  <button type="button" onClick={() => dismissSuggestion(suggestion)} disabled={busyId === suggestion.application_id}>Dismiss</button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
      <div className="applicationSummary" aria-label="Application dashboard summary">
        <div><strong>{summary.due_today}</strong><span>Due today</span></div>
        <div><strong>{summary.waiting_on_recruiter}</strong><span>Waiting on recruiter</span></div>
        <div><strong>{summary.interviews}</strong><span>Interviews</span></div>
        <div><strong>{summary.closed_recent}</strong><span>Closed in 14 days</span></div>
        <div><strong>{summary.pending_suggestions}</strong><span>Pending suggestions</span></div>
      </div>
      <div className="inventoryToolbar">
        <label className="inventorySearchField">
          <span>Search applications</span>
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search resume, role, recruiter, client..." />
        </label>
        <label>
          <span>Status</span>
          <select value={status} onChange={(event) => setStatus(event.target.value as 'all' | ApplicationStatus)}>
            <option value="all">All statuses</option>
            {STATUSES.map((value) => <option key={value} value={value}>{label(value)}</option>)}
          </select>
        </label>
      </div>
      {loading ? <p className="subtle">Loading applications...</p> : null}
      {error ? <p className="errorBanner">Applications error: {error}</p> : null}
      {!loading && rows.length === 0 ? <p className="inventoryEmpty">No tracked applications match these filters.</p> : null}
      <div className="opportunityGrid">
        {visible.map((item) => {
          const detail = details[item.id]
          const draft = eventDrafts[item.id] ?? { event_type: 'note', note: '', linked_recruiter_email_id: '' }
          const rtrDraft = rtrDrafts[item.id] ?? {
            role_scope: item.current_job_title || item.job_title_snapshot,
            end_client_scope: item.current_end_client || item.end_client_snapshot,
            expires_at: '',
          }
          const rtrProofDraft = rtrProofDrafts[item.id] ?? { proof_attachment_id: '', proof_recruiter_email_id: '' }
          const interviewDraft = interviewDrafts[item.id] ?? {
            round_type: 'interview_1' as const,
            scheduled_at: '',
            format: '',
            interviewer_names: '',
            sync_application_status: true,
          }
          const messageDraft = draftEdits[item.id] ?? emptyApplicationDraft()
          const currentRtr = detail?.rtr_history[0]
          const hasUnexpiredRtr = Boolean(
            currentRtr
            && !['expired', 'revoked'].includes(currentRtr.status)
            && (!currentRtr.expires_at || new Date(currentRtr.expires_at) > new Date()),
          )
          const conflicts = duplicateConflicts[item.id] ?? []
          const currentTitle = item.current_job_title || item.job_title_snapshot
          const currentCompany = item.current_recruiter_company || item.recruiter_company_snapshot
          const sourceChanged = currentTitle !== item.job_title_snapshot || currentCompany !== item.recruiter_company_snapshot
          return (
            <article key={item.id} className="opportunityCard applicationCard" data-application-id={item.id}>
              <header>
                <div>
                  <span className="categoryChip">{item.resume_file_name_snapshot} · v{item.resume_version_snapshot}</span>
                  <h3>{currentTitle || 'Tracked application'}</h3>
                </div>
                <select
                  aria-label={`Application status for ${currentTitle}`}
                  value={item.status}
                  onChange={(event) => patchRow(item.id, { status: event.target.value as ApplicationStatus })}
                  disabled={busyId === item.id}
                >
                  {item.status === 'submitted_to_client' ? <option value="submitted_to_client" disabled>Submitted To Client</option> : null}
                  {EDITABLE_STATUSES.map((value) => <option key={value} value={value}>{label(value)}</option>)}
                </select>
              </header>
              <div className="opportunitySummary">
                <p><strong>Recruiter:</strong> {item.current_recruiter_name || item.recruiter_name_snapshot || '--'}</p>
                <p><strong>Company:</strong> {currentCompany || '--'}</p>
                <p><strong>Phone:</strong> {item.current_recruiter_phone_display || '--'}</p>
                <p><strong>End client:</strong> {item.current_end_client || item.end_client_snapshot || '--'}</p>
                <p><strong>Last contact:</strong> {dateTimeLabel(item.last_contact_at)}</p>
                {sourceChanged ? <p className="snapshotNotice">Source details changed; the original resume/recruiter/job snapshot is preserved.</p> : null}
              </div>
              <div className="detailFormGrid">
                <label>
                  Next action
                  <input
                    value={edits[item.id]?.next_action_type ?? item.next_action_type ?? ''}
                    onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], next_action_type: event.target.value } }))}
                  />
                </label>
                <label>
                  Due at
                  <input
                    type="datetime-local"
                    value={edits[item.id]?.next_action_at ?? localDateTimeValue(item.next_action_at)}
                    onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], next_action_at: event.target.value } }))}
                  />
                </label>
                <label>
                  Closed reason
                  <input
                    value={edits[item.id]?.closed_reason ?? item.closed_reason ?? ''}
                    onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], closed_reason: event.target.value } }))}
                  />
                </label>
                <label>
                  Closed reason code
                  <select
                    value={edits[item.id]?.closed_reason_code ?? item.closed_reason_code ?? ''}
                    onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], closed_reason_code: event.target.value } }))}
                    disabled={!CLOSED_STATUSES.includes(item.status)}
                  >
                    <option value="">Select reason</option>
                    {CLOSED_REASON_CODES.map((value) => <option key={value} value={value}>{label(value)}</option>)}
                  </select>
                </label>
              </div>
              <div className="rowBtns">
                <button type="button" onClick={() => saveNextAction(item)} disabled={busyId === item.id || !edits[item.id]}>Save reminder</button>
                <button type="button" onClick={() => toggleDetails(item.id)} disabled={busyId === item.id}>{expandedId === item.id ? 'Hide timeline' : 'View timeline'}</button>
                <button type="button" className="dangerButton" onClick={() => remove(item.id)} disabled={busyId === item.id}>Delete</button>
              </div>
              {expandedId === item.id ? (
                <section className="applicationTimeline" aria-label={`Timeline for ${currentTitle}`}>
                  {!detail ? <p className="subtle">Loading timeline...</p> : (
                    <>
                      <section className="detailSection applicationWorkflowPanel">
                        <h4>Client submission</h4>
                        {item.status === 'submitted_to_client' ? (
                          <p className="subtle">Submitted {dateTimeLabel(item.submitted_to_client_at)}</p>
                        ) : (
                          <button type="button" onClick={() => submitToClient(item.id)} disabled={busyId === item.id}>
                            Submit to client
                          </button>
                        )}
                        {conflicts.length > 0 ? (
                          <div className="duplicateConflict" role="alert">
                            <p><strong>Possible duplicate submission</strong></p>
                            <ul>
                              {conflicts.map((conflict) => (
                                <li key={conflict.id}>
                                  #{conflict.id} · {conflict.job_title_snapshot || '--'} · {conflict.end_client_snapshot || '--'} · {label(conflict.status)}
                                </li>
                              ))}
                            </ul>
                            <button type="button" onClick={() => submitToClient(item.id, true)} disabled={busyId === item.id}>
                              Submit anyway
                            </button>
                          </div>
                        ) : null}
                      </section>

                      <section className="detailSection applicationWorkflowPanel">
                        <h4>RTR</h4>
                        {currentRtr ? (
                          <p>
                            <strong>{label(currentRtr.status)}</strong>
                            {' · '}{currentRtr.role_scope || item.job_title_snapshot || '--'}
                            {' · '}{currentRtr.end_client_scope || item.end_client_snapshot || '--'}
                            {currentRtr.expires_at ? ` · Expires ${dateTimeLabel(currentRtr.expires_at)}` : ''}
                          </p>
                        ) : <p className="subtle">No RTR history yet.</p>}
                        {!hasUnexpiredRtr ? (
                          <div className="detailFormGrid">
                            <label>
                              Role scope
                              <input value={rtrDraft.role_scope} onChange={(event) => setRtrDrafts((current) => ({ ...current, [item.id]: { ...rtrDraft, role_scope: event.target.value } }))} />
                            </label>
                            <label>
                              End client scope
                              <input value={rtrDraft.end_client_scope} onChange={(event) => setRtrDrafts((current) => ({ ...current, [item.id]: { ...rtrDraft, end_client_scope: event.target.value } }))} />
                            </label>
                            <label>
                              Expiry
                              <input type="datetime-local" value={rtrDraft.expires_at} onChange={(event) => setRtrDrafts((current) => ({ ...current, [item.id]: { ...rtrDraft, expires_at: event.target.value } }))} />
                            </label>
                            <button type="button" onClick={() => requestRtr(item)} disabled={busyId === item.id}>Request RTR</button>
                          </div>
                        ) : null}
                        {currentRtr?.status === 'requested' && hasUnexpiredRtr ? (
                          <div className="detailFormGrid">
                            <label>
                              Attachment proof
                              <select
                                value={rtrProofDraft.proof_attachment_id}
                                onChange={(event) => setRtrProofDrafts((current) => ({ ...current, [item.id]: { ...rtrProofDraft, proof_attachment_id: event.target.value } }))}
                              >
                                <option value="">No attachment selected</option>
                                {attachments.map((attachment) => <option key={attachment.id} value={attachment.id}>{attachment.file_name}</option>)}
                              </select>
                            </label>
                            <label>
                              Recruiter email ID proof
                              <input
                                type="number"
                                min={1}
                                value={rtrProofDraft.proof_recruiter_email_id}
                                onChange={(event) => setRtrProofDrafts((current) => ({ ...current, [item.id]: { ...rtrProofDraft, proof_recruiter_email_id: event.target.value } }))}
                              />
                            </label>
                            <div className="rowBtns">
                              <button
                                type="button"
                                onClick={() => confirmRtr(item.id, currentRtr.id)}
                                disabled={busyId === item.id || (!rtrProofDraft.proof_attachment_id && !rtrProofDraft.proof_recruiter_email_id)}
                              >
                                Confirm RTR
                              </button>
                              <button type="button" onClick={() => setRtrStatus(item.id, currentRtr.id, 'expired')} disabled={busyId === item.id}>Mark expired</button>
                              <button type="button" onClick={() => setRtrStatus(item.id, currentRtr.id, 'revoked')} disabled={busyId === item.id}>Revoke</button>
                            </div>
                          </div>
                        ) : null}
                        {currentRtr?.status === 'confirmed' && hasUnexpiredRtr ? (
                          <button type="button" onClick={() => setRtrStatus(item.id, currentRtr.id, 'revoked')} disabled={busyId === item.id}>Revoke RTR</button>
                        ) : null}
                      </section>

                      <section className="detailSection applicationWorkflowPanel">
                        <h4>Interview rounds</h4>
                        {detail.interviews.length === 0 ? <p className="subtle">No interview rounds yet.</p> : (
                          <div className="applicationInterviewList">
                            {detail.interviews.map((interview) => {
                              const edit = interviewEdits[interview.id] ?? {}
                              return (
                                <div key={interview.id} className="applicationInterviewRow">
                                  <p><strong>{label(interview.round_type)}</strong> · {dateTimeLabel(interview.scheduled_at)}</p>
                                  <div className="detailFormGrid">
                                    <label>
                                      Result
                                      <select
                                        value={edit.result ?? interview.result}
                                        onChange={(event) => setInterviewEdits((current) => ({ ...current, [interview.id]: { ...edit, result: event.target.value as ApplicationInterview['result'] } }))}
                                      >
                                        {INTERVIEW_RESULTS.map((value) => <option key={value} value={value}>{label(value)}</option>)}
                                      </select>
                                    </label>
                                    <label>
                                      Feedback
                                      <textarea
                                        rows={2}
                                        value={edit.feedback ?? interview.feedback}
                                        onChange={(event) => setInterviewEdits((current) => ({ ...current, [interview.id]: { ...edit, feedback: event.target.value } }))}
                                      />
                                    </label>
                                  </div>
                                  <div className="rowBtns">
                                    <button type="button" onClick={() => saveInterview(item.id, interview)} disabled={busyId === item.id || !interviewEdits[interview.id]}>Save interview</button>
                                    <button type="button" className="dangerButton" onClick={() => removeInterview(item.id, interview.id)} disabled={busyId === item.id}>Delete interview</button>
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                        )}
                        <div className="detailFormGrid applicationInterviewForm">
                          <label>
                            Round
                            <select value={interviewDraft.round_type} onChange={(event) => setInterviewDrafts((current) => ({ ...current, [item.id]: { ...interviewDraft, round_type: event.target.value as ApplicationInterview['round_type'] } }))}>
                              {INTERVIEW_ROUND_TYPES.map((value) => <option key={value} value={value}>{label(value)}</option>)}
                            </select>
                          </label>
                          <label>
                            Scheduled at
                            <input type="datetime-local" value={interviewDraft.scheduled_at} onChange={(event) => setInterviewDrafts((current) => ({ ...current, [item.id]: { ...interviewDraft, scheduled_at: event.target.value } }))} />
                          </label>
                          <label>
                            Format
                            <input value={interviewDraft.format} onChange={(event) => setInterviewDrafts((current) => ({ ...current, [item.id]: { ...interviewDraft, format: event.target.value } }))} />
                          </label>
                          <label>
                            Interviewers
                            <input value={interviewDraft.interviewer_names} onChange={(event) => setInterviewDrafts((current) => ({ ...current, [item.id]: { ...interviewDraft, interviewer_names: event.target.value } }))} />
                          </label>
                          <label className="checkboxLabel">
                            <input
                              type="checkbox"
                              checked={interviewDraft.sync_application_status}
                              onChange={(event) => setInterviewDrafts((current) => ({ ...current, [item.id]: { ...interviewDraft, sync_application_status: event.target.checked } }))}
                            />
                            Move application to this round (uncheck for historical rounds)
                          </label>
                          <button type="button" onClick={() => addInterview(item.id)} disabled={busyId === item.id}>Add interview</button>
                        </div>
                      </section>

                      <section className="detailSection applicationWorkflowPanel applicationDraftPanel">
                        <h4>Draft &amp; send</h4>
                        <p className="subtle">Generate a starting point, review every field, then send explicitly.</p>
                        <div className="detailFormGrid">
                          <label>
                            Message type
                            <select
                              aria-label={`Message type for ${currentTitle}`}
                              value={messageDraft.message_kind}
                              onChange={(event) => updateDraft(item.id, { message_kind: event.target.value as ApplicationMessageKind })}
                            >
                              <option value="followup">Follow-up</option>
                              <option value="submission_to_recruiter">Submission to recruiter</option>
                            </select>
                          </label>
                          <div className="rowBtns applicationDraftActions">
                            <button type="button" onClick={() => generateDraft(item.id)} disabled={busyId === item.id}>Generate draft</button>
                            {messageDraft.source ? <span className="subtle applicationDraftSource">{draftSourceLabel(messageDraft.source)}</span> : null}
                          </div>
                          <label>
                            To
                            <input value={messageDraft.to} onChange={(event) => updateDraft(item.id, { to: event.target.value })} />
                          </label>
                          <label>
                            Cc
                            <input value={messageDraft.cc} onChange={(event) => updateDraft(item.id, { cc: event.target.value })} />
                          </label>
                          <label>
                            Subject
                            <input value={messageDraft.subject} onChange={(event) => updateDraft(item.id, { subject: event.target.value })} />
                          </label>
                          <label>
                            Body
                            <textarea rows={8} value={messageDraft.body} onChange={(event) => updateDraft(item.id, { body: event.target.value })} />
                          </label>
                        </div>
                        <label className="checkboxLabel">
                          <input
                            type="checkbox"
                            checked={messageDraft.include_resume}
                            onChange={(event) => updateDraft(item.id, { include_resume: event.target.checked })}
                          />
                          Attach application resume ({item.resume_file_name_snapshot})
                        </label>
                        {attachments.length > 0 ? (
                          <fieldset className="applicationAttachmentList">
                            <legend>Additional attachments</legend>
                            {attachments.map((attachment) => (
                              <label key={attachment.id} className="checkboxLabel">
                                <input
                                  type="checkbox"
                                  checked={messageDraft.attachment_asset_ids.includes(attachment.id)}
                                  onChange={(event) => updateDraft(item.id, {
                                    attachment_asset_ids: event.target.checked
                                      ? [...messageDraft.attachment_asset_ids, attachment.id]
                                      : messageDraft.attachment_asset_ids.filter((id) => id !== attachment.id),
                                  })}
                                />
                                {attachment.file_name}
                              </label>
                            ))}
                          </fieldset>
                        ) : null}
                        <div className="rowBtns">
                          <button
                            type="button"
                            onClick={() => sendDraft(item.id)}
                            disabled={busyId === item.id || !messageDraft.to.trim() || !messageDraft.body.trim()}
                          >
                            Send
                          </button>
                        </div>
                        <p className="subtle">Consider updating status above if this changes where things stand.</p>
                      </section>

                      <h4>Activity timeline</h4>
                      {detail.events.length === 0 ? <p className="subtle">No timeline events yet.</p> : (
                        <ol>
                          {detail.events.map((event) => (
                            <li key={event.id}>
                              <strong>{label(event.event_type)}</strong>
                              <time>{dateTimeLabel(event.occurred_at)}</time>
                              {event.note ? <p>{event.note}</p> : null}
                              {event.linked_recruiter_email_id ? <small>Email ID {event.linked_recruiter_email_id}</small> : null}
                            </li>
                          ))}
                        </ol>
                      )}
                      <div className="applicationEventForm">
                        <label>
                          Activity type
                          <select
                            value={draft.event_type}
                            onChange={(event) => setEventDrafts((current) => ({ ...current, [item.id]: { ...draft, event_type: event.target.value as EventDraft['event_type'] } }))}
                          >
                            <option value="note">Note</option>
                            <option value="call_note">Call note</option>
                            <option value="email_linked">Link recruiter email</option>
                          </select>
                        </label>
                        {draft.event_type === 'email_linked' ? (
                          <label>
                            Recruiter email ID
                            <input
                              type="number"
                              min={1}
                              value={draft.linked_recruiter_email_id}
                              onChange={(event) => setEventDrafts((current) => ({ ...current, [item.id]: { ...draft, linked_recruiter_email_id: event.target.value } }))}
                            />
                          </label>
                        ) : null}
                        <label>
                          Note
                          <textarea
                            rows={3}
                            value={draft.note}
                            onChange={(event) => setEventDrafts((current) => ({ ...current, [item.id]: { ...draft, note: event.target.value } }))}
                          />
                        </label>
                        <button
                          type="button"
                          onClick={() => addEvent(item.id)}
                          disabled={busyId === item.id || (draft.event_type === 'email_linked' && !draft.linked_recruiter_email_id)}
                        >
                          Add to timeline
                        </button>
                      </div>
                    </>
                  )}
                </section>
              ) : null}
            </article>
          )
        })}
      </div>
      {rows.length > 0 ? (
        <footer className="inventoryPaginationFooter">
          <span>Showing {(page - 1) * PAGE_SIZE + 1} to {Math.min(page * PAGE_SIZE, rows.length)} of {rows.length} loaded entries</span>
          <nav className="pagination" aria-label="Application pages">
            <button type="button" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={page === 1}>‹</button>
            <span>Page {page} of {totalPages}</span>
            <button type="button" onClick={() => setPage((value) => Math.min(totalPages, value + 1))} disabled={page === totalPages}>›</button>
          </nav>
        </footer>
      ) : null}
    </div>
  )
}
