import {
  type Candidate,
  type SentItemDetails,
  ParserDetailsPanel,
  ResumePickerPanel,
  canTrustRouting,
  draftTextSizeToPreviewStyle,
  draftToPreviewHtml,
  formatAtsScore,
  getAtsStrengthLabel,
  getOverallVerdict,
  getResumeContextLabel,
  jdSummarySkills,
  normalizeParserDetails,
  renderContactDetailsGrid,
  renderRoutingPanel,
  sourceListingUrl,
} from '../App'
import { getDraftSourceLabel } from '../features/ai/ui'
import VerificationBadge from '../features/premium_numbers/VerificationBadge'

type CandidateCardSelection = {
  checked: boolean
  onToggle: () => void
}

type CandidateCardProps = {
  item: Candidate
  searchSection?: string
  isSearchHighlighted?: boolean
  draftValue: string
  onDraftChange: (value: string) => void
  draftTextSize: string | null | undefined
  enabledAttachmentNames: string[]
  activeResumeName?: string | null
  parserExpanded: boolean
  onToggleParserExpanded: () => void
  selection?: CandidateCardSelection
  isSending: boolean
  onApprove: (item: Candidate) => void
  isRegenerating: boolean
  onRegenerate: (id: number) => void
  onRetryDetection: (id: number) => void
  isRejecting: boolean
  onReject: (id: number) => void
  isMovingToFailedMapping: boolean
  onSendToFailedMapping: (id: number) => void
  onToggleTracking: (id: number) => void
  sentDetailsExpanded: boolean
  onToggleSentDetails: (id: number) => void
  sentDetailsLoading: boolean
  sentDetailsError: string | undefined
  sentDetails: SentItemDetails | undefined
}

/** A quiet marker for a title the extractor did not produce.
 *
 * Deliberately not a warning badge: most of these are perfectly good titles, and
 * a red flag on a third of the queue is a flag people learn to ignore. It says
 * where the value came from and stops there. An "extracted" role gets nothing,
 * so the absence of a marker is itself the signal.
 */
export function roleProvenanceHint(source: string | null | undefined): { label: string; detail: string } | null {
  switch (source) {
    case 'extracted':
      return null
    case 'taxonomy_matched':
      return { label: 'matched', detail: 'Matched against your approved job-role vocabulary, not extracted from this email.' }
    case 'subject_fallback':
      return { label: 'from subject', detail: 'Extraction found no job title, so the email subject is standing in. Unverified.' }
    case 'source_parent':
      return { label: 'multi-role', detail: 'This email lists several roles; the individual requirements carry the real titles.' }
    case 'unknown':
      return { label: 'unverified', detail: 'No job title could be determined for this email.' }
    default:
      // NULL - written before provenance tracking existed. Deliberately renders
      // nothing, even though the DATA meaning is "unverified".
      //
      // Every one of the 8,508 pre-existing rows carries NULL, so badging them
      // would put an identical marker on every card in the queue. A marker on
      // 100% of rows carries no information and trains people to ignore the
      // badge, which costs us the cases that matter. Machine consumers - chatbot
      // context, aggregation - must still treat NULL as unverified; that is a
      // different consumer with different stakes, not a contradiction.
      return null
  }
}

export default function CandidateCard({
  item,
  searchSection,
  isSearchHighlighted,
  draftValue,
  onDraftChange,
  draftTextSize,
  enabledAttachmentNames,
  activeResumeName,
  parserExpanded,
  onToggleParserExpanded,
  selection,
  isSending,
  onApprove,
  isRegenerating,
  onRegenerate,
  onRetryDetection,
  isRejecting,
  onReject,
  isMovingToFailedMapping,
  onSendToFailedMapping,
  onToggleTracking,
  sentDetailsExpanded,
  onToggleSentDetails,
  sentDetailsLoading,
  sentDetailsError,
  sentDetails,
}: CandidateCardProps) {
  const routingTrusted = canTrustRouting(item)
  const verdict = getOverallVerdict(item, draftValue, routingTrusted)
  const parserDetails = normalizeParserDetails(item.parser_details)
  const atsStrength = getAtsStrengthLabel(item.ats_score)
  const atsTone = atsStrength === 'Strong' ? 'active' : atsStrength === 'Moderate' ? 'pending' : 'flagged'
  const hasCandidateBadges = Boolean(
    item.ats_score != null || item.premium_status || item.premium_verification_level || item.following_badge,
  )
  const requiresResumeForApproval = item.source === 'gmail'
  const sendsEmailOnApproval = true
  const approvalResumeName = item.resume_file_name || activeResumeName || ''
  const structuralSendabilityBlock = [
    'source_parent',
    'superseded_multi_role',
    'manifest_review',
    'extraction_review',
    'score_review',
  ].includes(item.sendability_status ?? '')
  const historicalSafetyBlock =
    item.screening_mode == null &&
    ['blocked_ineligible', 'eligibility_review', 'mandatory_resume_fail', 'mandatory_resume_review']
      .includes(item.sendability_status ?? '')
  const screeningAllowsApproval =
    !structuralSendabilityBlock &&
    !historicalSafetyBlock &&
    (item.screening_mode !== 'strict' || item.sendability_status === 'sendable')
  const canApprove =
    screeningAllowsApproval &&
    Boolean(item.recipient_email) &&
    Boolean(item.cc_email) &&
    Boolean(draftValue?.trim()) &&
    (!requiresResumeForApproval || Boolean(item.resume_file_name)) &&
    routingTrusted

  return (
    <article
      className={`emailItem ${isSearchHighlighted ? 'emailSearchHighlight' : ''}`}
      data-email-search-section={searchSection}
      data-email-search-related-id={searchSection ? item.id : undefined}
    >
      <div className="candidateCardTop">
        {selection ? (
          <input
            type="checkbox"
            className="emailItemCheckbox candidateCardCheckbox"
            aria-label={`Select candidate ${item.id}`}
            checked={selection.checked}
            onChange={selection.onToggle}
          />
        ) : null}

        <div className="candidateCardHeaderMain">
          <h3 className="candidateCardTitle">
            {item.role || 'Unknown Role'}
            {roleProvenanceHint(item.role_source) ? (
              <span className="roleProvenanceHint" title={roleProvenanceHint(item.role_source)?.detail}>
                {roleProvenanceHint(item.role_source)?.label}
              </span>
            ) : null}
          </h3>
          <p className="candidateCardSubtitle">
            {item.location || '-'}
            {' · '}{item.salary_text || 'Salary not specified'}
            {' · '}{jdSummarySkills(item).join(', ') || '-'}
          </p>
          <p className="candidateCardMeta"><strong>From:</strong> {item.sender}</p>
          <p className="candidateCardMeta"><strong>Subject:</strong> {item.subject}</p>
          {item.is_multi_role_child ? (
            <p className="candidateCardMeta"><strong>Requirement:</strong> {item.requirement_index ?? '-'} of {item.requirement_count ?? '-'}</p>
          ) : null}
          {sourceListingUrl(item) || item.gmail_message_url ? (
            <p className="candidateCardMeta candidateCardLinks">
              {sourceListingUrl(item) ? (
                <a href={sourceListingUrl(item)!} target="_blank" rel="noreferrer">Open source listing</a>
              ) : null}
              {item.gmail_message_url ? (
                <a href={item.gmail_message_url} target="_blank" rel="noreferrer">Open exact email in Gmail</a>
              ) : null}
            </p>
          ) : null}
          <p className="candidateCardMeta"><strong>To/CC:</strong> {item.recipient_email ?? '-'} / {item.cc_email ?? '-'}</p>
          <p className="candidateCardRecordId">Record ID: {item.record_id ?? '-'}</p>
        </div>

        {hasCandidateBadges ? (
          <div className="candidateCardBadges" aria-label="Candidate status badges">
            {item.ats_score != null ? (
              <span className={`statusBadge statusBadge--lg statusBadge--${atsTone}`}>
                ATS {atsStrength} · {formatAtsScore(item.ats_score)}
              </span>
            ) : null}
            {item.premium_status ? <span className={`statusBadge statusBadge--${item.premium_status === 'Active' ? 'active' : 'flagged'}`}>{item.premium_status}</span> : null}
            {item.premium_verification_level ? <VerificationBadge level={item.premium_verification_level} /> : null}
            {item.following_badge ? <span className="statusBadge statusBadge--pending" title={item.following_warning ?? undefined}>{item.following_badge === 'active' ? 'Active Following' : item.following_badge === 'tracked' ? 'Tracked' : 'Bookmarked Requirement'}</span> : null}
          </div>
        ) : null}
      </div>

      <section className="detailSection">
        <h4>Routing &amp; Screening</h4>
        {renderRoutingPanel(item)}
        <p><strong>Resume:</strong> {approvalResumeName ? `${approvalResumeName}${!item.resume_file_name ? ' (account default)' : ''}` : '-'}</p>
        <p><strong>Sendability:</strong> {item.sendability_status ?? 'legacy evaluation'}</p>
        {item.role_manifest_status === 'single_fallback' ? (
          <p className="subtle">Auto-resolved as one role because a confident split was unavailable.</p>
        ) : null}
        <p><strong>Screening Mode:</strong> {item.screening_mode ?? 'historical / not recorded'}</p>
        {item.eligibility_status ? <p><strong>Eligibility:</strong> {item.eligibility_status}</p> : null}
        {item.eligibility_details ? (
          <details>
            <summary>Eligibility diagnostics</summary>
            <pre>{JSON.stringify(item.eligibility_details, null, 2)}</pre>
          </details>
        ) : null}
        {item.inherited_constraints?.length ? (
          <details>
            <summary>Inherited source constraints</summary>
            <pre>{JSON.stringify(item.inherited_constraints, null, 2)}</pre>
          </details>
        ) : null}
        {item.role_manifest_diagnostics ? (
          <details>
            <summary>Role manifest diagnostics</summary>
            <pre>{JSON.stringify(item.role_manifest_diagnostics, null, 2)}</pre>
          </details>
        ) : null}
      </section>

      <section className="detailSection">
        <h4>Resume Match</h4>
        <ResumePickerPanel candidate={item} />
        <p><strong>Attachment files:</strong> {(enabledAttachmentNames.length > 0 ? enabledAttachmentNames : item.attachment_file_names ?? []).join(', ') || '-'}</p>
      </section>

      <section className="detailSection">
        <h4>Draft</h4>
        <p>
          <strong>Draft source:</strong> {getDraftSourceLabel(item.draft_source)}
          {item.draft_model ? ` (${item.draft_model})` : ''}
        </p>
        <p><strong>Resume Context:</strong> {getResumeContextLabel(item.draft_resume_context_status)}</p>
        <ParserDetailsPanel
          candidateId={item.id}
          source={item.source}
          parserDetails={parserDetails}
          atsScore={item.ats_score}
          atsSource={item.ats_score_source}
          atsSummary={item.ats_summary}
          atsBreakdown={item.ats_breakdown}
          resumePickerBreakdown={item.resume_picker_breakdown}
          expanded={parserExpanded}
          onToggle={onToggleParserExpanded}
        />
        {item.draft_ai_error ? <p className="subtle"><strong>AI fallback:</strong> {item.draft_ai_error}</p> : null}
        <p><strong>Draft:</strong></p>
        <div className="draftUnified">
          <label className="draftPaneLabel" htmlFor={`draft-${item.id}`}>Editable Draft</label>
          <textarea
            id={`draft-${item.id}`}
            value={draftValue}
            rows={10}
            onChange={(e) => onDraftChange(e.target.value)}
          />
          {/* A <label> with no form control is meaningless; the preview is a div. */}
          <span className="draftPaneLabel">Live Preview</span>
          <div
            className="draftPreview"
            style={draftTextSizeToPreviewStyle(draftTextSize)}
            dangerouslySetInnerHTML={{ __html: draftToPreviewHtml(draftValue) }}
          />
        </div>
        {item.last_error ? <p className="errorMessage"><strong>Last Error:</strong> {item.last_error}</p> : null}
      </section>
      <div className="rowBtns">
        <button
          type="button"
          className="sendActionButton"
          onClick={() => {
            if (sendsEmailOnApproval && !window.confirm(`Send this application to ${item.recipient_email || 'the recruiter'} now? This emails them directly and cannot be undone.`)) return
            onApprove(item)
          }}
          disabled={!canApprove || isSending}
          title={
            !canApprove
              ? requiresResumeForApproval
                ? 'Safe routing, To, CC, body, and resume are required before send'
                : 'Safe routing, To, CC, and body are required before send'
              : 'Approve and send'
          }
        >
          {isSending ? 'Sending...' : 'Approve & Send'}
        </button>
        <button
          type="button"
          onClick={() => onRegenerate(item.id)}
          disabled={isRegenerating || isSending || isRejecting || isMovingToFailedMapping}
          title="Re-run analysis and draft generation; splitting a multi-role requirement requires confirmation"
        >
          {isRegenerating ? 'Regenerating...' : 'Regenerate'}
        </button>
        {['invalid', 'uncertain'].includes(item.role_manifest_status ?? '') || item.sendability_status === 'superseded_multi_role' ? (
          <button
            type="button"
            onClick={() => onRetryDetection(item.source_parent_email_id ?? item.id)}
            disabled={isRegenerating}
          >
            {isRegenerating ? 'Detecting...' : 'Retry Detection'}
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => onReject(item.id)}
          disabled={isRejecting}
        >
          {isRejecting ? 'Rejecting...' : 'Reject'}
        </button>
        <button
          type="button"
          onClick={() => onSendToFailedMapping(item.id)}
          disabled={isMovingToFailedMapping}
          title="Move to Failed Mapping so recipients can be remapped"
        >
          {isMovingToFailedMapping ? 'Moving...' : 'Send to Failed Mapping'}
        </button>
        <button type="button" onClick={() => onToggleTracking(item.id)}>{item.marked_for_tracking ? 'Remove Tracking' : 'Track Application'}</button>
        <button type="button" onClick={() => onToggleSentDetails(item.id)}>
          {sentDetailsExpanded ? 'Hide Sourcing Audit Trail' : 'Sourcing Audit Trail'}
        </button>
        <span className={`verdictBadge verdict-${verdict.tone}`} title="Overall Verdict">
          {verdict.label} • {verdict.score}
        </span>
      </div>
      {sentDetailsExpanded ? (
        <div className="parserDetailsPanel">
          {sentDetailsLoading ? <p className="subtle">Loading contact details...</p> : null}
          {sentDetailsError ? <p className="errorMessage">{sentDetailsError}</p> : null}
          {sentDetails ? renderContactDetailsGrid(sentDetails, item) : null}
        </div>
      ) : null}
    </article>
  )
}
