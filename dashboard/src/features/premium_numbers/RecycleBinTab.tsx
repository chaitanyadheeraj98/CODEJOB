import { type MouseEvent, useEffect, useState } from 'react'

import type { FilterValues } from '../../components/FilterSortBar'
import SelectionActionBar from '../../components/SelectionActionBar'
import { bulkPurgeContacts, bulkRestoreContacts, listDeletedContacts, purgeContact, restoreContact } from './api'
import RecycleBinDetailPanel from './RecycleBinDetailPanel'
import { recycleBinFiltersToParams } from './recycleBinFilters'
import { CategoryChip } from './StatusBadge'
import type { ToastTone } from './Toast'
import type { InventoryRow } from './types'

const PAGE_SIZE = 10
const NO_NUMBER_PLACEHOLDER = '(XXX) XXX-XXXX'

type RecycleBinTabProps = {
  apiBase: string
  refreshToken: number
  onToast: (message: string, tone?: ToastTone) => void
  filterValues?: FilterValues
  sortValue?: string
}

function rowNumberDisplay(row: InventoryRow): string {
  return row.number || NO_NUMBER_PLACEHOLDER
}

function rowEmail(row: InventoryRow): string {
  return row.recruiter?.recruiter_email || row.employer?.employer_email || ''
}

function rowEmailDomain(row: InventoryRow): string {
  return row.recruiter?.recruiter_email_domain || row.employer?.employer_email_domain || ''
}

function formatDeletedAt(value: string): string {
  const timestamp = new Date(value).getTime()
  return Number.isFinite(timestamp) ? new Date(value).toLocaleString() : '--'
}

export default function RecycleBinTab({ apiBase, refreshToken, onToast, filterValues = {}, sortValue = 'newest' }: RecycleBinTabProps) {
  const [rows, setRows] = useState<InventoryRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [bulkBusy, setBulkBusy] = useState<'restore' | 'purge' | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [detailRow, setDetailRow] = useState<InventoryRow | null>(null)

  useEffect(() => { setPage(1); setSelected(new Set()) }, [filterValues, sortValue])

  const load = () => {
    setLoading(true)
    const params = { cursor: String((page - 1) * PAGE_SIZE), limit: String(PAGE_SIZE), sort: sortValue, ...recycleBinFiltersToParams(filterValues) }
    listDeletedContacts(apiBase, params)
      .then((payload) => {
        setRows(payload.items)
        setTotal(payload.total)
        setSelected((current) => new Set([...current].filter((id) => payload.items.some((row) => row.id === id))))
        setDetailRow((current) => (current ? payload.items.find((row) => row.id === current.id) ?? null : null))
      })
      .catch((reason) => onToast((reason as Error).message, 'error'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    const timer = window.setTimeout(load, 150)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, filterValues, page, refreshToken, sortValue])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const allVisibleSelected = rows.length > 0 && rows.every((row) => selected.has(row.id))

  const restoreOne = (row: InventoryRow) => {
    setBusyId(row.id)
    restoreContact(apiBase, row.id)
      .then(() => {
        onToast('Restored')
        if (detailRow?.id === row.id) setDetailRow(null)
        load()
      })
      .catch((reason) => onToast((reason as Error).message, 'error'))
      .finally(() => setBusyId(null))
  }

  const purgeOne = (row: InventoryRow) => {
    if (!window.confirm(`Permanently delete this contact (${rowNumberDisplay(row)})? This cannot be undone.`)) return
    setBusyId(row.id)
    purgeContact(apiBase, row.id)
      .then(() => {
        onToast('Deleted forever')
        if (detailRow?.id === row.id) setDetailRow(null)
        load()
      })
      .catch((reason) => onToast((reason as Error).message, 'error'))
      .finally(() => setBusyId(null))
  }

  const restoreSelected = () => {
    const ids = [...selected]
    if (!ids.length) return
    setBulkBusy('restore')
    bulkRestoreContacts(apiBase, ids)
      .then((result) => {
        const failed = result.results.filter((item) => item.status !== 'restored').length
        onToast(failed ? `${ids.length - failed} restored, ${failed} failed` : 'Restored')
        setSelected(new Set())
        load()
      })
      .catch((reason) => onToast((reason as Error).message, 'error'))
      .finally(() => setBulkBusy(null))
  }

  const purgeSelected = () => {
    const ids = [...selected]
    if (!ids.length) return
    if (!window.confirm(`Permanently delete ${ids.length} selected contact${ids.length === 1 ? '' : 's'}? This cannot be undone.`)) return
    setBulkBusy('purge')
    bulkPurgeContacts(apiBase, ids)
      .then((result) => {
        const failed = result.results.filter((item) => item.status !== 'purged').length
        onToast(failed ? `${ids.length - failed} deleted forever, ${failed} failed` : 'Deleted forever')
        setSelected(new Set())
        load()
      })
      .catch((reason) => onToast((reason as Error).message, 'error'))
      .finally(() => setBulkBusy(null))
  }

  const toggle = (id: number) => setSelected((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const stop = (event: MouseEvent) => event.stopPropagation()
  const busy = busyId !== null

  return (
    <div className="inventoryPanel">
      <SelectionActionBar
        selectedCount={selected.size}
        busyKey={bulkBusy}
        onClearSelection={() => setSelected(new Set())}
        actions={[
          { key: 'restore', label: 'Restore', busyLabel: 'Restoring...', onClick: restoreSelected },
          { key: 'purge', label: 'Delete Forever', busyLabel: 'Deleting...', onClick: purgeSelected, variant: 'danger' },
        ]}
      />
      <p className="inventoryNote">Deleted contacts are hidden from Number Inventory but kept here until restored or permanently deleted.</p>
      {loading ? <p className="subtle">Loading deleted contacts...</p> : null}
      <div className="inventoryTableCard">
        <div className="inventoryTableScroll">
          <table className="inventoryTable">
            <thead>
              <tr>
                <th className="inventoryCheckboxCell">
                  <input
                    type="checkbox"
                    aria-label="Select all visible deleted contacts"
                    checked={allVisibleSelected}
                    onChange={(event) => setSelected(event.target.checked ? new Set(rows.map((row) => row.id)) : new Set())}
                    disabled={rows.length === 0}
                  />
                </th>
                <th>Owner</th>
                <th>Number</th>
                <th>Email</th>
                <th>E-Domain</th>
                <th>Category</th>
                <th>Deleted</th>
                <th><span className="visuallyHidden">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key} onClick={() => setDetailRow(row)}>
                  <td className="inventoryCheckboxCell" onClick={stop}>
                    <input type="checkbox" aria-label={`Select ${rowNumberDisplay(row)}`} checked={selected.has(row.id)} onChange={() => toggle(row.id)} />
                  </td>
                  <td>
                    <span>{row.owner || 'Unassigned'}</span>
                    {row.company && row.company !== 'Unknown' ? <small>{row.company}</small> : null}
                  </td>
                  <td className={`inventoryNumber ${row.number ? '' : 'inventoryNumber--empty'}`}>{rowNumberDisplay(row)}</td>
                  <td>{rowEmail(row) || '--'}</td>
                  <td>{rowEmailDomain(row) || '--'}</td>
                  <td>
                    <div className="categoryChips">
                      {row.categories.map((category) => <CategoryChip key={category} category={category} />)}
                    </div>
                  </td>
                  <td>{formatDeletedAt(row.lastCheckedAt)}</td>
                  <td className="recycleBinRowActions" onClick={stop}>
                    <button type="button" onClick={() => restoreOne(row)} disabled={busyId === row.id}>
                      {busyId === row.id ? 'Working...' : 'Restore'}
                    </button>
                    <button type="button" className="dangerText" onClick={() => purgeOne(row)} disabled={busyId === row.id}>
                      Delete Forever
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.length === 0 && !loading ? <p className="inventoryEmpty">Recycle Bin is empty.</p> : null}
        <footer className="inventoryPaginationFooter">
          <span>Showing {rows.length === 0 ? 0 : (page - 1) * PAGE_SIZE + 1} to {Math.min(page * PAGE_SIZE, total)} of {total} deleted contacts</span>
          <nav className="pagination" aria-label="Recycle bin pages">
            <button type="button" onClick={() => setPage((current) => current - 1)} disabled={page <= 1}>‹</button>
            <button type="button" onClick={() => setPage((current) => current + 1)} disabled={page >= totalPages}>›</button>
          </nav>
        </footer>
      </div>

      {detailRow ? (
        <RecycleBinDetailPanel
          row={detailRow}
          busy={busy}
          onClose={() => setDetailRow(null)}
          onRestore={() => restoreOne(detailRow)}
          onDelete={() => purgeOne(detailRow)}
        />
      ) : null}
    </div>
  )
}
