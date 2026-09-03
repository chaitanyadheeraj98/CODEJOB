import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react'

import {
  acknowledgeContactEnrichment,
  approveContactLink,
  approveContactMerge,
  ContactPhoneConflictError,
  deleteContactVersion,
  dismissReviewSuggestion,
  getEmployerNumber,
  getRecruiterNumber,
  getRecruiterReputation,
  listContactVersions,
  listExtractionAudit,
  deleteOlderContactVersions,
  patchNumberReview,
  rescoreContactWithDiff,
  selectContactVersion,
  unmarkContactRole,
  updateEmployerNumber,
  updateRecruiterNumber,
} from './api'
import { CategoryChip, StatusBadge } from './StatusBadge'
import type { ContactFieldChange, EmployerNumberCard, ExtractionAuditEntry, InventoryAction, InventoryRow, PhoneEntry, PremiumNumberVersion, RecruiterNumberCard, RecruiterReputation, ReviewEdits } from './types'

type RecruiterEdits = Partial<Pick<RecruiterNumberCard, 'recruiter_name' | 'company' | 'secondary_company' | 'designation' | 'recruiter_email' | 'linkedin_url' | 'recruiter_verification_level' | 'do_not_work_again' | 'do_not_work_again_reason'>> & { phone_number?: string; phones?: string[]; emails?: string[] }
type EmployerEdits = Partial<Pick<EmployerNumberCard, 'owner_name' | 'company' | 'secondary_company' | 'designation' | 'employer_email' | 'linkedin_url' | 'recruiter_verification_level' | 'do_not_work_again' | 'do_not_work_again_reason'>> & { phones?: string[]; emails?: string[] }

type ConflictContact = { recruiter?: RecruiterNumberCard; employer?: EmployerNumberCard }

function buildRecruiterUndoPatch(changes: ContactFieldChange[]): RecruiterEdits {
  const patch: RecruiterEdits = {}
  for (const change of changes) {
    switch (change.field) {
      case 'recruiter_name': patch.recruiter_name = change.old; break
      case 'company': patch.company = change.old; break
      case 'designation': patch.designation = change.old; break
      case 'recruiter_email': patch.recruiter_email = change.old; break
      case 'linkedin_url': patch.linkedin_url = change.old; break
      case 'display_phone_number': patch.phone_number = change.old; break
    }
  }
  return patch
}

function buildEmployerUndoPatch(changes: ContactFieldChange[]): EmployerEdits {
  // display_phone_number has no revert path here - the employer PATCH endpoint doesn't
  // accept a phone number at all, so that one field is undo-visible but not undoable.
  const patch: EmployerEdits = {}
  for (const change of changes) {
    switch (change.field) {
      case 'owner_name': patch.owner_name = change.old; break
      case 'company': patch.company = change.old; break
      case 'designation': patch.designation = change.old; break
      case 'employer_email': patch.employer_email = change.old; break
      case 'linkedin_url': patch.linkedin_url = change.old; break
    }
  }
  return patch
}

function RescoreDiffModal({
  changes,
  undoing,
  onKeep,
  onUndo,
}: {
  changes: ContactFieldChange[]
  undoing: boolean
  onKeep: () => void
  onUndo: () => void
}) {
  return (
    <div className="detailPanelOverlay" onMouseDown={(event) => event.target === event.currentTarget && onKeep()}>
      <div className="detailPanel" role="dialog" aria-modal="true" aria-label="Rescore results">
        <header className="detailPanelHeader">
          <div>
            <p className="detailPanelEyebrow">AI rescore</p>
            <h3>Here's what changed</h3>
          </div>
          <button type="button" className="iconBtn" aria-label="Close" onClick={onKeep}>×</button>
        </header>
        <div className="detailPanelBody">
          <p className="subtle">These changes are already saved. Review what the AI found, then keep them or undo.</p>
          <dl className="detailList">
            {changes.map((change) => (
              <div key={change.field}>
                <dt>{change.label}</dt>
                <dd><span className="subtle">{change.old || '--'}</span>{' → '}<strong>{change.new || '--'}</strong></dd>
              </div>
            ))}
          </dl>
          <div className="detailEditActions">
            <button type="button" className="primaryButton" onClick={onKeep} disabled={undoing}>Keep changes</button>
            <button type="button" className="dangerButton" onClick={onUndo} disabled={undoing}>{undoing ? 'Undoing...' : 'Undo'}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

// Review rows created by contact_identity_service's identity-conflict path are always
// tagged role="recruiter" even when the matched contact is employer-only, so the id alone
// doesn't say which endpoint owns it - try recruiter first, fall back to employer.
function fetchConflictContact(apiBase: string, contactId: number): Promise<ConflictContact> {
  return getRecruiterNumber(apiBase, contactId)
    .then((recruiter) => ({ recruiter }) as ConflictContact)
    .catch(() => getEmployerNumber(apiBase, contactId).then((employer) => ({ employer }) as ConflictContact).catch(() => ({}) as ConflictContact))
}

function externalUrl(value: string): string {
  return /^https?:\/\//i.test(value) ? value : `https://${value}`
}

function PhoneNumbersList({
  phones,
  fallback,
  includeLabeled = false,
}: {
  phones?: PhoneEntry[]
  fallback: string
  includeLabeled?: boolean
}) {
  const numbers = (phones ?? []).filter((entry) => includeLabeled || !entry.label)
  if (numbers.length === 0) return <>{fallback || '--'}</>
  return (
    <div className="phoneNumbersList">
      {numbers.map((entry) => (
        <div key={`${entry.phone}-${entry.extension}`} className="phoneNumbersListRow">
          <span>{entry.display}</span>
          {entry.is_primary ? <span className="categoryChip">Primary</span> : null}
          {entry.label ? <span className="categoryChip">{entry.label}</span> : null}
        </div>
      ))}
    </div>
  )
}

function EmailsList({ emails, fallback }: { emails?: Array<{ email: string; is_primary: boolean }>; fallback: string }) {
  const values = emails?.length ? emails : fallback ? [{ email: fallback, is_primary: true }] : []
  if (values.length === 0) return <>--</>
  return (
    <div className="emailAddressesList">
      {values.map((entry) => (
        <div key={entry.email} className="emailAddressesListRow">
          <a href={`mailto:${entry.email}`}>{entry.email}</a>
          {entry.is_primary ? <span className="categoryChip">Primary</span> : null}
        </div>
      ))}
    </div>
  )
}

function OtherNumbersList({ phones }: { phones?: PhoneEntry[] }) {
  const others = (phones ?? []).filter((entry) => entry.label)
  if (others.length === 0) return null
  return (
    <div className="phoneNumbersList">
      {others.map((entry) => (
        <div key={`${entry.phone}-${entry.extension}`} className="phoneNumbersListRow">
          <span>{entry.display}</span>
          <span className="categoryChip">{entry.label.charAt(0).toUpperCase() + entry.label.slice(1)}</span>
        </div>
      ))}
    </div>
  )
}

function PhoneListEditor({ phones, onChange }: { phones: string[]; onChange: (next: string[]) => void }) {
  return (
    <div className="phoneListEditor">
      {phones.map((value, index) => (
        // eslint-disable-next-line react/no-array-index-key -- rows have no stable identity, only position
        <div key={index} className="phoneListEditorRow">
          <input
            placeholder="(XXX) XXX-XXXX ext 1234"
            value={value}
            onChange={(event) => onChange(phones.map((entry, i) => (i === index ? event.target.value : entry)))}
          />
          <span className="categoryChip">{index === 0 ? 'Primary' : 'Secondary'}</span>
          <button type="button" className="iconBtn" aria-label="Remove phone" onClick={() => onChange(phones.filter((_, i) => i !== index))}>×</button>
        </div>
      ))}
      <button type="button" onClick={() => onChange([...phones, ''])}>+ Add phone</button>
    </div>
  )
}

function EmailListEditor({ emails, onChange }: { emails: string[]; onChange: (next: string[]) => void }) {
  return (
    <div className="emailListEditor">
      {emails.map((value, index) => (
        <div key={index} className="emailListEditorRow">
          <input
            type="email"
            placeholder="name@example.com"
            value={value}
            onChange={(event) => onChange(emails.map((entry, i) => (i === index ? event.target.value : entry)))}
          />
          <span className="categoryChip">{index === 0 ? 'Primary' : 'Secondary'}</span>
          <button type="button" className="iconBtn" aria-label="Remove email" onClick={() => onChange(emails.filter((_, i) => i !== index))}>&times;</button>
        </div>
      ))}
      <button type="button" onClick={() => onChange([...emails, ''])}>+ Add email</button>
    </div>
  )
}

function conflictContactLabel(contact: ConflictContact | null): string {
  if (!contact) return 'existing contact'
  const name = contact.recruiter?.recruiter_name ?? contact.employer?.owner_name
  const company = contact.recruiter?.company ?? contact.employer?.company
  return name ? `${name}${company ? ` · ${company}` : ''}` : 'existing contact'
}

function ConflictContactPreview({ contact, loading, matchedOn }: { contact: ConflictContact | null; loading: boolean; matchedOn?: 'phone' | 'email' }) {
  if (loading) return <p className="subtle">Loading matched contact...</p>
  const details = contact?.recruiter ?? contact?.employer
  if (!details) return <p className="subtle">Contact details unavailable.</p>
  const name = contact?.recruiter?.recruiter_name ?? contact?.employer?.owner_name
  return (
    <dl className="detailList">
      <div><dt>Name</dt><dd>{name || '--'}</dd></div>
      <div><dt>Role</dt><dd>{contact?.recruiter ? 'Recruiter' : 'Employer'}</dd></div>
      <div><dt>Company</dt><dd>{details.company || '--'}</dd></div>
      {details.secondary_company ? <div><dt>Sister company</dt><dd>{details.secondary_company}</dd></div> : null}
      <div><dt>Designation</dt><dd>{details.designation || '--'}</dd></div>
      <div><dt>Phone numbers</dt><dd><PhoneNumbersList phones={details.phones} fallback={details.display_phone_number} includeLabeled />{matchedOn === 'phone' ? <span className="categoryChip">Matched</span> : null}</dd></div>
      <div><dt>Email addresses</dt><dd><EmailsList emails={details.emails} fallback={contact?.recruiter?.recruiter_email ?? contact?.employer?.employer_email ?? ''} />{matchedOn === 'email' ? <span className="categoryChip">Matched</span> : null}</dd></div>
      <div><dt>LinkedIn</dt><dd>{details.linkedin_url ? <a href={externalUrl(details.linkedin_url)} target="_blank" rel="noreferrer">{details.linkedin_url}</a> : '--'}</dd></div>
      <div><dt>Verification</dt><dd>{details.recruiter_verification_level}</dd></div>
      <div><dt>Last updated</dt><dd>{details.updated_at ? new Date(details.updated_at).toLocaleString() : '--'}</dd></div>
    </dl>
  )
}

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
  onPhoneConflict: (row: InventoryRow, conflictingContactId: number) => void
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
  onPhoneConflict,
}: DetailPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const primaryRole = row.recruiter ? 'recruiter' : 'employer'
  const primaryContact = row.recruiter ?? row.employer
  const auditSourceEmailId = row.review?.source_email_id
    ?? (primaryContact?.source_type === 'gmail' ? primaryContact.source_id : null)
    ?? row.recruiter?.first_detected_email_id
    ?? row.employer?.source_email_id
    ?? null
  const [reviewEdits, setReviewEdits] = useState<ReviewEdits>({})
  const [editingReview, setEditingReview] = useState(false)
  const [savingReview, setSavingReview] = useState(false)
  const [versions, setVersions] = useState<PremiumNumberVersion[]>([])
  const [versionsLoading, setVersionsLoading] = useState(Boolean(primaryContact))
  const [editingRecruiter, setEditingRecruiter] = useState(false)
  const [recruiterEdits, setRecruiterEdits] = useState<RecruiterEdits>({})
  const [savingRecruiter, setSavingRecruiter] = useState(false)
  const [editingEmployer, setEditingEmployer] = useState(false)
  const [employerEdits, setEmployerEdits] = useState<EmployerEdits>({})
  const [savingEmployer, setSavingEmployer] = useState(false)
  const [pendingAction, setPendingAction] = useState<InventoryAction | null>(null)
  const [rescoring, setRescoring] = useState(false)
  const [rescoreDiff, setRescoreDiff] = useState<ContactFieldChange[] | null>(null)
  const [undoingRescore, setUndoingRescore] = useState(false)
  const [removingRole, setRemovingRole] = useState<'recruiter' | 'employer' | null>(null)
  const [deletingVersion, setDeletingVersion] = useState(false)
  const [deletingOlderVersions, setDeletingOlderVersions] = useState(false)
  const [syncingVersion, setSyncingVersion] = useState(false)
  const [reputation, setReputation] = useState<RecruiterReputation | null>(null)
  const [auditEntries, setAuditEntries] = useState<ExtractionAuditEntry[]>([])
  const [auditLoading, setAuditLoading] = useState(Boolean(auditSourceEmailId))
  const [conflictTarget, setConflictTarget] = useState<ConflictContact | null>(null)
  const [conflictSecondary, setConflictSecondary] = useState<ConflictContact | null>(null)
  const [conflictLoading, setConflictLoading] = useState(Boolean(row.review?.target_contact_id))
  const [resolvingConflict, setResolvingConflict] = useState<'link' | 'merge-target' | 'merge-secondary' | 'dismiss' | null>(null)
  const [acknowledging, setAcknowledging] = useState(false)

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

  useEffect(() => {
    if (!row.recruiter) return
    getRecruiterReputation(apiBase, row.id)
      .then(setReputation)
      .catch((reason) => onError((reason as Error).message))
  }, [apiBase, onError, row.id, row.recruiter])

  useEffect(() => {
    if (!auditSourceEmailId) return
    listExtractionAudit(apiBase, auditSourceEmailId)
      .then(setAuditEntries)
      .catch((reason) => onError((reason as Error).message))
      .finally(() => setAuditLoading(false))
  }, [apiBase, auditSourceEmailId, onError, row.key])

  const targetContactId = row.review?.target_contact_id
  const secondaryContactId = row.review?.secondary_contact_id
  useEffect(() => {
    if (!targetContactId) {
      setConflictTarget(null)
      setConflictSecondary(null)
      return
    }
    setConflictLoading(true)
    Promise.all([
      fetchConflictContact(apiBase, targetContactId),
      secondaryContactId ? fetchConflictContact(apiBase, secondaryContactId) : Promise.resolve(null),
    ])
      .then(([target, secondary]) => {
        setConflictTarget(target)
        setConflictSecondary(secondary)
      })
      .catch((reason) => onError((reason as Error).message))
      .finally(() => setConflictLoading(false))
  }, [apiBase, onError, targetContactId, secondaryContactId])

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

  const runRescore = () => {
    setRescoring(true)
    rescoreContactWithDiff(apiBase, primaryRole, row.id)
      .then((result) => {
        if (result.status === 'source_not_found') {
          onError('No source email or post found to rescore from')
          return
        }
        return onReload().then(() => {
          if (result.changes.length === 0) onToast('Rescore found no changes')
          else setRescoreDiff(result.changes)
        })
      })
      .catch((reason) => {
        if (reason instanceof ContactPhoneConflictError && reason.conflictingContactId != null) {
          onPhoneConflict(row, reason.conflictingContactId)
          return
        }
        onError((reason as Error).message)
      })
      .finally(() => setRescoring(false))
  }

  const undoRescore = () => {
    if (!rescoreDiff) return
    setUndoingRescore(true)
    const call = primaryRole === 'recruiter'
      ? updateRecruiterNumber(apiBase, row.id, buildRecruiterUndoPatch(rescoreDiff))
      : updateEmployerNumber(apiBase, row.id, buildEmployerUndoPatch(rescoreDiff))
    call
      .then(() => {
        onToast('Rescore undone')
        setRescoreDiff(null)
        return onReload()
      })
      .catch((reason) => onError((reason as Error).message))
      .finally(() => setUndoingRescore(false))
  }

  const removeRole = (role: 'recruiter' | 'employer') => {
    if (!window.confirm(`Remove the ${role} role from this contact? Its ${role} profile will be hidden until the role is added again.`)) return
    setRemovingRole(role)
    unmarkContactRole(apiBase, role, row.id)
      .then(() => {
        onToast('Removed')
        return onReload()
      })
      .catch((reason) => onError((reason as Error).message))
      .finally(() => setRemovingRole(null))
  }

  const resolveConflict = (kind: 'link' | 'merge-target' | 'merge-secondary' | 'dismiss') => {
    if (!row.review) return
    const reviewId = row.review.id
    setResolvingConflict(kind)
    const request =
      kind === 'link' ? approveContactLink(apiBase, reviewId)
      : kind === 'merge-target' ? approveContactMerge(apiBase, reviewId, targetContactId ?? undefined)
      : kind === 'merge-secondary' ? approveContactMerge(apiBase, reviewId, secondaryContactId ?? undefined)
      : dismissReviewSuggestion(apiBase, reviewId)
    request
      .then((result) => {
        onToast(kind === 'dismiss'
          ? ('follow_up_review_id' in result && result.follow_up_review_id
              ? 'Kept separate \u2014 a new review was created to resolve who owns this number.'
              : 'Kept as a separate contact')
          : kind === 'link' ? 'Linked to existing contact' : 'Merged')
        onClose()
        return onReload()
      })
      .catch((reason) => onError((reason as Error).message))
      .finally(() => setResolvingConflict(null))
  }

  const acknowledgeEnrichment = () => {
    if (!row.review) return
    setAcknowledging(true)
    acknowledgeContactEnrichment(apiBase, row.review.id)
      .then(() => {
        onToast('Acknowledged')
        onClose()
        return onReload()
      })
      .catch((reason) => onError((reason as Error).message))
      .finally(() => setAcknowledging(false))
  }

  const review = row.review
  const recruiter = row.recruiter
  const employer = row.employer
  const conflictUnresolved = !!review?.target_contact_id && review.reason_code !== 'contact_enriched'

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
        aria-label={`Premium number details for ${row.number || '(XXX) XXX-XXXX'}`}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <header className="detailPanelHeader">
          <div>
            <p className="detailPanelEyebrow">{row.kind === 'review' ? 'Pending review' : 'Number contact'}</p>
            <h3 className={row.number ? '' : 'detailPanelNumberEmpty'}>{row.number || '(XXX) XXX-XXXX'}</h3>
            <div className="categoryChips">
              {row.categories.map((category) => <CategoryChip key={category} category={category} />)}
              <StatusBadge status={row.status} reasonCode={row.review?.reason_code} />
            </div>
          </div>
          <button type="button" className="iconBtn" aria-label="Close details" onClick={onClose}>×</button>
        </header>

        <div className="detailPanelBody">
          {review ? (
            <>
              <section className="detailSection">
                <div className="detailSectionHeader">
                  <h4>Contact details</h4>
                  {!editingReview ? (
                    <button
                      type="button"
                      onClick={() => {
                        setReviewEdits({
                          display_phone_number: review.display_phone_number,
                          owner_name: review.owner_name,
                          company: review.company,
                          designation: review.designation,
                          contact_email: review.contact_email,
                          linkedin_url: review.linkedin_url ?? '',
                        })
                        setEditingReview(true)
                      }}
                      disabled={busy}
                    >
                      Edit
                    </button>
                  ) : null}
                </div>
                {editingReview ? (
                  <div className="detailFormGrid">
                    <label>Phone<input placeholder="(XXX) XXX-XXXX ext 1234" value={reviewEdits.display_phone_number ?? review.display_phone_number} onChange={(event) => setReviewEdits((value) => ({ ...value, display_phone_number: event.target.value }))} /></label>
                    <label>Owner<input value={reviewEdits.owner_name ?? review.owner_name} onChange={(event) => setReviewEdits((value) => ({ ...value, owner_name: event.target.value }))} /></label>
                    <label>Company<input value={reviewEdits.company ?? review.company} onChange={(event) => setReviewEdits((value) => ({ ...value, company: event.target.value }))} /></label>
                    <label>Designation<input value={reviewEdits.designation ?? review.designation} onChange={(event) => setReviewEdits((value) => ({ ...value, designation: event.target.value }))} /></label>
                    <label>Contact email<input type="email" value={reviewEdits.contact_email ?? review.contact_email} onChange={(event) => setReviewEdits((value) => ({ ...value, contact_email: event.target.value }))} /></label>
                    <label>LinkedIn profile<input value={reviewEdits.linkedin_url ?? review.linkedin_url ?? ''} onChange={(event) => setReviewEdits((value) => ({ ...value, linkedin_url: event.target.value }))} /></label>
                  </div>
                ) : (
                  <dl className="detailList">
                    <div><dt>Phone</dt><dd>{review.display_phone_number || '--'}</dd></div>
                    <div><dt>Owner</dt><dd>{review.owner_name}</dd></div>
                    <div><dt>Company</dt><dd>{review.company}</dd></div>
                    <div><dt>Designation</dt><dd>{review.designation}</dd></div>
                    <div><dt>Contact email</dt><dd>{review.contact_email ? <a href={`mailto:${review.contact_email}`}>{review.contact_email}</a> : '--'}</dd></div>
                    <div><dt>LinkedIn profile</dt><dd>{review.linkedin_url ? <a href={externalUrl(review.linkedin_url)} target="_blank" rel="noreferrer">{review.linkedin_url}</a> : '--'}</dd></div>
                  </dl>
                )}
                {editingReview ? (
                  <div className="detailEditActions">
                    <button
                      type="button"
                      className="primaryButton"
                      onClick={() => {
                        setSavingReview(true)
                        patchNumberReview(apiBase, review.id, {
                          owner_name: reviewEdits.owner_name,
                          company: reviewEdits.company,
                          designation: reviewEdits.designation,
                          contact_email: reviewEdits.contact_email,
                          display_phone_number: reviewEdits.display_phone_number,
                          linkedin_url: reviewEdits.linkedin_url,
                        })
                          .then(() => {
                            onToast('Saved')
                            setEditingReview(false)
                            return onReload()
                          })
                          .catch((reason) => onError((reason as Error).message))
                          .finally(() => setSavingReview(false))
                      }}
                      disabled={savingReview || busy}
                    >
                      {savingReview ? 'Saving...' : 'Save'}
                    </button>
                    <button type="button" onClick={() => { setReviewEdits({}); setEditingReview(false) }} disabled={savingReview}>Cancel</button>
                  </div>
                ) : null}
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
                  <div><dt>Review reason</dt><dd><StatusBadge status={'Pending'} reasonCode={review.reason_code} /></dd></div>
                  <div><dt>Occurrences</dt><dd>{review.occurrence_count}</dd></div>
                  <div><dt>Score basis</dt><dd>{review.scored_with === 'legacy' ? 'Legacy (rescore recommended)' : 'Current'}</dd></div>
                  <div><dt>Email sender</dt><dd>{review.email_sender || '--'}</dd></div>
                  <div><dt>Email subject</dt><dd>{review.email_subject || '--'}</dd></div>
                  <div><dt>Evidence date</dt><dd>{review.evidence_at ? new Date(review.evidence_at).toLocaleString() : '--'}</dd></div>
                  <div><dt>Evidence</dt><dd>{review.evidence_snippet || '--'}</dd></div>
                </dl>
                {review.gmail_open_url ? <a href={review.gmail_open_url} target="_blank" rel="noreferrer">{review.source_external_opportunity_id ? 'Open original post' : 'Open exact email in Gmail'}</a> : null}
              </section>
              {review.target_contact_id && review.reason_code === 'contact_enriched' ? (
                <section className="detailSection">
                  <h4>Here's what the AI merged in</h4>
                  <p className="subtle">This is already saved on the contact. Review it, then acknowledge.</p>
                  <dl className="detailList">
                    {(() => {
                      let changes: { field: string; label: string; old: string; new: string }[] = []
                      try { changes = JSON.parse(review.field_changes_json || '[]') } catch { /* malformed, show nothing */ }
                      return changes.map((change) => (
                        <div key={change.field}>
                          <dt>{change.label}</dt>
                          <dd><span className="subtle">{change.old || '--'}</span>{' → '}<strong>{change.new || '--'}</strong></dd>
                        </div>
                      ))
                    })()}
                  </dl>
                  <div className="detailEditActions">
                    <button type="button" className="primaryButton" onClick={acknowledgeEnrichment} disabled={busy || acknowledging}>
                      {acknowledging ? 'Acknowledging...' : 'Acknowledge'}
                    </button>
                  </div>
                </section>
              ) : null}
              {review.target_contact_id && review.reason_code !== 'contact_enriched' ? (
                <section className="detailSection">
                  <h4>Resolve identity conflict</h4>
                  <div className="mergePreviewCard">
                    <p><strong>Existing contact</strong></p>
                    <ConflictContactPreview contact={conflictTarget} loading={conflictLoading} matchedOn="phone" />
                  </div>
                  {review.secondary_contact_id ? (
                    <div className="mergePreviewCard">
                      <p><strong>Second matched contact</strong></p>
                      <ConflictContactPreview contact={conflictSecondary} loading={conflictLoading} matchedOn="email" />
                    </div>
                  ) : null}
                  <div className="detailEditActions">
                    {review.secondary_contact_id ? (
                      <>
                        <button type="button" onClick={() => resolveConflict('merge-target')} disabled={busy || resolvingConflict !== null}>
                          {resolvingConflict === 'merge-target' ? 'Merging...' : `Merge into "${conflictContactLabel(conflictTarget)}"`}
                        </button>
                        <button type="button" onClick={() => resolveConflict('merge-secondary')} disabled={busy || resolvingConflict !== null}>
                          {resolvingConflict === 'merge-secondary' ? 'Merging...' : `Merge into "${conflictContactLabel(conflictSecondary)}"`}
                        </button>
                        <button type="button" className="dangerButton" onClick={() => resolveConflict('dismiss')} disabled={busy || resolvingConflict !== null}>
                          {resolvingConflict === 'dismiss' ? 'Dismissing...' : 'Keep separate'}
                        </button>
                      </>
                    ) : (
                      <>
                        <button type="button" onClick={() => resolveConflict('link')} disabled={busy || resolvingConflict !== null}>
                          {resolvingConflict === 'link' ? 'Linking...' : 'Yes, same person — link'}
                        </button>
                        <button type="button" className="dangerButton" onClick={() => resolveConflict('dismiss')} disabled={busy || resolvingConflict !== null}>
                          {resolvingConflict === 'dismiss' ? 'Dismissing...' : 'No, different person — dismiss'}
                        </button>
                      </>
                    )}
                  </div>
                </section>
              ) : null}
            </>
          ) : null}

          {auditSourceEmailId ? (
            <details className={'detailSection'}>
              <summary>Extraction audit</summary>
              {auditLoading ? <p className={'subtle'}>Loading extraction decisions...</p> : null}
              {!auditLoading && auditEntries.length === 0 ? <p className={'subtle'}>No extraction audit entries.</p> : null}
              {auditEntries.length ? (
                <ul>
                  {auditEntries.map((entry) => (
                    <li key={entry.id}>
                      <strong>{entry.status === 'accepted' ? 'Accepted' : 'Rejected'}:</strong>{' '}
                      {entry.raw_value} — {entry.reason}
                    </li>
                  ))}
                </ul>
              ) : null}
            </details>
          ) : null}

          {recruiter ? (
            <section className="detailSection">
              <div className="detailSectionHeader">
                <h4>Recruiter profile</h4>
                <div className="detailEditActions">
                {row.kind === 'contact' && employer ? (
                  <button type="button" className="dangerText" onClick={() => removeRole('recruiter')} disabled={busy || removingRole !== null}>
                    {removingRole === 'recruiter' ? 'Removing...' : 'Remove Recruiter Role'}
                  </button>
                ) : null}
                {!editingRecruiter ? (
                  <button
                    type="button"
                    onClick={() => {
                      setRecruiterEdits({
                        recruiter_name: recruiter.recruiter_name,
                        company: recruiter.company,
                        secondary_company: recruiter.secondary_company ?? '',
                        designation: recruiter.designation,
                        phones: recruiter.phones?.length ? recruiter.phones.map((entry) => entry.display) : (recruiter.display_phone_number ? [recruiter.display_phone_number] : []),
                        emails: recruiter.emails?.length ? recruiter.emails.map((entry) => entry.email) : (recruiter.recruiter_email ? [recruiter.recruiter_email] : []),
                        linkedin_url: recruiter.linkedin_url,
                        recruiter_verification_level: recruiter.recruiter_verification_level,
                        do_not_work_again: recruiter.do_not_work_again,
                        do_not_work_again_reason: recruiter.do_not_work_again_reason,
                      })
                      setEditingRecruiter(true)
                    }}
                    disabled={busy}
                  >
                    Edit
                  </button>
                ) : null}
                </div>
              </div>
              {editingRecruiter ? (
                <div className="detailFormGrid">
                  <label>Name<input value={recruiterEdits.recruiter_name ?? ''} onChange={(event) => setRecruiterEdits((value) => ({ ...value, recruiter_name: event.target.value }))} /></label>
                  <label>Company<input value={recruiterEdits.company ?? ''} onChange={(event) => setRecruiterEdits((value) => ({ ...value, company: event.target.value }))} /></label>
                  <label>Sister company<input value={recruiterEdits.secondary_company ?? ''} onChange={(event) => setRecruiterEdits((value) => ({ ...value, secondary_company: event.target.value }))} placeholder="Optional" /></label>
                  <label className="detailFormGridFullRow">Phone numbers<PhoneListEditor phones={recruiterEdits.phones ?? []} onChange={(next) => setRecruiterEdits((value) => ({ ...value, phones: next }))} /></label>
                  <label className="detailFormGridFullRow">Email addresses<EmailListEditor emails={recruiterEdits.emails ?? []} onChange={(next) => setRecruiterEdits((value) => ({ ...value, emails: next }))} /></label>
                  <label>Designation<input value={recruiterEdits.designation ?? ''} onChange={(event) => setRecruiterEdits((value) => ({ ...value, designation: event.target.value }))} /></label>
                  <label>LinkedIn URL<input value={recruiterEdits.linkedin_url ?? ''} onChange={(event) => setRecruiterEdits((value) => ({ ...value, linkedin_url: event.target.value }))} /></label>
                  <label>
                    Verification
                    <select value={recruiterEdits.recruiter_verification_level ?? 'unverified'} onChange={(event) => setRecruiterEdits((value) => ({ ...value, recruiter_verification_level: event.target.value as RecruiterNumberCard['recruiter_verification_level'] }))}>
                      <option value="unverified">Unverified</option><option value="verified">Verified</option><option value="trusted">Trusted</option>
                    </select>
                  </label>
                  <label className="checkboxLabel">
                    <input
                      type="checkbox"
                      checked={recruiterEdits.do_not_work_again ?? false}
                      onChange={(event) => {
                        const checked = event.target.checked
                        if (checked && !window.confirm('Mark this recruiter as "do not work with again"?\n\nTheir open opportunities will be excluded from future resume-matching suggestions in Application Tracking.')) {
                          return
                        }
                        setRecruiterEdits((value) => ({ ...value, do_not_work_again: checked }))
                      }}
                    />
                    Do not work with again
                  </label>
                  {recruiterEdits.do_not_work_again ? (
                    <label>Reason<input value={recruiterEdits.do_not_work_again_reason ?? ''} onChange={(event) => setRecruiterEdits((value) => ({ ...value, do_not_work_again_reason: event.target.value }))} /></label>
                  ) : null}
                </div>
              ) : (
                <dl className="detailList">
                  <div><dt>Name</dt><dd>{recruiter.recruiter_name}</dd></div>
                  <div><dt>Company</dt><dd>{recruiter.company}</dd></div>
                  {recruiter.secondary_company ? <div><dt>Sister company</dt><dd>{recruiter.secondary_company}</dd></div> : null}
                  <div><dt>Designation</dt><dd>{recruiter.designation}</dd></div>
                  <div><dt>Phone numbers</dt><dd><PhoneNumbersList phones={recruiter.phones} fallback={recruiter.display_phone_number} /></dd></div>
                  {recruiter.phones?.some((entry) => entry.label) ? <div><dt>Other numbers</dt><dd><OtherNumbersList phones={recruiter.phones} /></dd></div> : null}
                  <div><dt>Email addresses</dt><dd><EmailsList emails={recruiter.emails} fallback={recruiter.recruiter_email} /></dd></div>
                  <div><dt>LinkedIn</dt><dd>{recruiter.linkedin_url ? <a href={externalUrl(recruiter.linkedin_url)} target="_blank" rel="noreferrer">{recruiter.linkedin_url}</a> : '--'}</dd></div>
                  <div><dt>Verification</dt><dd>{recruiter.recruiter_verification_level}</dd></div>
                  <div><dt>Do not work again</dt><dd>{recruiter.do_not_work_again ? recruiter.do_not_work_again_reason || 'Yes' : 'No'}</dd></div>
                  <div><dt>Opportunities</dt><dd>{recruiter.total_opportunity_count}</dd></div>
                  <div><dt>Seen</dt><dd>{recruiter.seen_count} times</dd></div>
                  <div><dt>Last email</dt><dd>{recruiter.last_email_received_at ? new Date(recruiter.last_email_received_at).toLocaleString() : '--'}</dd></div>
                </dl>
              )}
              {editingRecruiter ? (
                <div className="detailEditActions">
                  <button
                    type="button"
                    className="primaryButton"
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

          {recruiter ? (
            <section className="detailSection recruiterReputation">
              <h4>Reputation</h4>
              {!reputation ? <p className="subtle">Loading recruiter history...</p> : (
                <>
                  {reputation.history_label === 'limited_history' ? <p className="limitedHistoryLabel">Limited history</p> : null}
                  <dl className="detailList">
                    <div><dt>Outreach attempts</dt><dd>{reputation.outreach_count}</dd></div>
                    <div><dt>Replies</dt><dd>{reputation.replies_count}</dd></div>
                    <div><dt>Median first reply</dt><dd>{reputation.median_first_reply_business_days == null ? '--' : `${reputation.median_first_reply_business_days} business days`}</dd></div>
                    <div><dt>Client submissions</dt><dd>{reputation.submissions_count}</dd></div>
                    <div><dt>Interviews after submission</dt><dd>{reputation.interviews_after_submission_count}</dd></div>
                    <div><dt>Offers</dt><dd>{reputation.offers_count}</dd></div>
                    <div><dt>Last active</dt><dd>{reputation.last_active_at ? new Date(reputation.last_active_at).toLocaleString() : '--'}</dd></div>
                  </dl>
                </>
              )}
            </section>
          ) : null}

          {employer ? (
            <section className="detailSection">
              <div className="detailSectionHeader">
                <h4>Employer profile</h4>
                <div className="detailEditActions">
                {row.kind === 'contact' && recruiter ? (
                  <button type="button" className="dangerText" onClick={() => removeRole('employer')} disabled={busy || removingRole !== null}>
                    {removingRole === 'employer' ? 'Removing...' : 'Remove Employer Role'}
                  </button>
                ) : null}
                {!editingEmployer ? (
                  <button
                    type="button"
                    onClick={() => {
                      setEmployerEdits({
                        owner_name: employer.owner_name,
                        company: employer.company,
                        secondary_company: employer.secondary_company ?? '',
                        designation: employer.designation,
                        phones: employer.phones?.length ? employer.phones.map((entry) => entry.display) : (employer.display_phone_number ? [employer.display_phone_number] : []),
                        emails: employer.emails?.length ? employer.emails.map((entry) => entry.email) : (employer.employer_email ? [employer.employer_email] : []),
                        linkedin_url: employer.linkedin_url,
                        recruiter_verification_level: employer.recruiter_verification_level,
                        do_not_work_again: employer.do_not_work_again,
                        do_not_work_again_reason: employer.do_not_work_again_reason,
                      })
                      setEditingEmployer(true)
                    }}
                    disabled={busy}
                  >
                    Edit
                  </button>
                ) : null}
                </div>
              </div>
              {editingEmployer ? (
                <div className="detailFormGrid">
                  <label>Owner<input value={employerEdits.owner_name ?? ''} onChange={(event) => setEmployerEdits((value) => ({ ...value, owner_name: event.target.value }))} /></label>
                  <label>Company<input value={employerEdits.company ?? ''} onChange={(event) => setEmployerEdits((value) => ({ ...value, company: event.target.value }))} /></label>
                  <label>Sister company<input value={employerEdits.secondary_company ?? ''} onChange={(event) => setEmployerEdits((value) => ({ ...value, secondary_company: event.target.value }))} placeholder="Optional" /></label>
                  <label className="detailFormGridFullRow">Phone numbers<PhoneListEditor phones={employerEdits.phones ?? []} onChange={(next) => setEmployerEdits((value) => ({ ...value, phones: next }))} /></label>
                  <label className="detailFormGridFullRow">Email addresses<EmailListEditor emails={employerEdits.emails ?? []} onChange={(next) => setEmployerEdits((value) => ({ ...value, emails: next }))} /></label>
                  <label>Designation<input value={employerEdits.designation ?? ''} onChange={(event) => setEmployerEdits((value) => ({ ...value, designation: event.target.value }))} /></label>
                  <label>LinkedIn URL<input value={employerEdits.linkedin_url ?? ''} onChange={(event) => setEmployerEdits((value) => ({ ...value, linkedin_url: event.target.value }))} /></label>
                  <label>
                    Verification
                    <select value={employerEdits.recruiter_verification_level ?? 'unverified'} onChange={(event) => setEmployerEdits((value) => ({ ...value, recruiter_verification_level: event.target.value as EmployerNumberCard['recruiter_verification_level'] }))}>
                      <option value="unverified">Unverified</option><option value="verified">Verified</option><option value="trusted">Trusted</option>
                    </select>
                  </label>
                  <label className="checkboxLabel">
                    <input
                      type="checkbox"
                      checked={employerEdits.do_not_work_again ?? false}
                      onChange={(event) => {
                        const checked = event.target.checked
                        if (checked && !window.confirm('Mark this employer contact as "do not work with again"?\n\nIf this contact also has recruiter opportunities on file, those will be excluded from future resume-matching suggestions in Application Tracking.')) {
                          return
                        }
                        setEmployerEdits((value) => ({ ...value, do_not_work_again: checked }))
                      }}
                    />
                    Do not work with again
                  </label>
                  {employerEdits.do_not_work_again ? (
                    <label>Reason<input value={employerEdits.do_not_work_again_reason ?? ''} onChange={(event) => setEmployerEdits((value) => ({ ...value, do_not_work_again_reason: event.target.value }))} /></label>
                  ) : null}
                </div>
              ) : (
                <dl className="detailList">
                  <div><dt>Owner</dt><dd>{employer.owner_name}</dd></div>
                  <div><dt>Company</dt><dd>{employer.company}</dd></div>
                  {employer.secondary_company ? <div><dt>Sister company</dt><dd>{employer.secondary_company}</dd></div> : null}
                  <div><dt>Designation</dt><dd>{employer.designation}</dd></div>
                  <div><dt>Phone numbers</dt><dd><PhoneNumbersList phones={employer.phones} fallback={employer.display_phone_number} /></dd></div>
                  {employer.phones?.some((entry) => entry.label) ? <div><dt>Other numbers</dt><dd><OtherNumbersList phones={employer.phones} /></dd></div> : null}
                  <div><dt>Email addresses</dt><dd><EmailsList emails={employer.emails} fallback={employer.employer_email} /></dd></div>
                  <div><dt>LinkedIn</dt><dd>{employer.linkedin_url ? <a href={externalUrl(employer.linkedin_url)} target="_blank" rel="noreferrer">{employer.linkedin_url}</a> : '--'}</dd></div>
                  <div><dt>Verification</dt><dd>{employer.recruiter_verification_level}</dd></div>
                  <div><dt>Do not work again</dt><dd>{employer.do_not_work_again ? employer.do_not_work_again_reason || 'Yes' : 'No'}</dd></div>
                  <div><dt>Seen</dt><dd>{employer.seen_count} times</dd></div>
                </dl>
              )}
              {editingEmployer ? (
                <div className="detailEditActions">
                  <button
                    type="button"
                    className="primaryButton"
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
                <button
                  type="button"
                  className="dangerButton"
                  disabled={versionsLoading || busy || deletingOlderVersions || versions.length <= 1}
                  onClick={() => {
                    if (!window.confirm(`Delete all ${versions.length - 1} older version(s)? This keeps only the current active version and cannot be undone.`)) return
                    setDeletingOlderVersions(true)
                    deleteOlderContactVersions(apiBase, primaryRole, row.id)
                      .then((result) => {
                        onToast(`Deleted ${result.deleted_count} older version(s)`)
                        return onReload()
                      })
                      .catch((reason) => onError((reason as Error).message))
                      .finally(() => setDeletingOlderVersions(false))
                  }}
                >
                  {deletingOlderVersions ? 'Deleting older versions...' : 'Delete older versions'}
                </button>
              </div>
              {primaryContact.source_link_url ? <a href={primaryContact.source_link_url} target="_blank" rel="noreferrer">{primaryContact.source_type === 'nvoids' ? 'Open original post' : 'Open exact email in Gmail'}</a> : null}
            </section>
          ) : null}
        </div>

        <footer className="detailPanelActions">
          {conflictUnresolved ? <p className="subtle">Resolve the identity conflict above before marking this contact.</p> : null}
          <button type="button" onClick={() => act('mark-recruiter', reviewEdits)} disabled={busy || conflictUnresolved}>{pendingAction === 'mark-recruiter' ? 'Working...' : 'Mark as Recruiter'}</button>
          <button type="button" onClick={() => act('mark-employer', reviewEdits)} disabled={busy || conflictUnresolved}>{pendingAction === 'mark-employer' ? 'Working...' : 'Mark as Employer'}</button>
          {row.kind === 'contact' ? (
            <button type="button" onClick={runRescore} disabled={busy || rescoring}>{rescoring ? 'Working...' : 'Rescore'}</button>
          ) : (
            <button type="button" onClick={() => act('rescore')} disabled={busy}>{pendingAction === 'rescore' ? 'Working...' : 'Rescore'}</button>
          )}
          <button type="button" className="dangerButton" onClick={() => act('delete')} disabled={busy}>{pendingAction === 'delete' ? 'Deleting...' : 'Delete Entire Contact'}</button>
        </footer>
      </div>
      {rescoreDiff ? (
        <RescoreDiffModal
          changes={rescoreDiff}
          undoing={undoingRescore}
          onKeep={() => setRescoreDiff(null)}
          onUndo={undoRescore}
        />
      ) : null}
    </div>
  )
}
