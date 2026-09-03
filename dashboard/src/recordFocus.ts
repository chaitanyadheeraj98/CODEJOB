export type FocusTarget = { section: string; recruiter_email_id: number | null } | null

/**
 * Returns the id of a focused Needs Review record that is not on screen, or null.
 *
 * Auto-pagination keeps loading pages until a focused record appears, so a miss
 * only counts once there are no pages left — otherwise the notice would flash
 * during a normal deep link into a long queue. Without it, a record excluded by
 * an active filter scrolls nowhere and explains nothing.
 */
export function focusedCandidateMissing(
  target: FocusTarget,
  loaded: Array<{ id: number }>,
  hasMorePages: boolean,
): number | null {
  if (target?.section !== 'needs_review') return null
  const candidateId = target.recruiter_email_id
  if (candidateId == null || hasMorePages) return null
  return loaded.some((item) => item.id === candidateId) ? null : candidateId
}
