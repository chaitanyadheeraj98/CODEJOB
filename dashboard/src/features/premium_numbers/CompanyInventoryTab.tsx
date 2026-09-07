import { useEffect, useRef, useState } from 'react'

import type { FilterValues } from '../../components/FilterSortBar'
import { getCompanyDetail, listCompanies } from './api'
import { companyFiltersToParams } from './companyFilters'
import CompanyDetailPanel from './CompanyDetailPanel'
import type { ToastTone } from './Toast'
import type { CompanyCard, CompanyDetail, InventoryRow } from './types'

const PAGE_SIZE = 10

type CompanyInventoryTabProps = {
  apiBase: string
  refreshToken: number
  onToast: (message: string, tone?: ToastTone) => void
  onOpenContact: (company: CompanyCard, contact: InventoryRow) => void
  onOpenCompany: (company: CompanyCard) => void
  filterValues?: FilterValues
  sortValue?: string
}

function formatRelativeDate(value: string): string {
  const timestamp = new Date(value).getTime()
  return Number.isFinite(timestamp) ? new Date(value).toLocaleDateString() : '--'
}

export default function CompanyInventoryTab({
  apiBase,
  refreshToken,
  onToast,
  onOpenContact,
  onOpenCompany,
  filterValues = {},
  sortValue = 'newest',
}: CompanyInventoryTabProps) {
  const [rows, setRows] = useState<CompanyCard[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  // Starts true for the same reason the Recycle Bin's does: a fetch is always
  // scheduled on mount, so the first paint must not claim there are no companies.
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<CompanyCard | null>(null)
  const [detail, setDetail] = useState<CompanyDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const returnFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => { setPage(1) }, [filterValues, sortValue])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLoading(true)
      listCompanies(apiBase, {
        cursor: String((page - 1) * PAGE_SIZE),
        limit: String(PAGE_SIZE),
        sort: sortValue,
        ...companyFiltersToParams(filterValues),
      })
        .then((payload) => { setRows(payload.items); setTotal(payload.total) })
        .catch((reason) => onToast((reason as Error).message, 'error'))
        .finally(() => setLoading(false))
    }, 150)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, filterValues, page, refreshToken, sortValue])

  const openCompany = (company: CompanyCard, trigger: HTMLElement) => {
    returnFocusRef.current = trigger
    setSelected(company)
    setDetail(null)
    setDetailLoading(true)
    getCompanyDetail(apiBase, company)
      .then(setDetail)
      .catch((reason) => onToast((reason as Error).message, 'error'))
      .finally(() => setDetailLoading(false))
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const pages = Array.from({ length: totalPages }, (_, index) => index + 1)

  return (
    <div className="inventoryPanel">
      <p className="inventoryNote" aria-live="polite">
        One row per email domain, falling back to the company name for contacts with no email address.
        Open a company to see what the relationship has produced and whether they write back.
        {loading ? <span className="inventoryNoteBusy"> Loading companies...</span> : null}
      </p>
      <div className="inventoryTableCard">
        <div className="inventoryTableScroll">
          <table className="inventoryTable">
            <thead>
              <tr>
                <th>Company</th>
                <th>People</th>
                <th>Status</th>
                <th>Opportunities</th>
                <th>Applications</th>
                <th>Replies</th>
                <th>Last activity</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((company) => (
                <tr
                  key={company.key}
                  data-company-key={company.key}
                  // The row is the only way into the panel, so it has to be
                  // reachable without a mouse - and focusable, or the panel has
                  // nowhere to hand focus back to on close.
                  tabIndex={0}
                  role="button"
                  aria-label={`Open ${company.name}`}
                  onClick={(event) => openCompany(company, event.currentTarget)}
                  onKeyDown={(event) => {
                    if (event.key !== 'Enter' && event.key !== ' ') return
                    event.preventDefault()
                    openCompany(company, event.currentTarget)
                  }}
                >
                  <td>
                    <span>{company.name}</span>
                    <small>{company.domain || 'No email domain'}</small>
                  </td>
                  <td className="inventoryScore">{company.contact_count}</td>
                  <td>
                    <div className="categoryChips">
                      {company.active_count > 0 ? <span className="statusBadge statusBadge--active">{company.active_count} Active</span> : null}
                      {company.flagged_count > 0 ? <span className="statusBadge statusBadge--flagged">{company.flagged_count} Flagged</span> : null}
                      {company.unscored_count > 0 ? <span className="statusBadge statusBadge--neutral">{company.unscored_count} Unscored</span> : null}
                    </div>
                  </td>
                  <td className="inventoryScore">{company.opportunity_count}</td>
                  <td className="inventoryScore">{company.application_count}</td>
                  <td>
                    {company.conversation_count === 0
                      ? <span className="subtle">--</span>
                      : <span className={company.replied_count > 0 ? 'inventoryScore' : 'subtle'}>{company.replied_count} of {company.conversation_count}</span>}
                  </td>
                  <td>{formatRelativeDate(company.lastCheckedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.length === 0 && !loading ? <p className="inventoryEmpty">No companies match these filters.</p> : null}
        <footer className="inventoryPaginationFooter">
          <span>Showing {rows.length === 0 ? 0 : (page - 1) * PAGE_SIZE + 1} to {Math.min(page * PAGE_SIZE, total)} of {total} companies</span>
          <nav className="pagination" aria-label="Company pages">
            <button type="button" onClick={() => setPage((current) => current - 1)} disabled={page <= 1}>‹</button>
            {pages.slice(Math.max(0, page - 3), Math.min(totalPages, page + 2)).map((pageNumber) => (
              <button
                type="button"
                key={pageNumber}
                className={pageNumber === page ? 'active' : ''}
                aria-current={pageNumber === page ? 'page' : undefined}
                onClick={() => setPage(pageNumber)}
              >
                {pageNumber}
              </button>
            ))}
            <button type="button" onClick={() => setPage((current) => current + 1)} disabled={page >= totalPages}>›</button>
          </nav>
        </footer>
      </div>

      {selected ? (
        <CompanyDetailPanel
          key={selected.key}
          company={selected}
          detail={detail}
          loading={detailLoading}
          returnFocusRef={returnFocusRef}
          onClose={() => setSelected(null)}
          onOpenContact={onOpenContact}
          onViewInInventory={onOpenCompany}
        />
      ) : null}
    </div>
  )
}
