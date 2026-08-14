import { useEffect, useId, useState } from 'react'

import {
  fetchEmailSearch,
  isEmailSearchQueryValid,
  type EmailSearchHit,
  type EmailSearchResponse,
  type EmailSearchSection,
} from '../../emailSearch'

const SECTION_LABELS: Record<EmailSearchSection, string> = {
  needs_review: 'Needs Review',
  failed_mapping: 'Failed Mapping',
  sent_items: 'Sent Items',
  premium_numbers: 'Premium Numbers',
  inbox: 'Inbox',
  recent_runs: 'Recent Runs',
  other: 'Other',
}

type EmailSearchProps = {
  apiBase: string
  onNavigate: (hit: EmailSearchHit) => void
  fetchImpl?: typeof fetch
  debounceMs?: number
  currentSection?: string
}

function hitKey(hit: EmailSearchHit, index: number): string {
  const relatedId =
    hit.detail.conversation_id ??
    hit.detail.premium_number_lead_id ??
    hit.detail.recent_run_skipped_item_id ??
    hit.detail.recent_run_id ??
    hit.detail.run_key ??
    index
  return `${hit.section}-${hit.recruiter_email_id ?? 'none'}-${String(relatedId)}`
}

export default function EmailSearch({
  apiBase,
  onNavigate,
  fetchImpl,
  debounceMs = 300,
  currentSection,
}: EmailSearchProps) {
  const resultsId = useId()
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<EmailSearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const normalized = query.trim()
    if (!isEmailSearchQueryValid(normalized)) return

    const controller = new AbortController()
    const timerId = window.setTimeout(() => {
      setLoading(true)
      setError('')
      fetchEmailSearch(apiBase, normalized, fetchImpl, controller.signal, currentSection)
        .then((payload) => {
          if (controller.signal.aborted) return
          setResult(payload)
          setOpen(true)
        })
        .catch((requestError: unknown) => {
          if (controller.signal.aborted) return
          setResult(null)
          setError((requestError as Error).message)
          setOpen(true)
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false)
        })
    }, debounceMs)

    return () => {
      window.clearTimeout(timerId)
      controller.abort()
    }
  }, [apiBase, currentSection, debounceMs, fetchImpl, query])

  const showPanel = open && isEmailSearchQueryValid(query)

  return (
    <div className="emailSearch">
      <label htmlFor={`${resultsId}-input`} className="visuallyHidden">Find a stored email or Email ID</label>
      <input
        id={`${resultsId}-input`}
        className="search emailSearchInput"
        type="search"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value)
          setResult(null)
          setLoading(false)
          setError('')
          setOpen(false)
        }}
        onFocus={() => {
          if (result || error || loading) setOpen(true)
        }}
        onBlur={() => setOpen(false)}
        placeholder="Find email or Email ID..."
        autoComplete="off"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={showPanel}
        aria-controls={resultsId}
      />
      {showPanel ? (
        <div id={resultsId} className="emailSearchPanel" role="listbox" aria-label="Email search results">
          {loading ? <p className="emailSearchStatus">Searching...</p> : null}
          {error ? <p className="emailSearchStatus errorMessage">{error}</p> : null}
          {!loading && !error && result?.hits.length === 0 ? (
            <p className="emailSearchStatus">No stored records found.</p>
          ) : null}
          {!loading && !error ? result?.hits.map((hit, index) => {
            const content = (
              <>
                <span className="emailSearchResultTopline">
                  <span className={`emailSearchSectionBadge section-${hit.section}`}>{SECTION_LABELS[hit.section]}</span>
                  <span className="emailSearchResultState">{hit.state}</span>
                </span>
                <strong>{hit.sender || 'Unknown sender'}</strong>
                <span>{hit.subject || 'No subject'}</span>
                <small>{hit.recruiter_email_id == null ? 'No Email ID' : `Email ID: ${hit.recruiter_email_id}`}</small>
              </>
            )
            return hit.section === 'other' ? (
              <div key={hitKey(hit, index)} className="emailSearchResult emailSearchResultStatic" role="option" aria-selected="false">
                {content}
              </div>
            ) : (
              <button
                key={hitKey(hit, index)}
                type="button"
                className="emailSearchResult"
                role="option"
                aria-selected="false"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  onNavigate(hit)
                  setOpen(false)
                }}
              >
                {content}
              </button>
            )
          }) : null}
          {!loading && !error && result?.truncated ? (
            <p className="emailSearchStatus">Showing the first 200 matches. Refine your query for a shorter list.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
