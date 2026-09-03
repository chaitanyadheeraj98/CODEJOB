import type { ProposalFields, ProposalHandler } from './proposals'

export type ProposalResult = { approved: boolean; detail: string } | 'cancelled'

type ProposalCardProps = {
  handler: ProposalHandler
  fields: ProposalFields
  result?: ProposalResult
  // `busy` is this card's own in-flight state; `disabled` is any card's, so a
  // second confirmation can't be fired while one is running. They are separate
  // because only the running card shows "Working...".
  busy: boolean
  disabled: boolean
  onApprove: () => void
  onCancel: () => void
}

export default function ProposalCard({ handler, fields, result, busy, disabled, onApprove, onCancel }: ProposalCardProps) {
  return (
    <div className="chatProposal">
      <strong>Confirm action</strong>
      <dl>
        {handler.summary(fields).map(([label, value]) => (
          <div key={label}><dt>{label}</dt><dd>{value || '-'}</dd></div>
        ))}
      </dl>
      {result === 'cancelled' ? <p>Cancelled. No changes were made.</p> : null}
      {result && result !== 'cancelled' ? (
        <p className={result.approved ? '' : 'chatError'}>{result.detail}</p>
      ) : null}
      {!result ? (
        <div className="chatProposalActions">
          <button
            type="button"
            onClick={onApprove}
            disabled={disabled}
          >
            {busy ? 'Working...' : handler.confirmLabel(fields)}
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={disabled}
          >
            Cancel
          </button>
        </div>
      ) : null}
    </div>
  )
}
