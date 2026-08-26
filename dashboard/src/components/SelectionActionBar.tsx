export type SelectionAction = { key: string; label: string; busyLabel?: string; onClick: () => void; disabled?: boolean; variant?: 'default' | 'danger' }
export default function SelectionActionBar({ selectedCount, busyKey, actions, onClearSelection }: { selectedCount: number; busyKey: string | null; actions: SelectionAction[]; onClearSelection: () => void }) {
  if (!selectedCount) return null
  const busy = busyKey !== null
  return <div className="selectionBar"><strong>{selectedCount} Selected</strong><span>Bulk actions active</span><div className="selectionActions">{actions.map((action) => <button key={action.key} type="button" className={action.variant === 'danger' ? 'dangerButton' : undefined} onClick={action.onClick} disabled={busy || action.disabled}>{action.key === busyKey ? action.busyLabel ?? 'Working...' : action.label}</button>)}<button type="button" onClick={onClearSelection} disabled={busy}>Clear selection</button></div></div>
}
