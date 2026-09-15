import { useRef, useState, type FormEvent, type KeyboardEvent } from 'react'

import type { ApplicationCard, ApplicationDuplicateSummary, ApplicationInterview, ApplicationStatus } from '../premium_numbers/types'
import {
  ApplicationDuplicateConflictError,
  RoleManifestForkRequiredError,
  addApplicationInterview,
  createApplicationEvent,
  getApplication,
  requestApplicationRtr,
  submitApplicationToClient,
  updateApplication,
  updateApplicationInterview,
  updateApplicationRtr,
} from './api'
import type { JourneyAction, JourneyNode } from './journey'

const CLOSED_STATUSES: ApplicationStatus[] = ['hired', 'rejected', 'withdrawn', 'no_response', 'position_closed', 'duplicate']
const CLOSED_REASON_CODES = ['rate_mismatch', 'skills_gap', 'client_freeze', 'position_filled', 'candidate_declined', 'recruiter_unresponsive', 'other']
const INTERVIEW_ROUNDS: ApplicationInterview['round_type'][] = ['recruiter_screen', 'interview_1', 'interview_2', 'final_interview', 'other']
const INTERVIEW_RESULTS: ApplicationInterview['result'][] = ['scheduled', 'completed', 'passed', 'failed', 'cancelled', 'rescheduled']

const label = (value: string) => value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
const iso = (value: string) => value ? new Date(value).toISOString() : null

type Props = {
  application: ApplicationCard
  action?: JourneyAction
  node?: JourneyNode
  duplicateConflicts: ApplicationDuplicateSummary[]
  apiBase: string
  onUpdated: (application: ApplicationCard) => void
  onDuplicateConflicts: (conflicts: ApplicationDuplicateSummary[]) => void
  onError: (message: string) => void
  onClose: () => void
}

export default function JourneyPanel({ application, action, node, duplicateConflicts, apiBase, onUpdated, onDuplicateConflicts, onError, onClose }: Props) {
  const panelRef = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(false)
  const [panelError, setPanelError] = useState('')
  const [note, setNote] = useState('')
  const [emailId, setEmailId] = useState('')
  const [roleScope, setRoleScope] = useState(application.current_job_title || application.job_title_snapshot)
  const [endClientScope, setEndClientScope] = useState(application.current_end_client || application.end_client_snapshot)
  const [expiresAt, setExpiresAt] = useState('')
  const [proofAttachmentId, setProofAttachmentId] = useState('')
  const [proofEmailId, setProofEmailId] = useState('')
  const [roundType, setRoundType] = useState<ApplicationInterview['round_type']>('interview_1')
  const [scheduledAt, setScheduledAt] = useState('')
  const [format, setFormat] = useState('')
  const [interviewers, setInterviewers] = useState('')
  const [syncStatus, setSyncStatus] = useState(true)
  const interviews = application.interviews
  const [interviewId, setInterviewId] = useState(String(interviews.at(-1)?.id ?? ''))
  const [result, setResult] = useState<ApplicationInterview['result']>(interviews.at(-1)?.result ?? 'completed')
  const [feedback, setFeedback] = useState(interviews.at(-1)?.feedback ?? '')
  const [followUpNote, setFollowUpNote] = useState(interviews.at(-1)?.follow_up_task_note ?? '')
  const [nextActionAt, setNextActionAt] = useState('')
  const [closeStatus, setCloseStatus] = useState<ApplicationStatus>('rejected')
  const [closedReason, setClosedReason] = useState('')
  const [closedReasonCode, setClosedReasonCode] = useState('')

  const fail = (reason: unknown) => {
    const message = reason instanceof RoleManifestForkRequiredError
      ? `This source contains ${reason.requirementCount} roles and must be split before this action can continue.`
      : reason instanceof Error ? reason.message : String(reason)
    setPanelError(message)
    onError(message)
  }

  const finish = (updated: ApplicationCard) => {
    onDuplicateConflicts([])
    onUpdated(updated)
    onClose()
  }

  const submitToClient = async (override = false) => {
    setBusy(true)
    setPanelError('')
    try {
      finish(await submitApplicationToClient(apiBase, application.id, override))
    } catch (reason) {
      if (reason instanceof ApplicationDuplicateConflictError) {
        onDuplicateConflicts(reason.duplicates)
        setPanelError(reason.message)
      } else fail(reason)
    } finally {
      setBusy(false)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!action) return
    setBusy(true)
    setPanelError('')
    try {
      let updated: ApplicationCard
      switch (action.kind) {
        case 'add_note':
        case 'add_call_note':
        case 'link_email':
          await createApplicationEvent(apiBase, application.id, {
            event_type: action.kind === 'add_note' ? 'note' : action.kind === 'add_call_note' ? 'call_note' : 'email_linked',
            note,
            ...(action.kind === 'link_email' ? { linked_recruiter_email_id: Number(emailId) } : {}),
          })
          updated = await getApplication(apiBase, application.id)
          break
        case 'request_rtr':
          updated = await requestApplicationRtr(apiBase, application.id, { role_scope: roleScope, end_client_scope: endClientScope, expires_at: iso(expiresAt) })
          break
        case 'confirm_rtr':
        case 'expire_rtr':
        case 'revoke_rtr': {
          const rtr = application.rtr_history.find((candidate) => action.kind === 'confirm_rtr' ? candidate.status === 'requested' : ['requested', 'confirmed'].includes(candidate.status))
          if (!rtr) throw new Error('No active RTR is available for this action.')
          updated = await updateApplicationRtr(apiBase, application.id, rtr.id, action.kind === 'confirm_rtr'
            ? { status: 'confirmed', ...(proofAttachmentId ? { proof_attachment_id: Number(proofAttachmentId) } : {}), ...(proofEmailId ? { proof_recruiter_email_id: Number(proofEmailId) } : {}) }
            : { status: action.kind === 'expire_rtr' ? 'expired' : 'revoked' })
          break
        }
        case 'add_interview':
          updated = await addApplicationInterview(apiBase, application.id, { round_type: roundType, scheduled_at: iso(scheduledAt), format, interviewer_names: interviewers, sync_application_status: syncStatus })
          break
        case 'record_result': {
          const selected = interviews.find((candidate) => candidate.id === Number(interviewId))
          if (!selected) throw new Error('Choose an interview round.')
          updated = await updateApplicationInterview(apiBase, application.id, selected.id, { result, feedback, follow_up_task_note: followUpNote })
          break
        }
        case 'recruiter_responded':
          updated = await updateApplication(apiBase, application.id, { status: 'recruiter_responded' })
          break
        case 'mark_no_response':
          updated = await updateApplication(apiBase, application.id, { status: 'no_response' })
          break
        case 'mark_hired':
          updated = await updateApplication(apiBase, application.id, { status: 'hired' })
          break
        case 'close':
          updated = await updateApplication(apiBase, application.id, { status: closeStatus, closed_reason: closedReason.trim() || null, closed_reason_code: closedReasonCode || null })
          break
        case 'follow_up':
          await createApplicationEvent(apiBase, application.id, { event_type: 'note', note })
          updated = await updateApplication(apiBase, application.id, { next_action_type: 'follow_up', next_action_at: iso(nextActionAt) })
          break
        case 'submit_to_client':
          await submitToClient()
          return
      }
      finish(updated)
    } catch (reason) {
      fail(reason)
    } finally {
      setBusy(false)
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
      return
    }
    if (event.key !== 'Tab' || !panelRef.current) return
    const focusable = Array.from(panelRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]'))
    if (!focusable.length) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }

  const actionFields = () => {
    if (!action) return null
    switch (action.kind) {
      case 'add_note':
      case 'add_call_note':
        return <label>Note<textarea required rows={4} value={note} onChange={(event) => setNote(event.target.value)} /></label>
      case 'link_email':
        return <><label>Recruiter email ID<input required type="number" min={1} value={emailId} onChange={(event) => setEmailId(event.target.value)} /></label><label>Note<textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} /></label></>
      case 'request_rtr':
        return <><label>Role scope<input required value={roleScope} onChange={(event) => setRoleScope(event.target.value)} /></label><label>End client scope<input required value={endClientScope} onChange={(event) => setEndClientScope(event.target.value)} /></label><label>Expiry<input type="datetime-local" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} /></label></>
      case 'confirm_rtr':
        return <><label>Attachment ID proof<input type="number" min={1} value={proofAttachmentId} onChange={(event) => setProofAttachmentId(event.target.value)} /></label><label>Recruiter email ID proof<input type="number" min={1} value={proofEmailId} onChange={(event) => setProofEmailId(event.target.value)} /></label></>
      case 'add_interview':
        return <><label>Round<select value={roundType} onChange={(event) => setRoundType(event.target.value as ApplicationInterview['round_type'])}>{INTERVIEW_ROUNDS.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></label><label>Scheduled at<input type="datetime-local" value={scheduledAt} onChange={(event) => setScheduledAt(event.target.value)} /></label><label>Format<input value={format} onChange={(event) => setFormat(event.target.value)} /></label><label>Interviewers<input value={interviewers} onChange={(event) => setInterviewers(event.target.value)} /></label><label className="checkboxLabel"><input type="checkbox" checked={syncStatus} onChange={(event) => setSyncStatus(event.target.checked)} />Move application to this round</label></>
      case 'record_result':
        return <><label>Interview<select required value={interviewId} onChange={(event) => { const id = Number(event.target.value); const selected = interviews.find((candidate) => candidate.id === id); setInterviewId(event.target.value); if (selected) { setResult(selected.result); setFeedback(selected.feedback); setFollowUpNote(selected.follow_up_task_note) } }}><option value="">Choose a round</option>{interviews.map((interview) => <option key={interview.id} value={interview.id}>{label(interview.round_type)}</option>)}</select></label><label>Result<select value={result} onChange={(event) => setResult(event.target.value as ApplicationInterview['result'])}>{INTERVIEW_RESULTS.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></label><label>Feedback<textarea rows={3} value={feedback} onChange={(event) => setFeedback(event.target.value)} /></label><label>Follow-up note<textarea rows={2} value={followUpNote} onChange={(event) => setFollowUpNote(event.target.value)} /></label></>
      case 'follow_up':
        return <><label>Follow-up note<textarea required rows={4} value={note} onChange={(event) => setNote(event.target.value)} /></label><label>Due at<input required type="datetime-local" value={nextActionAt} onChange={(event) => setNextActionAt(event.target.value)} /></label></>
      case 'close':
        return <><label>Closed status<select value={closeStatus} onChange={(event) => setCloseStatus(event.target.value as ApplicationStatus)}>{CLOSED_STATUSES.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></label><label>Reason code<select value={closedReasonCode} onChange={(event) => setClosedReasonCode(event.target.value)}><option value="">Select reason</option>{CLOSED_REASON_CODES.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></label><label>Reason<textarea rows={3} value={closedReason} onChange={(event) => setClosedReason(event.target.value)} /></label></>
      default:
        return null
    }
  }

  return (
    <div className="detailPanelOverlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div ref={panelRef} className="detailPanel journeyPanel" role="dialog" aria-modal="true" aria-label={action?.label ?? node?.title ?? 'Journey details'} tabIndex={-1} onKeyDown={handleKeyDown}>
        <header className="detailPanelHeader">
          <div><p className="detailPanelEyebrow">Application journey</p><h3>{action?.label ?? node?.title}</h3></div>
          <button type="button" className="iconBtn" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <div className="detailPanelBody">
          {node ? (
            <dl className="detailList">
              <div><dt>When</dt><dd>{node.at ? new Date(node.at).toLocaleString() : 'Unscheduled'}</dd></div>
              {Object.entries(node.detail).map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{key.endsWith('_at') || key === 'first_reached' ? new Date(value).toLocaleString() : value || '--'}</dd></div>)}
              {!application.is_manual_entry && application.sent_gmail_message_link ? <div><dt>Gmail</dt><dd><a href={application.sent_gmail_message_link} target="_blank" rel="noreferrer">Open Gmail message</a></dd></div> : null}
            </dl>
          ) : (
            <form className="detailFormGrid" onSubmit={submit}>
              {actionFields()}
              {panelError ? <p className="errorMessage detailFormGridFullRow" role="alert">{panelError}</p> : null}
              {duplicateConflicts.length ? <div className="duplicateConflict detailFormGridFullRow" role="alert"><p><strong>Possible duplicate submission</strong></p><ul>{duplicateConflicts.map((conflict) => <li key={conflict.id}>#{conflict.id} - {conflict.job_title_snapshot || '--'} - {conflict.end_client_snapshot || '--'} - {label(conflict.status)}</li>)}</ul><button type="button" onClick={() => submitToClient(true)} disabled={busy}>Submit anyway</button></div> : null}
              <div className="rowBtns detailFormGridFullRow"><button type="submit" className="primaryButton" disabled={busy}>{busy ? 'Saving...' : action?.label}</button><button type="button" onClick={onClose} disabled={busy}>Cancel</button></div>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
