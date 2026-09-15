// Everything the Background tasks panel needs to read a queued job. Kept out of
// the component so the naming and the "is it still going" rule can be tested
// without rendering, and so both chat surfaces share one definition of each.

export type BackgroundJob = {
  run_key: string
  run_source: string
  job_id: string | null
  status: string
  detail: string
  processed_items: number
  total_items: number | null
  progress_pct: number | null
  queue_name: string | null
  skipped_item_count: number
  failed_count: number | null
  created_at: string
}

export type BackgroundJobList = {
  items: BackgroundJob[]
  active_count: number
}

// The same set the server calls active. A job in either state is one the user
// can still stop, which is the only reason the panel distinguishes them.
const ACTIVE_STATUSES = new Set(['queued', 'running'])

export function isActive(job: BackgroundJob): boolean {
  return ACTIVE_STATUSES.has(job.status)
}

// `run_source` is the column, but two very different jobs share
// `nvoids_sync`: the scheduled crawl of the whole feed, and the one-off search
// the assistant starts from a confirmed card. Only the run-key prefix tells
// them apart, and a user looking for "the search I just asked for" is looking
// for the second one.
const SOURCE_LABELS: Record<string, string> = {
  gmail_sync: 'Gmail sync',
  nvoids_sync: 'Nvoids feed sync',
  automation_run: 'Recruiter email check',
  manual_intake: 'Manual intake',
}

export function jobLabel(job: BackgroundJob): string {
  if (job.run_key.startsWith('nvoids_client_search:')) return 'Nvoids search'
  return SOURCE_LABELS[job.run_source] ?? job.run_source
}

// What each source is actually counting. A shared "items" would be safe and
// useless; "3 emails" and "500 postings" are different enough that one word for
// both tells the user nothing.
const SOURCE_UNITS: Record<string, [string, string]> = {
  gmail_sync: ['email', 'emails'],
  automation_run: ['email', 'emails'],
  nvoids_sync: ['posting', 'postings'],
  manual_intake: ['item', 'items'],
}

// The label alone is the source, so three automation runs in a row read as one
// repeated word and the user cannot tell which of them to stop. The size is the
// cheapest thing that distinguishes them, and it is the thing being asked about
// ("which one is chewing on the twelve emails?"). Omitted rather than guessed
// while the run is still working out how much there is to do.
export function jobHeadline(job: BackgroundJob): string {
  const label = jobLabel(job)
  if (!job.total_items) return label
  const [one, many] = SOURCE_UNITS[job.run_source] ?? ['item', 'items']
  return `${label} - ${job.total_items} ${job.total_items === 1 ? one : many}`
}

// What each stored status means where the user is reading it. Wider than the
// queue's own vocabulary because an automation run writes its outcome into the
// same column: `ready`, `skipped` and `oauth_required` all come from
// orchestration_service, not from RQ, and all three reach this panel.
const STATUS_LABELS: Record<string, string> = {
  ok: 'Completed',
  ready: 'Completed',
  queued: 'Queued',
  running: 'Running',
  // It processed its emails and queued none of them. "Completed" would be true
  // and useless; the user's next question is whether anything came of it.
  skipped: 'Nothing to do',
  // The one status the user can act on, so it says what to do rather than what
  // went wrong. The row's detail carries the full instruction.
  oauth_required: 'Needs Gmail sign-in',
  failed: 'Failed',
  canceled: 'Stopped',
}

export function statusLabel(status: string): string {
  const known = STATUS_LABELS[status]
  if (known) return known
  // A status this build has no label for is a backend newer than the dashboard,
  // not a bug the user can do anything about. Humanised rather than passed
  // through raw: "oauth_required" shipped to this panel once already, and a
  // snake_case token on screen reads as a leaked internal either way.
  return status.replace(/_/g, ' ').replace(/^./, (first) => first.toUpperCase())
}

// Only ever the worker's own count. `progress_pct` is null until the first item
// is processed, and a queued job showing a bar at 0% is honest in a way that
// synthesising a percentage from elapsed time is not.
export function progressText(job: BackgroundJob): string {
  if (job.total_items == null) return `${job.processed_items} processed`
  return `${job.processed_items} of ${job.total_items}`
}

export async function listBackgroundJobs(apiBase: string): Promise<BackgroundJobList> {
  const response = await fetch(`${apiBase}/jobs?limit=20`)
  if (!response.ok) throw new Error('Could not read background tasks')
  return (await response.json()) as BackgroundJobList
}

export async function stopBackgroundJob(apiBase: string, runKey: string): Promise<BackgroundJob> {
  const response = await fetch(`${apiBase}/jobs/${encodeURIComponent(runKey)}/cancel`, { method: 'POST' })
  if (!response.ok) {
    // 409 is the job having finished between the poll and the click, and its
    // detail carries the status it finished in. Worth repeating rather than
    // flattening to "failed": nothing went wrong, the work is simply done.
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null
    const detail = typeof payload?.detail === 'string' ? payload.detail : ''
    if (detail.startsWith('job_not_cancelable:')) {
      throw new Error(`Already ${statusLabel(detail.split(':')[1] ?? '').toLowerCase() || 'finished'} - nothing to stop.`)
    }
    throw new Error(detail || 'Could not stop that task')
  }
  return (await response.json()) as BackgroundJob
}
