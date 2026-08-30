type StatusBadgeProps = {
  status: 'Active' | 'Pending' | 'Flagged' | 'Unscored'
  reasonCode?: string | null
}

const REASON_LABELS: Record<string, string> = {
  identity_conflict: 'Identity Conflict',
  insufficient_evidence: 'Needs More Evidence',
  source_attribution_failure: 'Source Mismatch',
  international_number_needs_verification: 'International — Needs Verification',
  new_number: 'Pending',
  suggested_email_match: 'Suggested Email Match',
  suggested_phone_match: 'Suggested Phone Match',
  phone_email_cross_conflict: 'Identity Conflict',
}

function reviewReasonLabel(reasonCode: string): string {
  return REASON_LABELS[reasonCode] ?? reasonCode.replaceAll('_', ' ')
}

export function StatusBadge({ status, reasonCode }: StatusBadgeProps) {
  const label = reasonCode ? reviewReasonLabel(reasonCode) : status
  const tone = reasonCode && reasonCode !== 'new_number' ? reasonCode.replaceAll('_', '-') : status === 'Unscored' ? 'neutral' : status.toLowerCase()
  return <span className={`statusBadge statusBadge--${tone}`}>{label}</span>
}

export function CategoryChip({ category }: { category: 'Recruiter' | 'Employer' }) {
  return <span className="categoryChip">{category}</span>
}
