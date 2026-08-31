import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

import { getContactMergePreview, mergeContacts } from './api'
import type { ContactMergePreviewResponse, ContactMergePreviewSide } from './types'

type MergeContactsModalProps = {
  apiBase: string
  contactIdA: number
  contactIdB: number
  onClose: () => void
  onMerged: () => void
  onError: (message: string) => void
}

function formatEvidenceAt(value: string | null): string {
  if (!value) return '--'
  return new Date(value).toLocaleString()
}

function displayName(side: ContactMergePreviewSide): string {
  if (side.recruiter_name && side.recruiter_name !== 'Unknown') return side.recruiter_name
  return side.owner_name || 'Unknown'
}

function SideCard({ side, selected, onSelect }: { side: ContactMergePreviewSide; selected: boolean; onSelect: () => void }) {
  return (
    <div className={`mergePreviewCard${selected ? ' mergePreviewCard--selected' : ''}`}>
      <dl className="detailList">
        <div><dt>Name</dt><dd>{displayName(side)}</dd></div>
        <div><dt>Company</dt><dd>{side.company}</dd></div>
        {side.secondary_company ? <div><dt>Sister company</dt><dd>{side.secondary_company}</dd></div> : null}
        <div><dt>Phone numbers</dt><dd>{side.phones?.length ? side.phones.map((entry) => <div key={`${entry.phone}-${entry.extension}`}>{entry.display}{entry.label ? ` (${entry.label})` : ''}{entry.is_primary ? ' (Primary)' : ''}</div>) : side.display_phone_number || '--'}</dd></div>
        <div><dt>Email addresses</dt><dd>{side.emails?.length ? side.emails.map((entry) => <div key={entry.email}>{entry.email}{entry.is_primary ? ' (Primary)' : ''}</div>) : side.recruiter_email || side.employer_email || '--'}</dd></div>
        <div><dt>Roles</dt><dd>{[side.is_recruiter ? 'Recruiter' : null, side.is_employer ? 'Employer' : null].filter(Boolean).join(', ') || '--'}</dd></div>
        <div><dt>Versions on file</dt><dd>{side.lead_count}</dd></div>
        <div><dt>Latest evidence</dt><dd>{formatEvidenceAt(side.latest_evidence_at)}</dd></div>
      </dl>
      <button type="button" onClick={onSelect} disabled={selected}>
        {selected ? 'Keeping this one' : 'Keep this one'}
      </button>
    </div>
  )
}

export default function MergeContactsModal({ apiBase, contactIdA, contactIdB, onClose, onMerged, onError }: MergeContactsModalProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const [preview, setPreview] = useState<ContactMergePreviewResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [canonicalId, setCanonicalId] = useState<number | null>(null)
  const [merging, setMerging] = useState(false)

  useEffect(() => {
    getContactMergePreview(apiBase, contactIdA, contactIdB)
      .then(setPreview)
      .catch((reason) => { onError((reason as Error).message); onClose() })
      .finally(() => setLoading(false))
    // Only load once per pair of contacts - onClose/onError identity isn't part of the query key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, contactIdA, contactIdB])

  useEffect(() => { panelRef.current?.focus() }, [])

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

  const confirm = () => {
    if (!canonicalId || !preview) return
    const loserId = canonicalId === preview.contact_a.id ? preview.contact_b.id : preview.contact_a.id
    setMerging(true)
    mergeContacts(apiBase, canonicalId, loserId)
      .then(() => { onMerged(); onClose() })
      .catch((reason) => onError((reason as Error).message))
      .finally(() => setMerging(false))
  }

  return (
    <div className="detailPanelOverlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div ref={panelRef} className="detailPanel" role="dialog" aria-modal="true" aria-label="Merge contacts" tabIndex={-1} onKeyDown={handleKeyDown}>
        <header className="detailPanelHeader">
          <div>
            <p className="detailPanelEyebrow">Number inventory</p>
            <h3>Merge Duplicate Contacts</h3>
          </div>
          <button type="button" className="iconBtn" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <div className="detailPanelBody">
          {loading ? <p className="subtle">Loading comparison...</p> : null}
          {preview ? (
            <>
              <p className="subtle">
                Pick which contact survives — its identity stays, and everything below gets moved onto it.
                The other one is soft-deleted (restorable from the Recycle Bin).
              </p>
              <div className="mergePreviewGrid">
                <SideCard side={preview.contact_a} selected={canonicalId === preview.contact_a.id} onSelect={() => setCanonicalId(preview.contact_a.id)} />
                <SideCard side={preview.contact_b} selected={canonicalId === preview.contact_b.id} onSelect={() => setCanonicalId(preview.contact_b.id)} />
              </div>
              <div className="detailEditActions">
                <button type="button" className="primaryButton" onClick={confirm} disabled={!canonicalId || merging}>
                  {merging ? 'Merging...' : canonicalId ? `Confirm — keep #${canonicalId}, merge the other in` : 'Pick a survivor above'}
                </button>
                <button type="button" onClick={onClose} disabled={merging}>Cancel</button>
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}
