import type { MouseEvent } from 'react'

import { CategoryChip, StatusBadge } from './StatusBadge'
import type { InventoryAction, InventoryRow } from './types'

function formatRelativeTime(value: string): string {
  const timestamp = new Date(value).getTime()
  if (!Number.isFinite(timestamp)) return '--'
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000))
  if (seconds < 60) return 'Just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`
  return new Date(value).toLocaleDateString()
}

function formatScore(score: number | null): string {
  return score == null ? '--' : `${score}/100`
}

type InventoryTableProps = {
  rows: InventoryRow[]
  allRowsCount: number
  selected: Set<string>
  busy: boolean
  page: number
  pageSize: number
  totalPages: number
  highlightedKey: string | null
  onToggle: (key: string) => void
  onSelectVisible: (checked: boolean) => void
  onPageChange: (page: number) => void
  onOpen: (row: InventoryRow, trigger: HTMLElement) => void
  onAction: (row: InventoryRow, action: InventoryAction) => void
}

export default function InventoryTable({
  rows,
  allRowsCount,
  selected,
  busy,
  page,
  pageSize,
  totalPages,
  highlightedKey,
  onToggle,
  onSelectVisible,
  onPageChange,
  onOpen,
  onAction,
}: InventoryTableProps) {
  const allVisibleSelected = rows.length > 0 && rows.every((row) => selected.has(row.key))
  const start = allRowsCount === 0 ? 0 : (page - 1) * pageSize + 1
  const end = Math.min(page * pageSize, allRowsCount)
  const pages = Array.from({ length: totalPages }, (_, index) => index + 1)

  const stop = (event: MouseEvent) => event.stopPropagation()

  return (
    <div className="inventoryTableCard">
      <div className="inventoryTableScroll">
        <table className="inventoryTable">
          <thead>
            <tr>
              <th className="inventoryCheckboxCell">
                <input
                  type="checkbox"
                  aria-label="Select all visible numbers"
                  checked={allVisibleSelected}
                  onChange={(event) => onSelectVisible(event.target.checked)}
                  disabled={busy || rows.length === 0}
                />
              </th>
              <th>Number</th>
              <th>Owner</th>
              <th>Category</th>
              <th>Score</th>
              <th>Status</th>
              <th>Last checked</th>
              <th><span className="visuallyHidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.key}
                className={`${selected.has(row.key) ? 'inventoryRow--selected' : ''} ${highlightedKey === row.key ? 'emailSearchHighlight' : ''}`}
                onClick={(event) => onOpen(row, event.currentTarget)}
                data-email-search-section="premium_numbers"
                data-email-search-related-id={row.id}
              >
                <td className="inventoryCheckboxCell" onClick={stop}>
                  <input
                    type="checkbox"
                    aria-label={`Select ${row.number}`}
                    checked={selected.has(row.key)}
                    onChange={() => onToggle(row.key)}
                    disabled={busy}
                  />
                </td>
                <td className="inventoryNumber">{row.number}</td>
                <td>
                  <span>{row.owner || 'Unassigned'}</span>
                  {row.company && row.company !== 'Unknown' ? <small>{row.company}</small> : null}
                </td>
                <td>
                  <div className="categoryChips">
                    {row.categories.length === 0 ? <span className="subtle">Pending</span> : null}
                    {row.categories.map((category) => <CategoryChip key={category} category={category} />)}
                  </div>
                </td>
                <td className="inventoryScore">{formatScore(row.score)}</td>
                <td><StatusBadge status={row.status} reasonCode={row.review?.reason_code} /></td>
                <td>{formatRelativeTime(row.lastCheckedAt)}</td>
                <td className="inventoryMenuCell" onClick={stop}>
                  <details className="inventoryMenu">
                    <summary aria-label={`Actions for ${row.number}`}>⋮</summary>
                    <div className="inventoryMenuPopover">
                      <button type="button" onClick={(event) => onOpen(row, event.currentTarget)}>View details</button>
                      <button type="button" onClick={() => onAction(row, 'mark-recruiter')}>Mark as Recruiter</button>
                      <button type="button" onClick={() => onAction(row, 'mark-employer')}>Mark as Employer</button>
                      <button type="button" onClick={() => onAction(row, 'rescore')}>Rescore</button>
                      <button type="button" className="dangerText" onClick={() => onAction(row, 'delete')}>Delete</button>
                    </div>
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length === 0 ? <p className="inventoryEmpty">No numbers match these filters.</p> : null}
      <footer className="inventoryPaginationFooter">
        <span>Showing {start} to {end} of {allRowsCount} loaded entries</span>
        <nav className="pagination" aria-label="Inventory pages">
          <button type="button" onClick={() => onPageChange(page - 1)} disabled={page <= 1}>‹</button>
          {pages.slice(Math.max(0, page - 3), Math.min(totalPages, page + 2)).map((pageNumber) => (
            <button
              type="button"
              key={pageNumber}
              className={pageNumber === page ? 'active' : ''}
              aria-current={pageNumber === page ? 'page' : undefined}
              onClick={() => onPageChange(pageNumber)}
            >
              {pageNumber}
            </button>
          ))}
          <button type="button" onClick={() => onPageChange(page + 1)} disabled={page >= totalPages}>›</button>
        </nav>
      </footer>
    </div>
  )
}
