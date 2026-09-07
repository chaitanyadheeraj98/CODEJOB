export type BulkReviewScope = 'skill' | 'company' | 'location' | 'role'

export type BulkReviewBucket = 'approve' | 'dismiss' | 'review'

// What the user has chosen for one record. 'skip' means "leave it pending" and is
// the default for everything the classifier could not decide - a record is never
// written because nobody looked at it.
export type Decision = 'approve' | 'dismiss' | 'skip'

export type BulkReviewRecommendation = {
  // The record's normalized_name. Pending records are aggregates over emails and
  // have no integer id; this is the same key the settings panels already use.
  key: string
  display_name: string
  occurrence_count: number
  candidate_ids: number[]
  bucket: BulkReviewBucket
  reason: string
  source: 'rules' | 'model'
  // Rules dismissed this as malformed. The server refuses to approve it, so the
  // UI must not offer to.
  locked: boolean
}

export type BulkReviewClassifyResponse = {
  scope: string
  total_pending: number
  counts: Record<string, number>
  model_used: string | null
  model_error: string | null
  recommendations: BulkReviewRecommendation[]
}

export type BulkReviewApplyResponse = {
  scope: string
  action: string
  applied_count: number
  applied_names: string[]
  skipped: Array<{ key: string; reason: string }>
}
