import { useCallback, useEffect, useRef, useState } from 'react'

import type { EmailSearchHit } from '../../emailSearch'
import { pendingReviewCount } from './api'
import DetailPanel from './DetailPanel'
import InventoryTable from './InventoryTable'
import OpportunitiesTab from './OpportunitiesTab'
import { ToastHost, useToast } from './Toast'
import type { InventoryAction, InventoryRow, ReviewEdits } from './types'
import { useInventory } from './useInventory'

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
  onPendingCountChange: (count: number) => void
}

function inventoryTargetKey(target: EmailSearchHit | null): string | null {
  if (!target || target.section !== 'premium_numbers') return null
  if (target.detail.number_review_id != null) return `review:${target.detail.number_review_id}`
  const contactId = target.detail.recruiter_number_id ?? target.detail.employer_number_id
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
  onPendingCountChange,
}: PremiumNumbersPageProps) {
  const targetOpportunityId = opportunityTargetId(emailSearchTarget)
  const targetInventoryKey = inventoryTargetKey(emailSearchTarget)
  const [tab, setTab] = useState<'inventory' | 'opportunities'>(targetOpportunityId == null ? 'inventory' : 'opportunities')
  const [detailRow, setDetailRow] = useState<InventoryRow | null>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const toast = useToast()
  const inventory = useInventory(apiBase, refreshToken)
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
          <h2>Premium Numbers</h2>
          <p className="subtle">Manage inventory, assignments, and rescoring operations.</p>
        </div>
        <div className="premiumTabs" role="tablist" aria-label="Premium number views">
          <button type="button" role="tab" aria-selected={tab === 'inventory'} className={tab === 'inventory' ? 'active' : ''} onClick={() => setTab('inventory')}>Number Inventory</button>
          <button type="button" role="tab" aria-selected={tab === 'opportunities'} className={tab === 'opportunities' ? 'active' : ''} onClick={() => setTab('opportunities')}>Recruiter Opportunities</button>
        </div>
      </header>

      {tab === 'inventory' ? (
        <div role="tabpanel" className="inventoryPanel">
          <div className="inventoryToolbar">
            <label className="inventorySearchField">
              <span>Search inventory</span>
              <input value={inventory.search} onChange={(event) => inventory.setSearch(event.target.value)} placeholder="Search number, owner, company..." />
            </label>
            <label><span>Status</span><select value={inventory.status} onChange={(event) => inventory.setStatus(event.target.value as typeof inventory.status)}><option value="all">All statuses</option><option value="Pending">Pending</option><option value="Active">Active</option><option value="Flagged">Flagged</option></select></label>
            <label><span>Category</span><select value={inventory.category} onChange={(event) => inventory.setCategory(event.target.value as typeof inventory.category)}><option value="all">All categories</option><option value="Recruiter">Recruiter</option><option value="Employer">Employer</option></select></label>
            <label><span>Source</span><select value={inventory.source} onChange={(event) => inventory.setSource(event.target.value as typeof inventory.source)}><option value="all">All sources</option><option value="gmail">Gmail</option><option value="nvoids">Nvoids</option></select></label>
          </div>

          {inventory.selected.size > 0 ? (
            <div className="selectionBar">
              <strong>{inventory.selected.size} Selected</strong>
              <span>Bulk actions active</span>
              <div className="selectionActions">
                <button type="button" onClick={() => runBulk('mark-recruiter')} disabled={inventory.busy}>{inventory.busy ? 'Working...' : 'Mark as Recruiter'}</button>
                <button type="button" onClick={() => runBulk('mark-employer')} disabled={inventory.busy}>{inventory.busy ? 'Working...' : 'Mark as Employer'}</button>
                <button type="button" onClick={() => runBulk('rescore')} disabled={inventory.busy}>{inventory.busy ? 'Working...' : 'Rescore'}</button>
                <button type="button" className="dangerButton" onClick={() => runBulk('delete')} disabled={inventory.busy}>{inventory.busy ? 'Working...' : 'Delete'}</button>
              </div>
            </div>
          ) : null}

          <p className="inventoryNote">Rescoring re-checks every selected number in its original source. Bulk actions ignore unsaved detail-panel edits.</p>
          {inventory.loading ? <p className="subtle">Loading premium numbers...</p> : null}
          {inventory.error ? <p className="errorBanner">Premium numbers error: {inventory.error}</p> : null}
          <InventoryTable
            rows={inventory.visibleRows}
            allRowsCount={inventory.rows.length}
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
          />
          <p className="inventoryApproximation">Counts reflect up to 300 loaded rows per source; source endpoints remain independently paginated.</p>
        </div>
      ) : (
        <div role="tabpanel">
          <OpportunitiesTab apiBase={apiBase} mailDate={mailDate} refreshToken={refreshToken} highlightedId={targetOpportunityId} onToast={toast.show} />
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
    </section>
  )
}
