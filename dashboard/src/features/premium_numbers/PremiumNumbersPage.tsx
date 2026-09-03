import { useCallback, useEffect, useRef, useState } from 'react'

import type { EmailSearchHit } from '../../emailSearch'
import { backfillDuplicateContacts, ContactPhoneConflictError, getEmployerNumber, getRecruiterNumber, IdentityConflictError, pendingReviewCount } from './api'
import CreateContactPanel, { STATUS_MESSAGES } from './CreateContactPanel'
import DetailPanel from './DetailPanel'
import InventoryTable from './InventoryTable'
import MergeContactsModal from './MergeContactsModal'
import OpportunitiesTab from './OpportunitiesTab'
import RecycleBinTab from './RecycleBinTab'
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

type PremiumNumbersTab = 'inventory' | 'opportunities' | 'recycle_bin'

type PremiumNumbersPageProps = {
  apiBase: string
  mailDate: string | null
  emailSearchTarget: EmailSearchHit | null
  refreshToken: number
  applicationsEnabled: boolean
  onPendingCountChange: (count: number) => void
  activeTab?: PremiumNumbersTab
  onTabChange?: (tab: PremiumNumbersTab) => void
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
  const [tab, setLocalTab] = useState<PremiumNumbersTab>(targetOpportunityId == null ? 'inventory' : 'opportunities')
  const activeTab = controlledTab ?? tab
  const setTab = (next: PremiumNumbersTab) => { setLocalTab(next); onTabChange?.(next) }
  const [detailRow, setDetailRow] = useState<InventoryRow | null>(null)
  const [mergePair, setMergePair] = useState<{ a: number; b: number } | null>(null)
  const recoveredForRowsRef = useRef<InventoryRow[] | null>(null)
  const [creatingContact, setCreatingContact] = useState(false)
  const [backfillingDuplicates, setBackfillingDuplicates] = useState(false)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const toast = useToast()
  const inventory = useInventory(apiBase, refreshToken, filterValues, sortValue)
  const inventoryRows = inventory.rows
  const inventoryPageSize = inventory.pageSize
  const setInventoryPage = inventory.setPage

  useEffect(() => {
    if (inventory.error) toast.show(inventory.error, 'error')
  }, [inventory.error, toast.show])

  useEffect(() => {
    if (!detailRow) return
    const fresh = inventoryRows.find((candidate) => candidate.key === detailRow.key)
    if (fresh) {
      if (fresh !== detailRow) setDetailRow(fresh)
      return
    }
    // An action taken from the open panel (e.g. select-version) can bump the row's
    // sort key and push it off whatever page is currently loaded, so it won't show up
    // in inventoryRows even though it still exists - fall back to fetching it directly
    // rather than leaving the panel stuck on pre-action data. One attempt per reload.
    if (detailRow.kind !== 'contact' || recoveredForRowsRef.current === inventoryRows) return
    recoveredForRowsRef.current = inventoryRows
    const key = detailRow.key
    const wantRecruiter = Boolean(detailRow.recruiter)
    const wantEmployer = Boolean(detailRow.employer)
    if (!wantRecruiter && !wantEmployer) return
    Promise.all([
      wantRecruiter ? getRecruiterNumber(apiBase, detailRow.id).catch(() => undefined) : Promise.resolve(detailRow.recruiter),
      wantEmployer ? getEmployerNumber(apiBase, detailRow.id).catch(() => undefined) : Promise.resolve(detailRow.employer),
    ]).then(([recruiter, employer]) => {
      setDetailRow((current) => (current && current.key === key ? { ...current, recruiter, employer } : current))
    })
  }, [apiBase, inventoryRows, detailRow])

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
    try {
      await inventory.runRowAction(row, action, edits)
    } catch (reason) {
      // Rescore found the corrected number already belongs to another contact - that's
      // not a dead end to just report, it's the exact case "Merge Contacts" exists for.
      // Open it straight from the conflict instead of making the user go find and
      // checkbox-select both contacts from the list themselves.
      if (reason instanceof ContactPhoneConflictError && row.kind === 'contact' && reason.conflictingContactId != null) {
        setDetailRow(null)
        setMergePair({ a: row.id, b: reason.conflictingContactId })
        return
      }
      if (reason instanceof IdentityConflictError && reason.targetContactId != null && reason.secondaryContactId != null) {
        setDetailRow(null)
        setMergePair({ a: reason.targetContactId, b: reason.secondaryContactId })
        return
      }
      throw reason
    }
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

  const selectedContactRows = inventory.rows.filter((row) => inventory.selected.has(row.key) && row.kind === 'contact')
  const openMergePreview = () => {
    if (selectedContactRows.length !== 2) return
    setMergePair({ a: selectedContactRows[0].id, b: selectedContactRows[1].id })
  }

  const runDuplicateBackfill = () => {
    if (!window.confirm('Find contacts that share the same email address and merge them into one? This runs immediately and cannot be undone from here.')) return
    setBackfillingDuplicates(true)
    backfillDuplicateContacts(apiBase)
      .then((result) => {
        toast.show(result.groups_merged === 0 ? 'No duplicate contacts found' : `Merged ${result.contacts_merged} duplicate contact${result.contacts_merged === 1 ? '' : 's'} into ${result.groups_merged} contact${result.groups_merged === 1 ? '' : 's'}`)
        return inventory.reload()
      })
      .catch((reason) => toast.show((reason as Error).message, 'error'))
      .finally(() => setBackfillingDuplicates(false))
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
          <button type="button" role="tab" aria-selected={activeTab === 'recycle_bin'} className={activeTab === 'recycle_bin' ? 'active' : ''} onClick={() => setTab('recycle_bin')}>Recycle Bin</button>
        </div>
      </header>

      {activeTab === 'inventory' ? (
        <div role="tabpanel" className="inventoryPanel">
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
            <button type="button" onClick={runDuplicateBackfill} disabled={backfillingDuplicates}>
              {backfillingDuplicates ? 'Merging...' : 'Merge Duplicate Contacts'}
            </button>
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
              { key: 'merge', label: 'Merge (pick exactly 2 contacts)', onClick: openMergePreview, disabled: selectedContactRows.length !== 2 || inventory.selected.size !== 2 },
              { key: 'delete', label: 'Delete', onClick: () => runBulk('delete'), variant: 'danger' },
            ]}
          />

          <p className="inventoryNote">Rescoring re-checks every selected number in its original source. Bulk actions ignore unsaved detail-panel edits.</p>
          {inventory.loading ? <p className="subtle">Loading premium numbers...</p> : null}
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
      ) : activeTab === 'opportunities' ? (
        <div role="tabpanel">
          <OpportunitiesTab apiBase={apiBase} mailDate={mailDate} refreshToken={refreshToken} highlightedId={targetOpportunityId} applicationsEnabled={applicationsEnabled} onToast={toast.show} filterValues={filterValues} sortValue={sortValue} />
        </div>
      ) : (
        <div role="tabpanel">
          <RecycleBinTab apiBase={apiBase} refreshToken={refreshToken} onToast={toast.show} filterValues={filterValues} sortValue={sortValue} />
        </div>
      )}

      <ToastHost message={toast.message} tone={toast.tone} onDismiss={toast.clear} />

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
          onPhoneConflict={(conflictRow, conflictingContactId) => {
            setDetailRow(null)
            setMergePair({ a: conflictRow.id, b: conflictingContactId })
          }}
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
          onError={(message) => toast.show(message, 'error')}
        />
      ) : null}

      {mergePair ? (
        <MergeContactsModal
          apiBase={apiBase}
          contactIdA={mergePair.a}
          contactIdB={mergePair.b}
          onClose={() => setMergePair(null)}
          onMerged={() => {
            toast.show('Contacts merged')
            inventory.selectVisible(false)
            refreshCount()
            inventory.reload().catch(() => undefined)
          }}
          onError={(message) => toast.show(message, 'error')}
        />
      ) : null}
    </section>
  )
}
