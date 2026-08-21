import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react'

import { deleteContactVersion, listContactVersions, selectContactVersion, updateEmployerNumber, updateRecruiterNumber } from './api'
import { CategoryChip, StatusBadge } from './StatusBadge'
import type { EmployerNumberCard, InventoryAction, InventoryRow, PremiumNumberVersion, RecruiterNumberCard, ReviewEdits } from './types'

type RecruiterEdits = Partial<Pick<RecruiterNumberCard, 'recruiter_name' | 'company' | 'designation' | 'recruiter_email' | 'linkedin_url'>>
type EmployerEdits = Partial<Pick<EmployerNumberCard, 'owner_name' | 'company'>>

type DetailPanelProps = {
  apiBase: string
  row: InventoryRow
  busy: boolean
  returnFocusRef: RefObject<HTMLElement | null>
  onClose: () => void
  onAction: (row: InventoryRow, action: InventoryAction, edits?: ReviewEdits) => Promise<void>
  onReload: () => Promise<void>
  onError: (message: string) => void
  onToast: (message: string) => void
}

export default function DetailPanel({
  apiBase,
  row,
  busy,
  returnFocusRef,
  onClose,
  onAction,
  onReload,
  onError,
  onToast,
}: DetailPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const primaryRole = row.recruiter ? 'recruiter' : 'employer'
  const primaryContact = row.recruiter ?? row.employer
  const [reviewEdits, setReviewEdits] = useState<ReviewEdits>({})
  const [versions, setVersions] = useState<PremiumNumberVersion[]>([])
  const [versionsLoading, setVersionsLoading] = useState(Boolean(primaryContact))
  const [editingRecruiter, setEditingRecruiter] = useState(false)
  const [recruiterEdits, setRecruiterEdits] = useState<RecruiterEdits>({})
  const [savingRecruiter, setSavingRecruiter] = useState(false)
  const [editingEmployer, setEditingEmployer] = useState(false)
  const [employerEdits, setEmployerEdits] = useState<EmployerEdits>({})
  const [savingEmployer, setSavingEmployer] = useState(false)
  const [pendingAction, setPendingAction] = useState<InventoryAction | null>(null)
  const [deletingVersion, setDeletingVersion] = useState(false)
  const [syncingVersion, setSyncingVersion] = useState(false)

  useEffect(() => {
    const panel = panelRef.current
    const returnFocus = returnFocusRef.current
    panel?.focus()
    return () => returnFocus?.focus()
  }, [returnFocusRef])

  useEffect(() => {
    if (!primaryContact) return
    listContactVersions(apiBase, primaryRole, row.id)
      .then(setVersions)
      .catch((reason) => onError((reason as Error).message))
      .finally(() => setVersionsLoading(false))
  }, [apiBase, onError, primaryContact, primaryRole, row.id, row.key, row.recruiter?.linkedin_url])

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
      return
    }
    if (event.key !== 'Tab' || !panelRef.current) return
    const focusable = Array.from(panelRef.current.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]',
    ))
    if (focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  const closeEditors = () => {
    setEditingRecruiter(false)
    setEditingEmployer(false)
  }

  const act = (action: InventoryAction, edits?: ReviewEdits) => {
    setPendingAction(action)
    onAction(row, action, edits)
      .catch((reason) => onError((reason as Error).message))
      .finally(() => setPendingAction(null))
  }

  const review = row.review
  const recruiter = row.recruiter
  const employer = row.employer

  const activeVersion = versions.find((version) => version.id === primaryContact?.active_lead_id) ?? null
  const profileOutOfSync = Boolean(
    activeVersion &&
      (primaryRole === 'recruiter'
        ? recruiter &&
          (activeVersion.owner_name !== recruiter.recruiter_name ||
            activeVersion.company !== recruiter.company ||
            (activeVersion.contact_email || '') !== (recruiter.recruiter_email || '') ||
            (activeVersion.linkedin_url || '') !== (recruiter.linkedin_url || ''))
        : employer &&
          (activeVersion.owner_name !== employer.owner_name || activeVersion.company !== employer.company)),
  )

  return (
    <div className="detailPanelOverlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div
        ref={panelRef}
        className="detailPanel"
        role="dialog"
        aria-modal="true"
        aria-label={`Premium number details for ${row.number}`}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <header className="detailPanelHeader">
          <div>
            <p className="detailPanelEyebrow">{row.kind === 'review' ? 'Pending review' : 'Number contact'}</p>
            <h3>{row.number}</h3>
            <div className="categoryChips">
              {row.categories.map((category) => <CategoryChip key={category} category={category} />)}
              <StatusBadge status={row.status} />
            </div>
          </div>
          <button type="button" className="iconBtn" aria-label="Close details" onClick={onClose}>×</button>
        </header>

        <div className="detailPanelBody">
          {review ? (
            <>
              <section className="detailSection">
                <h4>Contact details</h4>
                <div className="detailFormGrid">
                  <label>Phone<input value={reviewEdits.display_phone_number ?? review.display_phone_number} onChange={(event) => setReviewEdits((value) => ({ ...value, display_phone_number: event.target.value }))} /></label>
                  <label>Owner<input value={reviewEdits.owner_name ?? review.owner_name} onChange={(event) => setReviewEdits((value) => ({ ...value, owner_name: event.target.value }))} /></label>
                  <label>Company<input value={reviewEdits.company ?? review.company} onChange={(event) => setReviewEdits((value) => ({ ...value, company: event.target.value }))} /></label>
                  <label>Designation<input value={reviewEdits.designation ?? review.designation} onChange={(event) => setReviewEdits((value) => ({ ...value, designation: event.target.value }))} /></label>
                  <label>Contact email<input type="email" value={reviewEdits.contact_email ?? review.contact_email} onChange={(event) => setReviewEdits((value) => ({ ...value, contact_email: event.target.value }))} /></label>
                  <label>LinkedIn profile<input value={reviewEdits.linkedin_url ?? review.linkedin_url ?? ''} onChange={(event) => setReviewEdits((value) => ({ ...value, linkedin_url: event.target.value }))} /></label>
                </div>
              </section>
              <section className="detailSection">
                <h4>Extraction evidence</h4>
                <dl className="detailList">
                  <div><dt>Confidence</dt><dd>{review.confidence.toUpperCase()}</dd></div>
                  <div><dt>Purpose</dt><dd>{review.purpose || '--'}</dd></div>
                  <div><dt>Contact type</dt><dd>{review.contact_type || '--'}</dd></div>
                  <div><dt>Relevance score</dt><dd>{review.recruiter_relevance_score}/100</dd></div>
                  <div><dt>Signal</dt><dd>{review.relevance_reason || '--'}</dd></div>
                  <div><dt>Extraction source</dt><dd>{review.extraction_source}</dd></div>
                  <div><dt>Score basis</dt><dd>{review.scored_with === 'legacy' ? 'Legacy (rescore recommended)' : 'Current'}</dd></div>
                  <div><dt>Email sender</dt><dd>{review.email_sender || '--'}</dd></div>
                  <div><dt>Email subject</dt><dd>{review.email_subject || '--'}</dd></div>
                  <div><dt>Evidence</dt><dd>{review.evidence_snippet || '--'}</dd></div>
                </dl>
                {review.gmail_open_url ? <a href={review.gmail_open_url} target="_blank" rel="noreferrer">{review.source_external_opportunity_id ? 'Open original post' : 'Open exact email in Gmail'}</a> : null}
              </section>
            </>
          ) : null}

          {recruiter ? (
            <section className="detailSection">
              <div className="detailSectionHeader">
                <h4>Recruiter profile</h4>
                {!editingRecruiter ? (
                  <button
                    type="button"
                    onClick={() => {
                      setRecruiterEdits({
                        recruiter_name: recruiter.recruiter_name,
                        company: recruiter.company,
                        designation: recruiter.designation,
                        recruiter_email: recruiter.recruiter_email,
                        linkedin_url: recruiter.linkedin_url,
                      })
                      setEditingRecruiter(true)
                    }}
                    disabled={busy}
                  >
                    Edit
                  </button>
                ) : null}
              </div>
              {editingRecruiter ? (
                <div className="detailFormGrid">
                  <label>Name<input value={recruiterEdits.recruiter_name ?? ''} onChange={(event) => setRecruiterEdits((value) => ({ ...value, recruiter_name: event.target.value }))} /></label>
                  <label>Company<input value={recruiterEdits.company ?? ''} onChange={(event) => setRecruiterEdits((value) => ({ ...value, company: event.target.value }))} /></label>
                  <label>Designation<input value={recruiterEdits.designation ?? ''} onChange={(event) => setRecruiterEdits((value) => ({ ...value, designation: event.target.value }))} /></label>
                  <label>Email<input type="email" value={recruiterEdits.recruiter_email ?? ''} onChange={(event) => setRecruiterEdits((value) => ({ ...value, recruiter_email: event.target.value }))} /></label>
                  <label>LinkedIn URL<input value={recruiterEdits.linkedin_url ?? ''} onChange={(event) => setRecruiterEdits((value) => ({ ...value, linkedin_url: event.target.value }))} /></label>
                </div>
              ) : (
                <dl className="detailList">
                  <div><dt>Name</dt><dd>{recruiter.recruiter_name}</dd></div>
                  <div><dt>Company</dt><dd>{recruiter.company}</dd></div>
                  <div><dt>Designation</dt><dd>{recruiter.designation}</dd></div>
                  <div><dt>Email</dt><dd>{recruiter.recruiter_email || '--'}</dd></div>
                  <div><dt>LinkedIn</dt><dd>{recruiter.linkedin_url || '--'}</dd></div>
                  <div><dt>Opportunities</dt><dd>{recruiter.total_opportunity_count}</dd></div>
                  <div><dt>Last email</dt><dd>{recruiter.last_email_received_at ? new Date(recruiter.last_email_received_at).toLocaleString() : '--'}</dd></div>
                </dl>
              )}
              {editingRecruiter ? (
                <div className="detailEditActions">
                  <button
                    type="button"
                    onClick={() => {
                      if (!window.confirm('Save changes to this recruiter profile?')) return
                      setSavingRecruiter(true)
                      updateRecruiterNumber(apiBase, row.id, recruiterEdits)
                        .then(() => {
                          onToast('Saved')
                          setEditingRecruiter(false)
                          return onReload()
                        })
                        .catch((reason) => onError((reason as Error).message))
                        .finally(() => setSavingRecruiter(false))
                    }}
                    disabled={savingRecruiter || busy}
                  >
                    {savingRecruiter ? 'Saving...' : 'Save'}
                  </button>
                  <button type="button" onClick={() => setEditingRecruiter(false)} disabled={savingRecruiter}>Cancel</button>
                </div>
              ) : null}
            </section>
          ) : null}

          {employer ? (
            <section className="detailSection">
              <div className="detailSectionHeader">
                <h4>Employer profile</h4>
                {!editingEmployer ? (
                  <button
                    type="button"
                    onClick={() => {
                      setEmployerEdits({ owner_name: employer.owner_name, company: employer.company })
                      setEditingEmployer(true)
                    }}
                    disabled={busy}
                  >
                    Edit
                  </button>
                ) : null}
              </div>
              {editingEmployer ? (
                <div className="detailFormGrid">
                  <label>Owner<input value={employerEdits.owner_name ?? ''} onChange={(event) => setEmployerEdits((value) => ({ ...value, owner_name: event.target.value }))} /></label>
                  <label>Company<input value={employerEdits.company ?? ''} onChange={(event) => setEmployerEdits((value) => ({ ...value, company: event.target.value }))} /></label>
                </div>
              ) : (
                <dl className="detailList">
                  <div><dt>Owner</dt><dd>{employer.owner_name}</dd></div>
                  <div><dt>Company</dt><dd>{employer.company}</dd></div>
                </dl>
              )}
              {editingEmployer ? (
                <div className="detailEditActions">
                  <button
                    type="button"
                    onClick={() => {
                      if (!window.confirm('Save changes to this employer profile?')) return
                      setSavingEmployer(true)
                      updateEmployerNumber(apiBase, row.id, employerEdits)
                        .then(() => {
                          onToast('Saved')
                          setEditingEmployer(false)
                          return onReload()
                        })
                        .catch((reason) => onError((reason as Error).message))
                        .finally(() => setSavingEmployer(false))
                    }}
                    disabled={savingEmployer || busy}
                  >
                    {savingEmployer ? 'Saving...' : 'Save'}
                  </button>
                  <button type="button" onClick={() => setEditingEmployer(false)} disabled={savingEmployer}>Cancel</button>
                </div>
              ) : null}
            </section>
          ) : null}

          {primaryContact ? (
            <section className="detailSection">
              <h4>Versions and source</h4>
              {profileOutOfSync && activeVersion ? (
                <p className="errorBanner">
                  The profile above doesn't match this active version ({activeVersion.owner_name} · {activeVersion.company}).
                  {' '}
                  <button
                    type="button"
                    onClick={() => {
                      setSyncingVersion(true)
                      selectContactVersion(apiBase, primaryRole, row.id, activeVersion.id)
                        .then(() => {
                          onToast('Profile synced from active version')
                          closeEditors()
                          return onReload()
                        })
                        .catch((reason) => onError((reason as Error).message))
                        .finally(() => setSyncingVersion(false))
                    }}
                    disabled={syncingVersion || busy}
                  >
                    {syncingVersion ? 'Syncing...' : 'Sync profile from this version'}
                  </button>
                </p>
              ) : null}
              <label>
                Active {primaryRole} version ({primaryContact.version_count})
                <select
                  value={primaryContact.active_lead_id ?? ''}
                  disabled={versionsLoading || busy || deletingVersion}
                  onChange={(event) => {
                    selectContactVersion(apiBase, primaryRole, row.id, Number(event.target.value))
                      .then(() => {
                        onToast('Version switched')
                        closeEditors()
                        return onReload()
                      })
                      .catch((reason) => onError((reason as Error).message))
                  }}
                >
                  {versionsLoading ? <option value="">Loading versions...</option> : null}
                  {versions.map((version, index) => {
                    const tags = [
                      index === 0 ? 'Newest' : null,
                      version.id === primaryContact.active_lead_id ? 'Current' : null,
                    ].filter(Boolean).join(', ')
                    const date = new Date(version.created_at).toLocaleDateString()
                    return (
                      <option key={version.id} value={version.id}>
                        {version.owner_name} · {version.company} · {version.extraction_source} · {date}{tags ? ` (${tags})` : ''}
                      </option>
                    )
                  })}
                </select>
              </label>
              <div className="detailEditActions">
                <button
                  type="button"
                  className="dangerButton"
                  disabled={versionsLoading || busy || deletingVersion || versions.length <= 1 || primaryContact.active_lead_id == null}
                  onClick={() => {
                    if (primaryContact.active_lead_id == null) return
                    if (!window.confirm('Delete this version? This will permanently remove only this saved version. The main contact card and its other versions will remain available.')) return
                    setDeletingVersion(true)
                    deleteContactVersion(apiBase, primaryRole, row.id, primaryContact.active_lead_id)
                      .then(() => {
                        onToast('Version deleted')
                        closeEditors()
                        return onReload()
                      })
                      .catch((reason) => onError((reason as Error).message))
                      .finally(() => setDeletingVersion(false))
                  }}
                >
                  {deletingVersion ? 'Deleting version...' : 'Delete this version'}
                </button>
              </div>
              {primaryContact.source_link_url ? <a href={primaryContact.source_link_url} target="_blank" rel="noreferrer">{primaryContact.source_type === 'nvoids' ? 'Open original post' : 'Open exact email in Gmail'}</a> : null}
            </section>
          ) : null}
        </div>

        <footer className="detailPanelActions">
          <button type="button" onClick={() => act('mark-recruiter', reviewEdits)} disabled={busy}>{pendingAction === 'mark-recruiter' ? 'Working...' : 'Mark as Recruiter'}</button>
          <button type="button" onClick={() => act('mark-employer', reviewEdits)} disabled={busy}>{pendingAction === 'mark-employer' ? 'Working...' : 'Mark as Employer'}</button>
          <button type="button" onClick={() => act('rescore')} disabled={busy}>{pendingAction === 'rescore' ? 'Working...' : 'Rescore'}</button>
          <button type="button" className="dangerButton" onClick={() => act('delete')} disabled={busy}>{pendingAction === 'delete' ? 'Deleting...' : 'Delete Entire Contact'}</button>
        </footer>
      </div>
    </div>
  )
}
