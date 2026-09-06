import { useEffect, useState } from 'react'

import type { FilterValues } from '../../components/FilterSortBar'
import { listCompanies } from './api'
import { companyFiltersToParams } from './companyFilters'
import { CategoryChip, StatusBadge } from './StatusBadge'
import type { ToastTone } from './Toast'
import type { CompanyCard, InventoryRow } from './types'

const PAGE_SIZE = 10
const NO_NUMBER_PLACEHOLDER = '(XXX) XXX-XXXX'

type CompanyInventoryTabProps = {
  apiBase: string
  refreshToken: number
  onToast: (message: string, tone?: ToastTone) => void
  onOpenContact: (company: CompanyCard, contact: InventoryRow) => void
  onOpenCompany: (company: CompanyCard) => void
  filterValues?: FilterValues
  sortValue?: string
}

function rowEmail(row: InventoryRow): string {
  return row.recruiter?.recruiter_email || row.employer?.employer_email || ''
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

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const pages = Array.from({ length: totalPages }, (_, index) => index + 1)

  return (
    <div className="inventoryPanel">
      <p className="inventoryNote" aria-live="polite">
        One card per email domain, falling back to the company name for contacts that have no email address yet.
        Opening a name jumps to that contact in Number Inventory.
        {loading ? <span className="inventoryNoteBusy"> Loading companies...</span> : null}
      </p>
      {!loading && rows.length === 0 ? <p className="inventoryEmpty">No companies match these filters.</p> : null}
      <div className="companyGrid">
        {rows.map((company) => (
          <article key={company.key} className="companyCard" data-company-key={company.key}>
            <header>
              <div className="companyIdentity">
                <h3>{company.name}</h3>
                <p className="companyDomain">{company.domain || 'No email domain'}</p>
              </div>
              <div className="companyHeaderMeta">
                <div className="categoryChips">
                  <span className="categoryChip">{company.contact_count} contact{company.contact_count === 1 ? '' : 's'}</span>
                  {company.recruiter_count > 0 ? <CategoryChip category="Recruiter" /> : null}
                  {company.employer_count > 0 ? <CategoryChip category="Employer" /> : null}
                </div>
                <button type="button" onClick={() => onOpenCompany(company)}>View in Number Inventory</button>
              </div>
            </header>
            <div className="inventoryTableScroll">
              <table className="inventoryTable companyContactsTable">
                <thead>
                  <tr>
                    <th>Owner</th>
                    <th>Number</th>
                    <th>Email</th>
                    <th>Category</th>
                    <th>Score</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {company.contacts.map((contact) => (
                    <tr key={contact.key} onClick={() => onOpenContact(company, contact)}>
                      <td>
                        <button type="button" className="companyContactLink" onClick={(event) => { event.stopPropagation(); onOpenContact(company, contact) }}>
                          {contact.owner || 'Unassigned'}
                        </button>
                        {contact.company && contact.company !== 'Unknown' && contact.company !== company.name
                          ? <small>{contact.company}</small>
                          : null}
                      </td>
                      <td className={`inventoryNumber ${contact.number ? '' : 'inventoryNumber--empty'}`}>{contact.number || NO_NUMBER_PLACEHOLDER}</td>
                      <td>{rowEmail(contact) || '--'}</td>
                      <td>
                        <div className="categoryChips">
                          {contact.categories.map((category) => <CategoryChip key={category} category={category} />)}
                        </div>
                      </td>
                      <td className="inventoryScore">{contact.score == null ? '--' : `${contact.score}/100`}</td>
                      <td><StatusBadge status={contact.status} reasonCode={contact.review?.reason_code} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {company.contact_count > company.contacts.length ? (
              <p className="inventoryNote">
                Showing {company.contacts.length} of {company.contact_count} contacts. Open the company in Number Inventory to see the rest.
              </p>
            ) : null}
          </article>
        ))}
      </div>
      {rows.length > 0 ? (
        <footer className="inventoryPaginationFooter">
          <span>Showing {(page - 1) * PAGE_SIZE + 1} to {Math.min(page * PAGE_SIZE, total)} of {total} companies</span>
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
      ) : null}
    </div>
  )
}
