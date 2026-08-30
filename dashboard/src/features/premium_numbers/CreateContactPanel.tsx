import { useState, type FormEvent } from 'react'

import { createManualContact } from './api'
import type { ManualContactPayload, ManualContactResult } from './types'

type CreateContactPanelProps = {
  apiBase: string
  onClose: () => void
  onCreated: (result: ManualContactResult) => void
  onError: (message: string) => void
}

const STATUS_MESSAGES: Record<ManualContactResult['status'], string> = {
  created: 'Contact created.',
  confirmed: 'Matched an existing contact on file — its details were updated.',
  pending_merge_approval: 'Phone and email match two different existing contacts. Flagged as a Pending Review in Number Inventory so you can merge or dismiss it.',
  pending_link_approval: 'Matches an existing contact with different details. Flagged as a Pending Review in Number Inventory to confirm.',
}

function parseErrorMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)
  try {
    const parsed = JSON.parse(raw) as { detail?: string }
    if (typeof parsed.detail === 'string' && parsed.detail) return parsed.detail
  } catch {
    // raw response wasn't JSON - fall through to showing it as-is
  }
  return raw
}

export default function CreateContactPanel({ apiBase, onClose, onCreated, onError }: CreateContactPanelProps) {
  const [name, setName] = useState('')
  const [title, setTitle] = useState('')
  const [company, setCompany] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [role, setRole] = useState<'recruiter' | 'employer'>('recruiter')
  const [saving, setSaving] = useState(false)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!name.trim()) {
      onError('Name is required.')
      return
    }
    if (!phone.trim() && !email.trim()) {
      onError('Provide a phone number, an email address, or both.')
      return
    }
    const payload: ManualContactPayload = {
      name: name.trim(),
      title: title.trim(),
      company: company.trim(),
      email: email.trim(),
      phone: phone.trim(),
      role,
    }
    setSaving(true)
    createManualContact(apiBase, payload)
      .then((result) => onCreated(result))
      .catch((reason) => onError(parseErrorMessage(reason)))
      .finally(() => setSaving(false))
  }

  return (
    <div className="detailPanelOverlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="detailPanel" role="dialog" aria-modal="true" aria-label="Create contact">
        <header className="detailPanelHeader">
          <div>
            <p className="detailPanelEyebrow">Number inventory</p>
            <h3>Create Contact</h3>
          </div>
          <button type="button" className="iconBtn" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <form className="detailPanelBody" onSubmit={submit}>
          <section className="detailSection">
            <h4>Contact details</h4>
            <p className="subtle">Enter a phone number, an email address, or both.</p>
            <div className="detailFormGrid">
              <label>Name<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
              <label>
                Role
                <select value={role} onChange={(event) => setRole(event.target.value as 'recruiter' | 'employer')}>
                  <option value="recruiter">Recruiter</option>
                  <option value="employer">Employer</option>
                </select>
              </label>
              <label>Company<input value={company} onChange={(event) => setCompany(event.target.value)} /></label>
              <label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
              <label>Phone<input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="(555) 555-5555" /></label>
              <label>Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@company.com" /></label>
            </div>
          </section>
          <div className="detailEditActions">
            <button type="submit" disabled={saving}>{saving ? 'Saving...' : 'Create Contact'}</button>
            <button type="button" onClick={onClose} disabled={saving}>Cancel</button>
          </div>
        </form>
      </div>
    </div>
  )
}

export { STATUS_MESSAGES }
