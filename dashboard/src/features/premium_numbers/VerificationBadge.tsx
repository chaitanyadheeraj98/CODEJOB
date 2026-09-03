export default function VerificationBadge({ level }: { level: 'unverified' | 'verified' | 'trusted' }) {
  // trusted = strongest positive signal; verified = calm/confirmed (neutral, not alarming);
  // unverified = simply not yet confirmed (amber, "worth a look") — never red, since it isn't
  // a flagged/problem state, just an unknown one. Red is reserved for genuinely flagged contacts.
  return <span className={`statusBadge statusBadge--${level === 'trusted' ? 'active' : level === 'verified' ? 'neutral' : 'pending'}`}>{level[0].toUpperCase() + level.slice(1)}</span>
}
