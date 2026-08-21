import { useEffect, useMemo, useRef, useState } from 'react'

import {
  createApplicationEvent,
  deleteApplication,
  getApplication,
  getApplicationsDashboardSummary,
  listApplications,
  updateApplication,
} from './api'
import type { ApplicationCard, ApplicationDashboardSummary, ApplicationStatus } from './types'

const PAGE_SIZE = 10
const STATUSES: ApplicationStatus[] = [
  'matched', 'contacted', 'recruiter_responded', 'resume_shared', 'rtr_requested', 'rtr_confirmed',
  'submitted_to_client', 'client_reviewing', 'interview_1', 'interview_2', 'final_interview', 'offer',
  'hired', 'rejected', 'withdrawn', 'no_response', 'position_closed', 'duplicate',
]

type ApplicationEdit = {
  next_action_type: string
  next_action_at: string
  closed_reason: string
}

type EventDraft = {
  event_type: 'note' | 'email_linked' | 'call_note'
  note: string
  linked_recruiter_email_id: string
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

export default function ApplicationsTab({ apiBase, refreshToken, onToast }: ApplicationsTabProps) {
  const [rows, setRows] = useState<ApplicationCard[]>([])
  const [summary, setSummary] = useState<ApplicationDashboardSummary>({ due_today: 0, waiting_on_recruiter: 0, interviews: 0, closed_recent: 0 })
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<'all' | ApplicationStatus>('all')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [details, setDetails] = useState<Record<number, ApplicationCard>>({})
  const [edits, setEdits] = useState<Record<number, Partial<ApplicationEdit>>>({})
  const [eventDrafts, setEventDrafts] = useState<Record<number, EventDraft>>({})
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
    ])
      .then(([items, counts]) => {
        if (requestId !== requestIdRef.current) return
        setRows(items)
        setSummary(counts)
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
    patch: Partial<Pick<ApplicationCard, 'status' | 'next_action_type' | 'next_action_at' | 'closed_reason'>>,
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

  return (
    <div className="applicationsTab">
      <div className="applicationSummary" aria-label="Application dashboard summary">
        <div><strong>{summary.due_today}</strong><span>Due today</span></div>
        <div><strong>{summary.waiting_on_recruiter}</strong><span>Waiting on recruiter</span></div>
        <div><strong>{summary.interviews}</strong><span>Interviews</span></div>
        <div><strong>{summary.closed_recent}</strong><span>Closed in 14 days</span></div>
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
                  {STATUSES.map((value) => <option key={value} value={value}>{label(value)}</option>)}
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
