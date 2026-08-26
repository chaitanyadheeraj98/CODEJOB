import { useEffect, useRef, useState } from 'react'
import FilterSortBar, { type FilterValues } from '../../components/FilterSortBar'
import { opportunityDefaultFilterValues, opportunityFilterFields, opportunityFiltersToParams, opportunitySortOptions } from './opportunityFilters'

import {
  createApplication,
  deleteOpportunity,
  generateColdCallScript,
  listOpportunityPage,
  listResumeOptions,
  matchOpportunitiesForResume,
  refreshOpportunityAiMetadata,
  updateOpportunity,
} from './api'
import type { OpportunityMatch, OpportunityStatus, RecruiterOpportunityCard, ResumeAssetOption } from './types'

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
  applicationsEnabled: boolean
  onToast: (message: string) => void
}

export default function OpportunitiesTab({ apiBase, mailDate, refreshToken, highlightedId, applicationsEnabled, onToast }: OpportunitiesTabProps) {
  const [rows, setRows] = useState<RecruiterOpportunityCard[]>([])
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<'all' | OpportunityStatus>('all')
  const [source, setSource] = useState<'all' | 'gmail' | 'nvoids'>('all')
  const [page, setPage] = useState(1)
  const [total,setTotal]=useState(0)
  const [filterValues,setFilterValues]=useState<FilterValues>(opportunityDefaultFilterValues)
  const [sort,setSort]=useState('newest')
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [edits, setEdits] = useState<Record<number, Partial<RecruiterOpportunityCard>>>({})
  const [trackingId, setTrackingId] = useState<number | null>(null)
  const [resumeOptions, setResumeOptions] = useState<ResumeAssetOption[]>([])
  const [selectedResumeId, setSelectedResumeId] = useState<number | null>(null)
  const [trackingDedupeKey, setTrackingDedupeKey] = useState('')
  const [sortByMatch, setSortByMatch] = useState(false)
  const [matchesByOpportunity, setMatchesByOpportunity] = useState<Record<number, OpportunityMatch>>({})
  const requestIdRef = useRef(0)

  const load = () => {
    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    setLoading(true)
    setError('')
    const request = sortByMatch && selectedResumeId != null
      ? matchOpportunitiesForResume(apiBase, selectedResumeId).then((matches) => {
          setMatchesByOpportunity(Object.fromEntries(matches.map((match) => [match.opportunity.id, match])) as Record<number, OpportunityMatch>)
          return matches.map((match) => match.opportunity)
        })
      : listOpportunityPage({ apiBase, cursor:(page-1)*PAGE_SIZE,limit:PAGE_SIZE,q:search,status,sourceType:source,mailDate,sort,filters:opportunityFiltersToParams(filterValues) }).then((payload) => {
          setMatchesByOpportunity({})
          setTotal(payload.total)
          return payload.items
        })
    return request
      .then((items) => {
        if (requestId !== requestIdRef.current) return
        setRows(items)
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
  }, [apiBase, filterValues, mailDate, page, refreshToken, search, selectedResumeId, sort, sortByMatch, source, status])

  useEffect(() => {
    if (!applicationsEnabled) return
    listResumeOptions(apiBase)
      .then((items) => {
        const enabled = items.filter((resume) => resume.is_enabled)
        setResumeOptions(enabled)
        setSelectedResumeId((current) => current ?? (enabled.find((resume) => resume.is_current) ?? enabled[0])?.id ?? null)
      })
      .catch((reason) => setError((reason as Error).message))
  }, [apiBase, applicationsEnabled])

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

  const totalPages = Math.max(1, Math.ceil((sortByMatch ? rows.length : total) / PAGE_SIZE))
  const visible = sortByMatch ? rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE) : rows

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

  const openResumePicker = async (opportunityId: number) => {
    setTrackingId(opportunityId)
    setTrackingDedupeKey(crypto.randomUUID())
    setBusyId(opportunityId)
    setError('')
    try {
      const enabled = (await listResumeOptions(apiBase)).filter((resume) => resume.is_enabled)
      setResumeOptions(enabled)
      setSelectedResumeId((enabled.find((resume) => resume.is_current) ?? enabled[0])?.id ?? null)
    } catch (reason) {
      setError((reason as Error).message)
      setTrackingId(null)
    } finally {
      setBusyId(null)
    }
  }

  const trackApplication = async (item: RecruiterOpportunityCard) => {
    if (selectedResumeId == null) return
    setBusyId(item.id)
    setError('')
    try {
      await createApplication(apiBase, {
        resume_asset_id: selectedResumeId,
        recruiter_opportunity_id: item.id,
        dedupe_key: trackingDedupeKey,
      })
      setTrackingId(null)
      onToast('Application tracking started')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="opportunitiesTab">
      <FilterSortBar fields={opportunityFilterFields} values={filterValues} onFieldChange={(key,value)=>{setFilterValues((current)=>({...current,[key]:value}));setPage(1)}} onClear={()=>{setFilterValues(opportunityDefaultFilterValues);setPage(1)}} sortOptions={opportunitySortOptions} sortValue={sort} onSortChange={(value)=>{setSort(value);setPage(1)}} loading={loading} disabled={sortByMatch} disabledMessage="Turn off resume-match sorting to filter opportunities." />
      <div className="inventoryToolbar opportunitiesToolbar">
        <label className="inventorySearchField">
          <span>Search opportunities</span>
          <input value={search} onChange={(event) => { setSearch(event.target.value); setPage(1) }} placeholder="Search role, recruiter, client..." disabled={sortByMatch} />
        </label>
        <label><span>Status</span><select value={status} onChange={(event) => setStatus(event.target.value as 'all' | OpportunityStatus)} disabled={sortByMatch}><option value="all">All statuses</option>{STATUSES.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label><span>Source</span><select value={source} onChange={(event) => setSource(event.target.value as 'all' | 'gmail' | 'nvoids')} disabled={sortByMatch}><option value="all">All sources</option><option value="gmail">Gmail</option><option value="nvoids">Nvoids</option></select></label>
        {applicationsEnabled ? (
          <>
            <label>
              <span>Best matches for resume</span>
              <select value={selectedResumeId ?? ''} onChange={(event) => setSelectedResumeId(Number(event.target.value))}>
                {resumeOptions.map((resume) => <option key={resume.id} value={resume.id}>{resume.file_name} (v{resume.version})</option>)}
              </select>
            </label>
            <label className="checkboxLabel matchSortToggle">
              <input type="checkbox" checked={sortByMatch} onChange={(event) => setSortByMatch(event.target.checked)} disabled={selectedResumeId == null} />
              Sort by resume fit
            </label>
          </>
        ) : null}
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
            {matchesByOpportunity[item.id] ? (
              <div className="opportunityMatchEvidence">
                <strong>{matchesByOpportunity[item.id].score}% match</strong>
                <ul>{matchesByOpportunity[item.id].reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
              </div>
            ) : null}
            <label>Location<input value={String(edits[item.id]?.location ?? item.location ?? '')} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], location: event.target.value } }))} /></label>
            <div className="opportunitySummary">
              <p><strong>Recruiter:</strong> {item.recruiter_name || '--'}</p>
              <p><strong>Company:</strong> {item.recruiter_company || '--'}</p>
              <p><strong>Email:</strong> {item.recruiter_email || '--'}</p>
              <p><strong>Email Sender:</strong> {item.email_sender || '--'}</p>
              <p><strong>Phone:</strong> {item.recruiter_phone_display || '--'}</p>
              <p><strong>Subject:</strong> {item.email_subject || '--'}</p>
              <p><strong>Record ID:</strong> {item.record_id ?? '--'}</p>
            </div>
            <div className="detailFormGrid">
              {EDITABLE_FIELDS.map(([field, label]) => (
                <label key={field}>{label}<input value={String(edits[item.id]?.[field] ?? item[field] ?? '')} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], [field]: event.target.value } }))} /></label>
              ))}
            </div>
            <details className="opportunityJobDetails">
              <summary>Job details</summary>
              <div className="detailFormGrid">
                <label>Employment type<input value={String(edits[item.id]?.employment_type ?? item.employment_type ?? '')} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], employment_type: event.target.value } }))} /></label>
                <label>Rate amount<input type="number" min={0} step="any" value={edits[item.id]?.rate_amount === undefined ? (item.rate_amount ?? '') : (edits[item.id]?.rate_amount ?? '')} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], rate_amount: event.target.value ? Number(event.target.value) : null } }))} /></label>
                <label>Currency<input value={String(edits[item.id]?.rate_currency ?? item.rate_currency ?? 'USD')} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], rate_currency: event.target.value } }))} /></label>
                <label>Rate unit<input value={String(edits[item.id]?.rate_unit ?? item.rate_unit ?? '')} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], rate_unit: event.target.value } }))} placeholder="hour, day, year" /></label>
                <label>Contract duration<input value={String(edits[item.id]?.contract_duration ?? item.contract_duration ?? '')} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], contract_duration: event.target.value } }))} /></label>
                <label>
                  Relocation required
                  <select
                    value={String(edits[item.id]?.relocation_required === undefined ? (item.relocation_required ?? '') : (edits[item.id]?.relocation_required ?? ''))}
                    onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], relocation_required: event.target.value === '' ? null : event.target.value === 'true' } }))}
                  >
                    <option value="">Unknown</option>
                    <option value="false">No</option>
                    <option value="true">Yes</option>
                  </select>
                </label>
                <label>
                  Extension likely
                  <select value={String(edits[item.id]?.extension_likely ?? item.extension_likely ?? 'unknown')} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], extension_likely: event.target.value } }))}>
                    <option value="unknown">Unknown</option><option value="yes">Yes</option><option value="no">No</option>
                  </select>
                </label>
                <label>
                  Job confidence
                  <select value={String(edits[item.id]?.job_confidence ?? item.job_confidence ?? 'unknown')} onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], job_confidence: event.target.value } }))}>
                    <option value="unknown">Unknown</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option>
                  </select>
                </label>
                <label className="checkboxLabel">
                  <input
                    type="checkbox"
                    checked={edits[item.id]?.end_client_confirmed ?? item.end_client_confirmed}
                    onChange={(event) => setEdits((current) => ({ ...current, [item.id]: { ...current[item.id], end_client_confirmed: event.target.checked } }))}
                  />
                  End client confirmed
                </label>
              </div>
            </details>
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
              {applicationsEnabled ? <button type="button" onClick={() => openResumePicker(item.id)} disabled={busyId === item.id}>Track Application</button> : null}
              <button type="button" className="dangerButton" onClick={() => remove(item.id)} disabled={busyId === item.id}>{busyId === item.id ? 'Working...' : 'Delete'}</button>
              {item.cold_call_script ? <button type="button" onClick={() => navigator.clipboard.writeText(item.cold_call_script || '').then(() => onToast('Copied')).catch(() => setError('Failed to copy cold call script'))}>Copy Script</button> : null}
            </div>
            {trackingId === item.id ? (
              <div className="resumeLockPicker">
                <label>
                  Resume version
                  <select
                    aria-label={`Resume for ${item.job_title}`}
                    value={selectedResumeId ?? ''}
                    onChange={(event) => setSelectedResumeId(Number(event.target.value))}
                  >
                    {resumeOptions.map((resume) => (
                      <option key={resume.id} value={resume.id}>{resume.file_name} (v{resume.version})</option>
                    ))}
                  </select>
                </label>
                {selectedResumeId == null ? (
                  <p className="subtle">Enable a resume in Settings before tracking this opportunity.</p>
                ) : (
                  <p className="subtle">
                    Confirming locks this resume version and the current recruiter/job details into the application history.
                  </p>
                )}
                <div className="rowBtns">
                  <button type="button" onClick={() => trackApplication(item)} disabled={busyId === item.id || selectedResumeId == null}>Confirm &amp; Track</button>
                  <button type="button" onClick={() => setTrackingId(null)} disabled={busyId === item.id}>Cancel</button>
                </div>
              </div>
            ) : null}
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
