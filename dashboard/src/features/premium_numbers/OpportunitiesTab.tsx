import { useEffect, useMemo, useRef, useState } from 'react'

import { deleteOpportunity, generateColdCallScript, listOpportunities, refreshOpportunityAiMetadata, updateOpportunity } from './api'
import type { OpportunityStatus, RecruiterOpportunityCard } from './types'

const PAGE_SIZE = 10
const STATUSES: OpportunityStatus[] = ['New', 'Called', 'Applied', 'Follow Up', 'Closed', 'Not Interested']
const EDITABLE_FIELDS = [
  ['job_title', 'Job Title'],
  ['work_mode', 'Work Mode'],
  ['visa_restrictions', 'Visa'],
  ['resume_file_name', 'Resume Variant Submitted'],
  ['implementation_partner', 'Implementation Partner'],
  ['prime_vendor', 'Prime Vendor'],
  ['end_client', 'End Client'],
  ['domain', 'Domain'],
  ['extracted_skills', 'Skills'],
] as const

type OpportunitiesTabProps = {
  apiBase: string
  mailDate: string | null
  refreshToken: number
  highlightedId: number | null
  onToast: (message: string) => void
}

export default function OpportunitiesTab({ apiBase, mailDate, refreshToken, highlightedId, onToast }: OpportunitiesTabProps) {
  const [rows, setRows] = useState<RecruiterOpportunityCard[]>([])
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<'all' | OpportunityStatus>('all')
  const [source, setSource] = useState<'all' | 'gmail' | 'nvoids'>('all')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [edits, setEdits] = useState<Record<number, Partial<RecruiterOpportunityCard>>>({})
  const requestIdRef = useRef(0)

  const load = () => {
    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    setLoading(true)
    setError('')
    return listOpportunities({ apiBase, q: search, status, sourceType: source, mailDate })
      .then((items) => {
        if (requestId !== requestIdRef.current) return
        setRows(items)
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
  }, [apiBase, mailDate, refreshToken, search, source, status])

  useEffect(() => {
    if (highlightedId == null) return
    const index = rows.findIndex((row) => row.id === highlightedId)
    if (index < 0) return
    window.setTimeout(() => {
      setPage(Math.floor(index / PAGE_SIZE) + 1)
      window.setTimeout(() => {
        document.querySelector<HTMLElement>(`[data-opportunity-id="${highlightedId}"]`)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
      }, 0)
    }, 0)
  }, [highlightedId, rows])

  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const visible = useMemo(() => rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [page, rows])

  const patchRow = async (id: number, patch: Partial<RecruiterOpportunityCard>) => {
    setBusyId(id)
    setError('')
    try {
      const updated = await updateOpportunity(apiBase, id, patch)
      setRows((current) => current.map((row) => row.id === id ? updated : row))
      setEdits((current) => {
        const next = { ...current }
        delete next[id]
        return next
      })
      onToast('Saved')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const generate = async (id: number) => {
    setBusyId(id)
    try {
      const updated = await generateColdCallScript(apiBase, id)
      setRows((current) => current.map((row) => row.id === id ? updated : row))
      onToast('Cold call script generated')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const refreshAiMetadata = async (id: number) => {
    setBusyId(id)
    try {
      const updated = await refreshOpportunityAiMetadata(apiBase, id)
      setRows((current) => current.map((row) => row.id === id ? updated : row))
      onToast('AI metadata refreshed & saved')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (id: number) => {
    if (!window.confirm('Delete this opportunity? This cannot be undone.')) return
    setBusyId(id)
    try {
      await deleteOpportunity(apiBase, id)
      setRows((current) => current.filter((row) => row.id !== id))
      onToast('Deleted')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="opportunitiesTab">
      <div className="inventoryToolbar opportunitiesToolbar">
        <label className="inventorySearchField">
          <span>Search opportunities</span>
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search role, recruiter, client..." />
        </label>
        <label><span>Status</span><select value={status} onChange={(event) => setStatus(event.target.value as 'all' | OpportunityStatus)}><option value="all">All statuses</option>{STATUSES.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label><span>Source</span><select value={source} onChange={(event) => setSource(event.target.value as 'all' | 'gmail' | 'nvoids')}><option value="all">All sources</option><option value="gmail">Gmail</option><option value="nvoids">Nvoids</option></select></label>
      </div>
      {loading ? <p className="subtle">Loading recruiter opportunities...</p> : null}
      {error ? <p className="errorBanner">Recruiter opportunities error: {error}</p> : null}
      {!loading && rows.length === 0 ? <p className="inventoryEmpty">No recruiter opportunities match these filters.</p> : null}
      <div className="opportunityGrid">
        {visible.map((item) => (
          <article
            key={item.id}
            className={`opportunityCard ${highlightedId === item.id ? 'emailSearchHighlight' : ''}`}
            data-email-search-section="premium_numbers"
            data-email-search-related-id={item.id}
            data-opportunity-id={item.id}
          >
            <header>
              <div><span className="categoryChip">{item.source_type.toUpperCase()}</span><h3>{item.job_title || 'Recruiter opportunity'}</h3></div>
              <select value={item.status} aria-label={`Status for ${item.job_title}`} onChange={(event) => patchRow(item.id, { status: event.target.value as OpportunityStatus })} disabled={busyId === item.id}>{STATUSES.map((value) => <option key={value} value={value}>{value}</option>)}</select>
            </header>
            <label>Location<input value={String(edits[item.id]?.location ?? item.location ?? '')} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], location: event.target.value } }))} /></label>
            <div className="opportunitySummary">
              <p><strong>Recruiter:</strong> {item.recruiter_name || '--'}</p>
              <p><strong>Company:</strong> {item.recruiter_company || '--'}</p>
              <p><strong>Email:</strong> {item.recruiter_email || '--'}</p>
              <p><strong>Email Sender:</strong> {item.email_sender || '--'}</p>
              <p><strong>Phone:</strong> {item.recruiter_phone_display || '--'}</p>
              <p><strong>Subject:</strong> {item.email_subject || '--'}</p>
              <p><strong>Email ID:</strong> {item.email_id ?? '--'}</p>
            </div>
            <div className="detailFormGrid">
              {EDITABLE_FIELDS.map(([field, label]) => (
                <label key={field}>{label}<input value={String(edits[item.id]?.[field] ?? item[field] ?? '')} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], [field]: event.target.value } }))} /></label>
              ))}
            </div>
            {item.source_url || item.gmail_open_url ? <a href={item.source_url || item.gmail_open_url} target="_blank" rel="noreferrer">{item.source_type === 'nvoids' ? 'Open original post' : 'Open exact email in Gmail'}</a> : null}
            {item.linkedin_url ? <a href={item.linkedin_url} target="_blank" rel="noreferrer">LinkedIn profile</a> : null}
            <label>Notes<textarea rows={3} value={item.notes || ''} onChange={(event) => setRows((current) => current.map((row) => row.id === item.id ? { ...row, notes: event.target.value } : row))} onBlur={(event) => patchRow(item.id, { notes: event.target.value })} /></label>
            <div className="rowBtns">
              <button
                type="button"
                onClick={() => { if (window.confirm('Save changes to this opportunity?')) patchRow(item.id, edits[item.id] ?? {}) }}
                disabled={busyId === item.id || !edits[item.id]}
              >
                {busyId === item.id ? 'Working...' : 'Save'}
              </button>
              <button type="button" onClick={() => generate(item.id)} disabled={busyId === item.id}>{busyId === item.id ? 'Working...' : 'Generate Cold Call Script'}</button>
              <button type="button" onClick={() => refreshAiMetadata(item.id)} disabled={busyId === item.id}>{busyId === item.id ? 'Working...' : 'Refresh AI Metadata'}</button>
              <button type="button" className="dangerButton" onClick={() => remove(item.id)} disabled={busyId === item.id}>{busyId === item.id ? 'Working...' : 'Delete'}</button>
              {item.cold_call_script ? <button type="button" onClick={() => navigator.clipboard.writeText(item.cold_call_script || '').then(() => onToast('Copied')).catch(() => setError('Failed to copy cold call script'))}>Copy Script</button> : null}
            </div>
            {item.cold_call_script ? <label>Cold Call Script<textarea rows={4} value={item.cold_call_script} readOnly /></label> : null}
          </article>
        ))}
      </div>
      {rows.length > 0 ? (
        <footer className="inventoryPaginationFooter">
          <span>Showing {(page - 1) * PAGE_SIZE + 1} to {Math.min(page * PAGE_SIZE, rows.length)} of {rows.length} loaded entries</span>
          <nav className="pagination" aria-label="Opportunity pages">
            <button type="button" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={page === 1}>‹</button>
            <span>Page {page} of {totalPages}</span>
            <button type="button" onClick={() => setPage((value) => Math.min(totalPages, value + 1))} disabled={page === totalPages}>›</button>
          </nav>
        </footer>
      ) : null}
    </div>
  )
}
