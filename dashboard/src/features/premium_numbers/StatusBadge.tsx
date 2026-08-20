type StatusBadgeProps = {
  status: 'Active' | 'Pending' | 'Flagged'
}

export function StatusBadge({ status }: StatusBadgeProps) {
  return <span className={`statusBadge statusBadge--${status.toLowerCase()}`}>{status}</span>
}

export function CategoryChip({ category }: { category: 'Recruiter' | 'Employer' }) {
  return <span className="categoryChip">{category}</span>
}
