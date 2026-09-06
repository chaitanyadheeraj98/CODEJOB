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

// A whole document can appear in a summary row - a profile card carries the
// complete resulting text, up to 20,000 characters. It must scroll, never
// truncate and never clip: the point of the card is that the user can read
// every character before clicking, so a "show more" toggle would not do.
const DOCUMENT_THRESHOLD = 400

function isDocument(value: string): boolean {
  return value.length > DOCUMENT_THRESHOLD || value.includes('\n')
}

export default function ProposalCard({ handler, fields, result, busy, disabled, onApprove, onCancel }: ProposalCardProps) {
  return (
    <div className="chatProposal">
      <strong>Confirm action</strong>
      <dl>
        {handler.summary(fields).map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{isDocument(value) ? <pre className="chatProposalDocument">{value}</pre> : (value || '-')}</dd>
          </div>
        ))}
      </dl>
      {result === 'cancelled' ? <p>Cancelled. No changes were made.</p> : null}
      {result && result !== 'cancelled' ? (
        <p className={result.approved ? '' : 'chatError'}>{result.detail}</p>
      ) : null}
      {/* Stated by the app, not the model: it is correct whether the assistant
          said the right thing, said nothing, or claimed the save already
          happened. Only in the unresolved branch - a card that shows both an
          outcome and "nothing has been saved yet" would be lying the other way. */}
      {!result && handler.pendingNotice ? (
        <p className="chatProposalPending">{handler.pendingNotice}</p>
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
