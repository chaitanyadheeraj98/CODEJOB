import type { ProposalFields, ProposalHandler } from './proposals'

export type ProposalResult = { approved: boolean; detail: string } | 'cancelled'

type ProposalCardProps = {
  handler: ProposalHandler
  fields: ProposalFields
  progress?: string
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

export default function ProposalCard({ handler, fields, progress, result, busy, disabled, onApprove, onCancel }: ProposalCardProps) {
  const grounding = fields.action === 'propose_resume_section' && fields.grounding && typeof fields.grounding === 'object'
    ? fields.grounding as Record<string, unknown> : null
  const numbers = Array.isArray(grounding?.novel_numbers) ? grounding.novel_numbers.filter((value): value is string => typeof value === 'string') : []
  const organisations = Array.isArray(grounding?.novel_organisations) ? grounding.novel_organisations.filter((value): value is string => typeof value === 'string') : []
  // Read, not recomputed: the server owns the threshold, so the caution shown
  // here is the same judgement the tool made when it built the card.
  const lowSimilarity = grounding?.low_similarity === true
  return (
    <div className="chatProposal">
      <strong>Confirm action</strong>
      {progress ? <p className="chatProposalProgress">{progress}</p> : null}
      <dl>
        {handler.summary(fields).map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{isDocument(value) ? <pre className="chatProposalDocument">{value}</pre> : (value || '-')}</dd>
          </div>
        ))}
      </dl>
      {numbers.length || organisations.length || lowSimilarity ? <div className="chatGroundingCaution" role="note">
        <strong>Check the supporting facts</strong>
        {numbers.length ? <p>Numbers absent from the draft: {numbers.join(', ')}</p> : null}
        {organisations.length ? <p>Possible new organizations: {organisations.join(', ')}</p> : null}
        {lowSimilarity ? <p>This wording differs substantially from the current section.</p> : null}
        <p>These are review cautions. Apply only if the new details are accurate.</p>
      </div> : null}
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
