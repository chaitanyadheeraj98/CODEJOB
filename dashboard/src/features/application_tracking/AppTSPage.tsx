import { useEffect, useRef, useState } from 'react'
import type { FilterValues } from '../../components/FilterSortBar'
import CandidateCard from '../../components/CandidateCard'
import type { Candidate, SentItemDetails } from '../../App'
import { formatAtsScore, getAtsStrengthLabel, renderContactDetailsGrid } from '../../App'
import { filterSortRegistry, resolveRegistryEntry } from '../../filterSortRegistry'
import { CategoryChip, StatusBadge } from '../premium_numbers/StatusBadge'
import VerificationBadge from '../premium_numbers/VerificationBadge'
import JourneyColumn from './JourneyColumn'
import {
  RoleManifestForkRequiredError,
  approveSendCandidate,
  fetchApplicationSentDetails,
  fetchCandidateSentDetails,
  getApplication,
  listApplicationPage,
  listBookmarkedRequirements,
  regenerateCandidateDraft,
  rejectCandidate,
  retryRoleDetectionForCandidate,
  sendCandidateToFailedMapping,
  toggleCandidateTracking,
  updateApplication,
} from './api'
import type { ApplicationCard, ApplicationDuplicateSummary, ApplicationStatus } from '../premium_numbers/types'

const PAGE_SIZE = 10
const STATUSES: ApplicationStatus[] = [
  'matched', 'contacted', 'recruiter_responded', 'resume_shared', 'rtr_requested', 'rtr_confirmed',
  'submitted_to_client', 'client_reviewing', 'interview_1', 'interview_2', 'final_interview', 'offer',
  'hired', 'rejected', 'withdrawn', 'no_response', 'position_closed', 'duplicate',
]
const EDITABLE_STATUSES = STATUSES.filter((value) => value !== 'submitted_to_client')
const CLOSED_STATUSES: ApplicationStatus[] = ['hired', 'rejected', 'withdrawn', 'no_response', 'position_closed', 'duplicate']
const CLOSED_REASON_CODES = ['rate_mismatch', 'skills_gap', 'client_freeze', 'position_filled', 'candidate_declined', 'recruiter_unresponsive', 'other'] as const

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

type ApplicationEdit = { next_action_type: string; next_action_at: string; closed_reason: string; closed_reason_code: string }

type Props = { apiBase: string; refreshToken: number; activeTab?: 'bookmarked' | 'tracked'; onTabChange?: (tab: 'bookmarked' | 'tracked') => void; filterValues?: FilterValues; sortValue?: string }

export default function AppTSPage({ apiBase, refreshToken, activeTab = 'bookmarked', onTabChange = () => undefined, filterValues = {}, sortValue = 'newest' }: Props) {
  const [bookmarked, setBookmarked] = useState<Candidate[]>([])
  const [bookmarkedRefresh, setBookmarkedRefresh] = useState(0)
  const [bookmarkedDraftEdits, setBookmarkedDraftEdits] = useState<Record<number, string>>({})
  const [bookmarkedParserExpanded, setBookmarkedParserExpanded] = useState<Record<number, boolean>>({})
  const [bookmarkedSendingId, setBookmarkedSendingId] = useState<number | null>(null)
  const [bookmarkedRegeneratingId, setBookmarkedRegeneratingId] = useState<number | null>(null)
  const [bookmarkedRejectingId, setBookmarkedRejectingId] = useState<number | null>(null)
  const [bookmarkedMovingToFailedId, setBookmarkedMovingToFailedId] = useState<number | null>(null)
  const [bookmarkedSentDetailsExpanded, setBookmarkedSentDetailsExpanded] = useState<Record<number, boolean>>({})
  const [bookmarkedSentDetailsLoading, setBookmarkedSentDetailsLoading] = useState<Record<number, boolean>>({})
  const [bookmarkedSentDetailsError, setBookmarkedSentDetailsError] = useState<Record<number, string | undefined>>({})
  const [bookmarkedSentDetails, setBookmarkedSentDetails] = useState<Record<number, SentItemDetails>>({})
  const [rows, setRows] = useState<ApplicationCard[]>([])
  const [total, setTotal] = useState(0)
  const [watchBudget, setWatchBudget] = useState({ count: 0, limit: 0, reached: false })
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<'all' | ApplicationStatus>('all')
  // Starts true: a fetch is always scheduled on mount, so the first paint
  // must not claim the list is empty before anything has been requested.
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [details, setDetails] = useState<Record<number, ApplicationCard>>({})
  const [edits, setEdits] = useState<Record<number, Partial<ApplicationEdit>>>({})
  const [duplicateConflicts, setDuplicateConflicts] = useState<Record<number, ApplicationDuplicateSummary[]>>({})
  const [trackedSentDetailsExpanded, setTrackedSentDetailsExpanded] = useState<Record<number, boolean>>({})
  const [trackedSentDetailsLoading, setTrackedSentDetailsLoading] = useState<Record<number, boolean>>({})
  const [trackedSentDetailsError, setTrackedSentDetailsError] = useState<Record<number, string | undefined>>({})
  const [trackedSentDetails, setTrackedSentDetails] = useState<Record<number, SentItemDetails>>({})
  const [error, setError] = useState('')
  const requestIdRef = useRef(0)

  useEffect(() => { setPage(1) }, [filterValues, sortValue, status, activeTab])

  useEffect(() => {
    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    setLoading(true)
    setError('')
    const timer = window.setTimeout(() => {
      let request: Promise<void>
      if (activeTab === 'bookmarked') {
        const bookmarkedParams = resolveRegistryEntry(filterSortRegistry['application_tracking:bookmarked'], { resumeAssets: [] })?.toParams(filterValues) ?? {}
        request = listBookmarkedRequirements(apiBase, bookmarkedParams, sortValue).then((items) => { setBookmarked(items) })
      } else {
        const filters = resolveRegistryEntry(filterSortRegistry['application_tracking:tracked'], { resumeAssets: [] })?.toParams(filterValues) ?? {}
        request = listApplicationPage({ apiBase, cursor: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE, q: '', status, filters, sort: sortValue }).then((result) => {
          setRows(result.items)
          setTotal(result.total)
          setWatchBudget({ count: result.watch_count ?? 0, limit: result.watch_limit ?? 0, reached: result.watch_limit_reached ?? false })
        })
      }
      request
        .catch((reason) => { if (requestId === requestIdRef.current) setError(reason instanceof Error ? reason.message : String(reason)) })
        .finally(() => { if (requestId === requestIdRef.current) setLoading(false) })
    }, 150)
    return () => window.clearTimeout(timer)
  }, [activeTab, apiBase, bookmarkedRefresh, filterValues, page, refreshToken, sortValue, status])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const replaceRow = (updated: ApplicationCard) => {
    setRows((current) => current.map((row) => row.id === updated.id ? updated : row))
    setDetails((current) => current[updated.id] ? { ...current, [updated.id]: updated } : current)
  }

  const saveDetailMutation = async (id: number, action: () => Promise<ApplicationCard>): Promise<ApplicationCard | null> => {
    setBusyId(id)
    setError('')
    try {
      const updated = await action()
      replaceRow(updated)
      setDetails((current) => ({ ...current, [id]: updated }))
      return updated
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      return null
    } finally {
      setBusyId(null)
    }
  }

  const patchRow = (id: number, patch: Partial<Pick<ApplicationCard, 'status' | 'next_action_type' | 'next_action_at' | 'closed_reason' | 'closed_reason_code'>>) =>
    saveDetailMutation(id, () => updateApplication(apiBase, id, patch)).then((updated) => {
      if (updated) setEdits((current) => { const next = { ...current }; delete next[id]; return next })
    })

  const saveNextAction = (item: ApplicationCard) => {
    const edit = edits[item.id] ?? {}
    const nextActionType = edit.next_action_type ?? item.next_action_type ?? ''
    const nextActionAt = edit.next_action_at ?? localDateTimeValue(item.next_action_at)
    patchRow(item.id, {
      next_action_type: nextActionType.trim() || null,
      next_action_at: nextActionAt ? new Date(nextActionAt).toISOString() : null,
      closed_reason: (edit.closed_reason ?? item.closed_reason ?? '').trim() || null,
      ...(CLOSED_STATUSES.includes(item.status) ? { closed_reason_code: (edit.closed_reason_code ?? item.closed_reason_code ?? '').trim() || null } : {}),
    }).catch(() => undefined)
  }

  const toggleDetails = async (id: number) => {
    if (expandedId === id) { setExpandedId(null); return }
    setExpandedId(id)
    if (details[id]) return
    setBusyId(id)
    try {
      const detail = await getApplication(apiBase, id)
      setDetails((current) => ({ ...current, [id]: detail }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusyId(null)
    }
  }

  const approveBookmarked = async (item: Candidate) => {
    setBookmarkedSendingId(item.id)
    setError('')
    try {
      await approveSendCandidate(apiBase, item.id, bookmarkedDraftEdits[item.id] ?? item.draft_reply)
      setBookmarkedRefresh((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBookmarkedSendingId(null)
    }
  }

  const regenerateBookmarked = async (id: number) => {
    setBookmarkedRegeneratingId(id)
    setError('')
    try {
      let updated
      try {
        updated = await regenerateCandidateDraft(apiBase, id)
      } catch (reason) {
        if (!(reason instanceof RoleManifestForkRequiredError)) throw reason
        if (!window.confirm(`This requirement contains ${reason.requirementCount} roles and regeneration will create ${reason.requirementCount} separate candidate cards. Continue?`)) return
        updated = await regenerateCandidateDraft(apiBase, id, true)
      }
      setBookmarkedDraftEdits((prev) => ({ ...prev, [id]: updated.draft_reply ?? '' }))
      setBookmarked((current) => current.map((row) => row.id === id ? updated : row))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBookmarkedRegeneratingId(null)
    }
  }

  const retryDetectionBookmarked = async (id: number) => {
    setBookmarkedRegeneratingId(id)
    setError('')
    try {
      await retryRoleDetectionForCandidate(apiBase, id)
      setBookmarkedRefresh((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBookmarkedRegeneratingId(null)
    }
  }

  const rejectBookmarked = async (id: number) => {
    setBookmarkedRejectingId(id)
    setError('')
    try {
      await rejectCandidate(apiBase, id)
      setBookmarkedRefresh((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBookmarkedRejectingId(null)
    }
  }

  const sendToFailedMappingBookmarked = async (id: number) => {
    setBookmarkedMovingToFailedId(id)
    setError('')
    try {
      await sendCandidateToFailedMapping(apiBase, id)
      setBookmarkedRefresh((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBookmarkedMovingToFailedId(null)
    }
  }

  const toggleTrackingBookmarked = async (id: number) => {
    try {
      await toggleCandidateTracking(apiBase, id)
      setBookmarkedRefresh((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const toggleSentDetailsBookmarked = async (id: number) => {
    if (bookmarkedSentDetailsExpanded[id]) {
      setBookmarkedSentDetailsExpanded((prev) => ({ ...prev, [id]: false }))
      return
    }
    setBookmarkedSentDetailsExpanded((prev) => ({ ...prev, [id]: true }))
    if (bookmarkedSentDetails[id] || bookmarkedSentDetailsLoading[id]) return
    setBookmarkedSentDetailsLoading((prev) => ({ ...prev, [id]: true }))
    setBookmarkedSentDetailsError((prev) => ({ ...prev, [id]: undefined }))
    try {
      const payload = await fetchCandidateSentDetails(apiBase, id)
      setBookmarkedSentDetails((prev) => ({ ...prev, [id]: payload }))
    } catch (reason) {
      setBookmarkedSentDetailsError((prev) => ({ ...prev, [id]: reason instanceof Error ? reason.message : String(reason) }))
    } finally {
      setBookmarkedSentDetailsLoading((prev) => ({ ...prev, [id]: false }))
    }
  }

  const toggleTrackedSentDetails = async (id: number) => {
    if (trackedSentDetailsExpanded[id]) {
      setTrackedSentDetailsExpanded((prev) => ({ ...prev, [id]: false }))
      return
    }
    setTrackedSentDetailsExpanded((prev) => ({ ...prev, [id]: true }))
    if (trackedSentDetails[id] || trackedSentDetailsLoading[id]) return
    setTrackedSentDetailsLoading((prev) => ({ ...prev, [id]: true }))
    setTrackedSentDetailsError((prev) => ({ ...prev, [id]: undefined }))
    try {
      const payload = await fetchApplicationSentDetails(apiBase, id)
      setTrackedSentDetails((prev) => ({ ...prev, [id]: payload }))
    } catch (reason) {
      setTrackedSentDetailsError((prev) => ({ ...prev, [id]: reason instanceof Error ? reason.message : String(reason) }))
    } finally {
      setTrackedSentDetailsLoading((prev) => ({ ...prev, [id]: false }))
    }
  }

  return (
    <section className="card pageSection">
      <header><h2>Application Tracking System</h2><p className="subtle">Explicitly bookmarked and tracked applications.</p></header>
      <div className="premiumTabs" role="tablist" aria-label="Application tracking views">
        <button type="button" role="tab" aria-selected={activeTab === 'bookmarked'} className={activeTab === 'bookmarked' ? 'active' : ''} onClick={() => onTabChange('bookmarked')}>Bookmarked Requirements</button>
        <button type="button" role="tab" aria-selected={activeTab === 'tracked'} className={activeTab === 'tracked' ? 'active' : ''} onClick={() => onTabChange('tracked')}>Tracked and Applied</button>
      </div>
      {error ? <p className="errorBanner">{error}</p> : null}
      {loading ? <p className="subtle">Loading...</p> : null}

      {activeTab === 'bookmarked' ? (
        <div>
          {!loading && bookmarked.length === 0 ? <p className="inventoryEmpty">No bookmarked requirements match these filters.</p> : null}
          {bookmarked.map((item) => (
            <CandidateCard
              key={item.id}
              item={item}
              draftValue={bookmarkedDraftEdits[item.id] ?? item.draft_reply}
              onDraftChange={(value) => setBookmarkedDraftEdits((prev) => ({ ...prev, [item.id]: value }))}
              draftTextSize={undefined}
              enabledAttachmentNames={[]}
              parserExpanded={Boolean(bookmarkedParserExpanded[item.id])}
              onToggleParserExpanded={() => setBookmarkedParserExpanded((prev) => ({ ...prev, [item.id]: !prev[item.id] }))}
              isSending={bookmarkedSendingId === item.id}
              onApprove={approveBookmarked}
              isRegenerating={bookmarkedRegeneratingId === item.id}
              onRegenerate={regenerateBookmarked}
              onRetryDetection={retryDetectionBookmarked}
              isRejecting={bookmarkedRejectingId === item.id}
              onReject={rejectBookmarked}
              isMovingToFailedMapping={bookmarkedMovingToFailedId === item.id}
              onSendToFailedMapping={sendToFailedMappingBookmarked}
              onToggleTracking={toggleTrackingBookmarked}
              sentDetailsExpanded={Boolean(bookmarkedSentDetailsExpanded[item.id])}
              onToggleSentDetails={toggleSentDetailsBookmarked}
              sentDetailsLoading={Boolean(bookmarkedSentDetailsLoading[item.id])}
              sentDetailsError={bookmarkedSentDetailsError[item.id]}
              sentDetails={bookmarkedSentDetails[item.id]}
            />
          ))}
        </div>
      ) : (
        <>
          <div className="inventoryToolbar">
            <label><span>Status</span>
              <select value={status} onChange={(event) => setStatus(event.target.value as 'all' | ApplicationStatus)}>
                <option value="all">All statuses</option>
                {STATUSES.map((value) => <option key={value} value={value}>{label(value)}</option>)}
              </select>
            </label>
          </div>
          {watchBudget.limit > 0 ? <p role="status" className="subtle">Recruiter watch budget: {watchBudget.count} of {watchBudget.limit} used.{watchBudget.reached ? ' Limit reached.' : ''}</p> : null}
          {!loading && rows.length === 0 ? <p className="inventoryEmpty">No tracked applications match these filters.</p> : null}
          <div className="opportunityGrid">
            {rows.map((item) => {
              const detail = details[item.id]
              const conflicts = duplicateConflicts[item.id] ?? []
              const currentTitle = item.current_job_title || item.job_title_snapshot
              const currentCompany = item.current_recruiter_company || item.recruiter_company_snapshot
              const sourceChanged = currentTitle !== item.job_title_snapshot || currentCompany !== item.recruiter_company_snapshot
              const atsStrength = getAtsStrengthLabel(item.ats_score)
              const atsTone = atsStrength === 'Strong' ? 'active' : atsStrength === 'Moderate' ? 'pending' : 'flagged'
              return (
                <article key={item.id} className="opportunityCard applicationCard" data-application-id={item.id}>
                  <div className="candidateCardTop">
                    <div className="candidateCardHeaderMain">
                      <span className="categoryChip">{item.resume_file_name_snapshot} · v{item.resume_version_snapshot}</span>
                      <h3 className="candidateCardTitle">{currentTitle || 'Tracked application'}</h3>
                      <p className="candidateCardSubtitle">
                        {item.location_snapshot || '-'}
                        {' · '}{item.resume_skills_snapshot.length ? item.resume_skills_snapshot.slice(0, 3).join(', ') : '-'}
                      </p>
                      <p className="candidateCardMeta"><strong>Applied:</strong> {dateTimeLabel(item.created_at)}</p>
                      {item.current_source_url ? (
                        <p className="candidateCardMeta candidateCardLinks">
                          <a href={item.current_source_url} target="_blank" rel="noreferrer">Open source listing</a>
                        </p>
                      ) : null}
                      {sourceChanged ? <p className="snapshotNotice">Source details changed; the original resume/recruiter/job snapshot is preserved.</p> : null}
                      <p className="candidateCardRecordId">Record ID: {item.record_id ?? '-'}</p>
                    </div>
                    <div className="applicationCardTopRight">
                      <div className="candidateCardBadges" aria-label="Application status badges">
                        {item.ats_score != null ? (
                          <span className={`statusBadge statusBadge--lg statusBadge--${atsTone}`}>
                            ATS {atsStrength} · {formatAtsScore(item.ats_score)}
                          </span>
                        ) : null}
                        <span className="statusBadge statusBadge--neutral" title="Current status (read-only)">{label(item.status)}</span>
                        {item.current_recruiter_categories.map((category) => <CategoryChip key={category} category={category} />)}
                        {item.current_recruiter_status ? <StatusBadge status={item.current_recruiter_status as 'Active' | 'Flagged'} /> : null}
                        {item.current_recruiter_verification_level ? <VerificationBadge level={item.current_recruiter_verification_level} /> : null}
                      </div>
                      <select aria-label={`Application status for ${currentTitle}`} value={item.status} onChange={(event) => patchRow(item.id, { status: event.target.value as ApplicationStatus })} disabled={busyId === item.id}>
                        {item.status === 'submitted_to_client' ? <option value="submitted_to_client" disabled>Submitted To Client</option> : null}
                        {EDITABLE_STATUSES.map((value) => <option key={value} value={value}>{label(value)}</option>)}
                      </select>
                    </div>
                  </div>
                  <div className="detailFormGrid">
                    <label>Next action<input value={edits[item.id]?.next_action_type ?? item.next_action_type ?? ''} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], next_action_type: event.target.value } }))} /></label>
                    <label>Due at<input type="datetime-local" value={edits[item.id]?.next_action_at ?? localDateTimeValue(item.next_action_at)} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], next_action_at: event.target.value } }))} /></label>
                    <label>Closed reason<input value={edits[item.id]?.closed_reason ?? item.closed_reason ?? ''} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], closed_reason: event.target.value } }))} /></label>
                    <label>Closed reason code
                      <select value={edits[item.id]?.closed_reason_code ?? item.closed_reason_code ?? ''} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], closed_reason_code: event.target.value } }))} disabled={!CLOSED_STATUSES.includes(item.status)}>
                        <option value="">Select reason</option>
                        {CLOSED_REASON_CODES.map((value) => <option key={value} value={value}>{label(value)}</option>)}
                      </select>
                    </label>
                  </div>
                  <div className="rowBtns">
                    <button type="button" onClick={() => saveNextAction(item)} disabled={busyId === item.id || !edits[item.id]}>Save reminder</button>
                    <button type="button" onClick={() => toggleDetails(item.id)} disabled={busyId === item.id}>{expandedId === item.id ? 'Hide timeline' : 'View timeline'}</button>
                    <button type="button" onClick={() => toggleTrackedSentDetails(item.id)} disabled={!item.source_recruiter_email_id} title={item.source_recruiter_email_id ? undefined : 'No linked sourcing email for this application'}>
                      {trackedSentDetailsExpanded[item.id] ? 'Hide Details' : 'View Details'}
                    </button>
                    {item.sent_gmail_message_link ? (
                      <a className="linkButton" href={item.sent_gmail_message_link} target="_blank" rel="noreferrer">Message</a>
                    ) : (
                      <button type="button" disabled title="No sent Gmail message linked to this application">Message</button>
                    )}
                  </div>
                  {trackedSentDetailsExpanded[item.id] ? (
                    <div className="parserDetailsPanel">
                      {trackedSentDetailsLoading[item.id] ? <p className="subtle">Loading contact details...</p> : null}
                      {trackedSentDetailsError[item.id] ? <p className="errorMessage">{trackedSentDetailsError[item.id]}</p> : null}
                      {trackedSentDetails[item.id] ? renderContactDetailsGrid(trackedSentDetails[item.id], {
                        id: item.source_recruiter_email_id ?? item.id,
                        role: currentTitle,
                        location: item.location_snapshot,
                        skills_text: item.resume_skills_snapshot.join(', '),
                        resume_file_name: item.resume_file_name_snapshot,
                        ats_score: item.ats_score,
                        ats_summary: item.ats_summary,
                      }) : null}
                    </div>
                  ) : null}
                  {expandedId === item.id ? (
                    <section className="applicationTimeline" aria-label={`Timeline for ${currentTitle}`}>
                      {!detail ? <p className="subtle">Loading timeline...</p> : (
                        <>
                          <JourneyColumn
                            application={detail}
                            apiBase={apiBase}
                            duplicateConflicts={conflicts}
                            onUpdated={replaceRow}
                            onDuplicateConflicts={(next) => setDuplicateConflicts((current) => ({ ...current, [item.id]: next }))}
                            onError={setError}
                          />
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
              <span>Showing {(page - 1) * PAGE_SIZE + 1} to {Math.min(page * PAGE_SIZE, total)} of {total} entries</span>
              <nav className="pagination" aria-label="Application pages">
                <button type="button" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={page === 1}>‹</button>
                <span>Page {page} of {totalPages}</span>
                <button type="button" onClick={() => setPage((value) => Math.min(totalPages, value + 1))} disabled={page === totalPages}>›</button>
              </nav>
            </footer>
          ) : null}
        </>
      )}
    </section>
  )
}
