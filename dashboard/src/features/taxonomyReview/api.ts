import type {
  BulkReviewApplyResponse,
  BulkReviewClassifyResponse,
  BulkReviewScope,
} from './types'

async function responseError(response: Response, fallback: string): Promise<Error> {
  const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null
  const detail = payload?.detail
  return new Error(typeof detail === 'string' ? detail : fallback)
}

export async function classifyPending(
  apiBase: string,
  scope: BulkReviewScope,
  useModel: boolean,
): Promise<BulkReviewClassifyResponse> {
  const response = await fetch(`${apiBase}/settings/taxonomy/bulk-review/classify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope, use_model: useModel }),
  })
  if (!response.ok) throw await responseError(response, 'Failed to classify pending records')
  return (await response.json()) as BulkReviewClassifyResponse
}

// `keys` and `expectedCount` are sent together on purpose: the server refuses the
// request unless they agree, so a preview the user never saw cannot be applied.
export async function applyBulkReview(
  apiBase: string,
  scope: BulkReviewScope,
  action: 'approve' | 'dismiss',
  keys: string[],
  expectedCount: number,
): Promise<BulkReviewApplyResponse> {
  const response = await fetch(`${apiBase}/settings/taxonomy/bulk-review/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope, action, keys, expected_count: expectedCount }),
  })
  if (!response.ok) throw await responseError(response, `Failed to ${action} the selected records`)
  return (await response.json()) as BulkReviewApplyResponse
}
