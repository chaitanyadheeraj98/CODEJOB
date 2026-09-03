export type ScheduledTask = {
  id: number
  title: string
  kind: string
  status: string
  schedule_kind: string
  cron_expression: string
  timezone: string
  trigger: string
  next_run_at: string | null
  last_run_at: string | null
  last_error: string
  consecutive_failures: number
  retention_hours: number
  permitted_actions: string
  subject_type: string
  subject_id: string
  run_count: number
}

export type PreparedItem = {
  item_id: string
  action: string
  record_kind: string
  record_id: number
  summary: string
  payload: Record<string, unknown>
  editable_fields: string[]
}

export type ScheduledRun = {
  id: number
  task_id: number
  started_at: string | null
  finished_at: string | null
  outcome: string
  item_count: number
  approved_count: number
  expires_at: string | null
  expired_at: string | null
  expiry_reason: string
  error: string
  items: PreparedItem[]
}

export type ChecklistItem = {
  id: number
  position: number
  text: string
  done: boolean
  done_at: string | null
}

export type PendingWorkItem = {
  source: string
  source_id: number
  // The task a scheduled run belongs to. Zero for suggestions. Distinct from
  // subject_id, which is the record the work is about.
  task_id: number
  title: string
  detail: string
  subject_type: string
  subject_id: string
  prepared_at: string | null
  expires_at: string | null
  item_count: number
  approve_endpoint: string
}

export class SchedulingDisabled extends Error {}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  // Every scheduling route 404s when the feature is off, so a 404 on the list
  // means "not enabled here", not "your data is missing".
  if (response.status === 404) throw new SchedulingDisabled('Scheduling is not enabled.')
  if (!response.ok) {
    let detail = fallback
    try {
      const body = await response.json() as { detail?: string }
      if (body?.detail) detail = body.detail
    } catch {
      // Keep the fallback: a non-JSON error body is still an error.
    }
    throw new Error(detail)
  }
  return await response.json() as T
}

export async function fetchTasks(apiBase: string, status = 'all'): Promise<ScheduledTask[]> {
  const response = await fetch(`${apiBase}/scheduled-tasks?status=${encodeURIComponent(status)}`)
  const body = await readJson<{ tasks: ScheduledTask[] }>(response, 'Could not load scheduled tasks')
  return body.tasks
}

export async function fetchRuns(
  apiBase: string,
  taskId: number,
): Promise<{ runs: ScheduledRun[]; items: ChecklistItem[] }> {
  const response = await fetch(`${apiBase}/scheduled-tasks/${taskId}/runs`)
  return await readJson(response, 'Could not load run history')
}

export async function patchTask(
  apiBase: string,
  taskId: number,
  body: Record<string, unknown>,
): Promise<ScheduledTask> {
  const response = await fetch(`${apiBase}/scheduled-tasks/${taskId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return await readJson(response, 'Could not update the task')
}

export async function deleteTask(apiBase: string, taskId: number): Promise<void> {
  const response = await fetch(`${apiBase}/scheduled-tasks/${taskId}`, { method: 'DELETE' })
  await readJson(response, 'Could not delete the task')
}

export async function toggleChecklistItem(
  apiBase: string,
  taskId: number,
  itemId: number,
  done: boolean,
): Promise<ChecklistItem> {
  const response = await fetch(`${apiBase}/scheduled-tasks/${taskId}/items/${itemId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ done }),
  })
  return await readJson(response, 'Could not update the item')
}

export async function fetchPendingWork(apiBase: string): Promise<PendingWorkItem[]> {
  const response = await fetch(`${apiBase}/scheduled-tasks/pending-work`)
  const body = await readJson<{ items: PendingWorkItem[] }>(response, 'Could not load pending work')
  return body.items
}

export async function approveRun(
  apiBase: string,
  runId: number,
  itemIds: string[],
  edits: Record<string, Record<string, unknown>> = {},
): Promise<{ approved: number; failed: number; outcome: string; errors: string[] }> {
  const response = await fetch(`${apiBase}/scheduled-tasks/runs/${runId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_ids: itemIds, edits }),
  })
  return await readJson(response, 'Could not approve this run')
}

export async function discardRun(apiBase: string, runId: number): Promise<ScheduledRun> {
  const response = await fetch(`${apiBase}/scheduled-tasks/runs/${runId}/discard`, { method: 'POST' })
  return await readJson(response, 'Could not discard this run')
}
