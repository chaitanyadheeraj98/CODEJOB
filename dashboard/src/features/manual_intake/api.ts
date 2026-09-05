export type ManualDuplicate = {
  id: number
  role: string
  client: string
  created_at: string
}

export type ManualPreview = {
  duplicate_of: ManualDuplicate | null
}

export type ManualJob = {
  run_key: string
  job_id: string
  status: string
}

export type ManualJobStatus = {
  run_key: string
  status: string
  detail: string
  processed_items: number | null
  total_items: number | null
  progress_pct: number | null
}

// The statuses a run stops at. Anything else means the worker is still going.
export const TERMINAL_STATUSES = new Set(['ok', 'failed', 'cancelled', 'error'])

async function readDetail(res: Response, fallback: string): Promise<string> {
  // The server's own detail names what is actually wrong - too long, already
  // running, empty. A generic message sends the user back to re-paste blind.
  const body = await res.json().catch(() => null)
  const detail = body?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && typeof detail.code === 'string') return detail.code
  return fallback
}

export async function previewRequirement(apiBase: string, text: string): Promise<ManualPreview> {
  const res = await fetch(`${apiBase}/manual-requirements/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!res.ok) throw new Error(await readDetail(res, 'Could not check for duplicates'))
  return res.json()
}

export async function createRequirement(
  apiBase: string,
  text: string,
  acknowledgedDuplicateOf: number | null,
): Promise<ManualJob> {
  const res = await fetch(`${apiBase}/manual-requirements`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, acknowledged_duplicate_of: acknowledgedDuplicateOf }),
  })
  if (!res.ok) throw new Error(await readDetail(res, 'Could not queue the requirement'))
  return res.json()
}

export async function fetchJobStatus(apiBase: string, runKey: string): Promise<ManualJobStatus> {
  const res = await fetch(`${apiBase}/jobs/${encodeURIComponent(runKey)}`)
  if (!res.ok) throw new Error(await readDetail(res, 'Could not read the run status'))
  return res.json()
}
