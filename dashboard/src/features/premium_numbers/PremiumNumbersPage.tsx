import { useCallback, useEffect, useRef, useState } from 'react'

import type { EmailSearchHit } from '../../emailSearch'
import { pendingReviewCount } from './api'
import CreateContactPanel, { STATUS_MESSAGES } from './CreateContactPanel'
import DetailPanel from './DetailPanel'
import InventoryTable from './InventoryTable'
import OpportunitiesTab from './OpportunitiesTab'
import { ToastHost, useToast } from './Toast'
import type { InventoryAction, InventoryRow, ReviewEdits } from './types'
import { useInventory } from './useInventory'
import type { FilterValues } from '../../components/FilterSortBar'
import SelectionActionBar from '../../components/SelectionActionBar'

const ACTION_TOAST_LABELS: Record<InventoryAction, string> = {
  'mark-recruiter': 'Marked as recruiter',
  'mark-employer': 'Marked as employer',
  rescore: 'Rescored',
  delete: 'Deleted',
}

type PremiumNumbersPageProps = {
  apiBase: string
  mailDate: string | null
  emailSearchTarget: EmailSearchHit | null
  refreshToken: number
  applicationsEnabled: boolean
  onPendingCountChange: (count: number) => void
  activeTab?: 'inventory' | 'opportunities'
  onTabChange?: (tab: 'inventory' | 'opportunities') => void
  filterValues?: FilterValues
  sortValue?: string
}

function inventoryTargetKey(target: EmailSearchHit | null): string | null {
  if (!target || target.section !== 'premium_numbers') return null
  if (target.detail.number_review_id != null) return `review:${target.detail.number_review_id}`
  // A premium_number_lead_id hit only has somewhere to navigate to once the lead is
  // promoted to a contact (detail.contact_id) - an un-promoted lead has no inventory row.
  const contactId = target.detail.recruiter_number_id ?? target.detail.employer_number_id ?? target.detail.contact_id
  return contactId == null ? null : `contact:${contactId}`
}

function opportunityTargetId(target: EmailSearchHit | null): number | null {
  if (!target || target.section !== 'premium_numbers') return null
  const value = target.detail.recruiter_opportunity_id
  return typeof value === 'number' ? value : null
}

export default function PremiumNumbersPage({
  apiBase,
  mailDate,
  emailSearchTarget,
  refreshToken,
  applicationsEnabled,
  onPendingCountChange,
  activeTab: controlledTab,
  onTabChange,
  filterValues = {},
  sortValue = 'newest',
}: PremiumNumbersPageProps) {
  const targetOpportunityId = opportunityTargetId(emailSearchTarget)
  const targetInventoryKey = inventoryTargetKey(emailSearchTarget)
  const [tab, setLocalTab] = useState<'inventory' | 'opportunities'>(targetOpportunityId == null ? 'inventory' : 'opportunities')
  const activeTab = controlledTab ?? tab
  const setTab = (next: 'inventory' | 'opportunities') => { setLocalTab(next); onTabChange?.(next) }
  const [detailRow, setDetailRow] = useState<InventoryRow | null>(null)
  const [creatingContact, setCreatingContact] = useState(false)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const toast = useToast()
  const inventory = useInventory(apiBase, refreshToken, filterValues, sortValue)
  const inventoryRows = inventory.rows
  const inventoryPageSize = inventory.pageSize
  const setInventoryPage = inventory.setPage

  useEffect(() => {
    if (!detailRow) return
    const fresh = inventoryRows.find((candidate) => candidate.key === detailRow.key)
    if (fresh && fresh !== detailRow) setDetailRow(fresh)
  }, [inventoryRows, detailRow])

  const refreshCount = useCallback(() => {
    pendingReviewCount(apiBase)
      .then(onPendingCountChange)
      .catch(() => undefined)
  }, [apiBase, onPendingCountChange])

  useEffect(refreshCount, [refreshCount, refreshToken])

  useEffect(() => {
    const nextTab = targetOpportunityId != null ? 'opportunities' : targetInventoryKey ? 'inventory' : null
    if (!nextTab) return
    const timer = window.setTimeout(() => setTab(nextTab), 0)
    return () => window.clearTimeout(timer)
  }, [targetInventoryKey, targetOpportunityId])

  useEffect(() => {
    if (!targetInventoryKey) return
    const index = inventoryRows.findIndex((row) => row.key === targetInventoryKey)
    if (index < 0) return
    const timer = window.setTimeout(() => {
      setInventoryPage(Math.floor(index / inventoryPageSize) + 1)
      window.setTimeout(() => {
        const row = inventoryRows[index]
        const candidates = Array.from(document.querySelectorAll<HTMLElement>('[data-email-search-section="premium_numbers"]'))
        candidates.find((element) => element.dataset.emailSearchRelatedId === String(row.id))
          ?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
      }, 0)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [inventoryPageSize, inventoryRows, setInventoryPage, targetInventoryKey])

  const openDetail = (row: InventoryRow, trigger: HTMLElement) => {
    returnFocusRef.current = trigger
    setDetailRow(row)
  }

  const runRowAction = async (row: InventoryRow, action: InventoryAction, edits?: ReviewEdits) => {
    if (action === 'delete' && !window.confirm(`Delete this entire contact (${row.number})? This will hide the contact card and all of its saved versions. You can restore it later.`)) return
    await inventory.runRowAction(row, action, edits)
    refreshCount()
    toast.show(ACTION_TOAST_LABELS[action])
    if (action === 'delete' || action === 'mark-recruiter' || action === 'mark-employer') setDetailRow(null)
  }

  const runBulk = (action: InventoryAction) => {
    if (action === 'delete') {
      const count = inventory.selected.size
      if (!window.confirm(`Delete ${count} selected number${count === 1 ? '' : 's'}? This hides them from the inventory but can be restored later.`)) return
    }
    inventory.runBulkAction(action)
      .then(() => {
        refreshCount()
        toast.show(ACTION_TOAST_LABELS[action])
      })
      .catch(() => undefined)
  }

  return (
    <section className="card pageSection premiumNumbersPage">
      <header className="premiumNumbersHeader">
        <div>
          <h2>Premium Contacts</h2>
          <p className="subtle">Manage recruiter and employer identities, assignments, and review operations.</p>
        </div>
        <div className="premiumTabs" role="tablist" aria-label="Premium number views">
          <button type="button" role="tab" aria-selected={activeTab === 'inventory'} className={activeTab === 'inventory' ? 'active' : ''} onClick={() => setTab('inventory')}>Number Inventory</button>
          <button type="button" role="tab" aria-selected={activeTab === 'opportunities'} className={activeTab === 'opportunities' ? 'active' : ''} onClick={() => setTab('opportunities')}>Recruiter Opportunities</button>
        </div>
      </header>

      {activeTab === 'inventory' ? (
        <div role="tabpanel" className="inventoryPanel">
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button type="button" onClick={() => setCreatingContact(true)}>+ Create Contact</button>
          </div>
          <SelectionActionBar
            selectedCount={inventory.selected.size}
            busyKey={inventory.busyBulkAction}
            onClearSelection={() => inventory.selectVisible(false)}
            actions={[
              { key: 'mark-recruiter', label: 'Mark as Recruiter', onClick: () => runBulk('mark-recruiter') },
              { key: 'mark-employer', label: 'Mark as Employer', onClick: () => runBulk('mark-employer') },
              { key: 'rescore', label: 'Rescore', onClick: () => runBulk('rescore') },
              { key: 'delete', label: 'Delete', onClick: () => runBulk('delete'), variant: 'danger' },
            ]}
          />

          <p className="inventoryNote">Rescoring re-checks every selected number in its original source. Bulk actions ignore unsaved detail-panel edits.</p>
          {inventory.loading ? <p className="subtle">Loading premium numbers...</p> : null}
          {inventory.error ? <p className="errorBanner">Premium numbers error: {inventory.error}</p> : null}
          <InventoryTable
            rows={inventory.visibleRows}
            allRowsCount={inventory.total}
            selected={inventory.selected}
            busy={inventory.busy}
            page={inventory.page}
            pageSize={inventory.pageSize}
            totalPages={inventory.totalPages}
            highlightedKey={targetInventoryKey}
            onToggle={inventory.toggle}
            onSelectVisible={inventory.selectVisible}
            onPageChange={inventory.setPage}
            onOpen={openDetail}
            onAction={(row, action) => { runRowAction(row, action).catch(() => undefined) }}
            onToggleFavorite={(row) => { inventory.toggleFavorite(row).catch(() => undefined) }}
          />
        </div>
      ) : (
        <div role="tabpanel">
          <OpportunitiesTab apiBase={apiBase} mailDate={mailDate} refreshToken={refreshToken} highlightedId={targetOpportunityId} applicationsEnabled={applicationsEnabled} onToast={toast.show} filterValues={filterValues} sortValue={sortValue} />
        </div>
      )}

      <ToastHost message={toast.message} onDismiss={toast.clear} />

      {detailRow ? (
        <DetailPanel
          key={detailRow.key}
          apiBase={apiBase}
          row={detailRow}
          busy={inventory.busy}
          returnFocusRef={returnFocusRef}
          onClose={() => setDetailRow(null)}
          onAction={runRowAction}
          onReload={inventory.reload}
          onError={inventory.setError}
          onToast={toast.show}
        />
      ) : null}

      {creatingContact ? (
        <CreateContactPanel
          apiBase={apiBase}
          onClose={() => setCreatingContact(false)}
          onCreated={(result) => {
            setCreatingContact(false)
            toast.show(STATUS_MESSAGES[result.status])
            refreshCount()
            inventory.reload().catch(() => undefined)
          }}
        />
      ) : null}
    </section>
  )
}
