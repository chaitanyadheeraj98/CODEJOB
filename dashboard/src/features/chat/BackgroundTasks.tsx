import { useCallback, useEffect, useRef, useState } from 'react'

import {
  isActive,
  jobLabel,
  listBackgroundJobs,
  progressText,
  statusLabel,
  stopBackgroundJob,
  type BackgroundJob,
} from './backgroundJobs'

// Open, the panel is a live view and has to keep up with a bar that moves.
// Closed, it exists only to keep the badge count honest, and 3s of polling for
// a number that changes every few minutes is a request per user per 3s for
// nothing.
const OPEN_POLL_MS = 3000
const CLOSED_POLL_MS = 20000

function elapsed(startedAt: string): string {
  const started = new Date(startedAt).getTime()
  if (Number.isNaN(started)) return ''
  const seconds = Math.max(0, Math.round((Date.now() - started) / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

function JobRow({ job, apiBase, onChanged }: { job: BackgroundJob; apiBase: string; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState('')
  const active = isActive(job)

  const stop = async () => {
    setStopping(true)
    setError('')
    try {
      await stopBackgroundJob(apiBase, job.run_key)
      onChanged()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not stop that task')
    } finally {
      setStopping(false)
    }
  }

  return (
    <li className={`bgTask bgTask-${job.status}`}>
      <button
        type="button"
        className="bgTaskHead"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="bgTaskName">{jobLabel(job)}</span>
        <span className="bgTaskStatus">{statusLabel(job.status)}</span>
      </button>

      {/* The bar is the worker's own count, never a timer. A job that stalls
          holds its position rather than easing toward a finish it has not
          reached. */}
      {active ? (
        <div className="bgTaskBar" role="progressbar" aria-valuenow={Math.round(job.progress_pct ?? 0)} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${Math.min(100, Math.max(0, job.progress_pct ?? 0))}%` }} />
        </div>
      ) : null}

      {open ? (
        <div className="bgTaskBody">
          <p className="bgTaskDetail">{job.detail}</p>
          <dl className="bgTaskFacts">
            <div><dt>Progress</dt><dd>{progressText(job)}</dd></div>
            <div><dt>Started</dt><dd>{elapsed(job.created_at)} ago</dd></div>
            {job.queue_name ? <div><dt>Queue</dt><dd>{job.queue_name}</dd></div> : null}
            {job.failed_count ? <div><dt>Failed</dt><dd>{job.failed_count}</dd></div> : null}
            {job.skipped_item_count ? <div><dt>Skipped</dt><dd>{job.skipped_item_count}</dd></div> : null}
          </dl>
          {/* The run key is the only handle that survives a reload, and it is
              what the assistant is told to quote when asked about a run. */}
          <code className="bgTaskKey">{job.run_key}</code>
          {active ? (
            <button type="button" className="bgTaskStop" onClick={() => void stop()} disabled={stopping}>
              {stopping ? 'Stopping...' : 'Stop task'}
            </button>
          ) : null}
          {error ? <p className="bgTaskError" role="alert">{error}</p> : null}
        </div>
      ) : null}
    </li>
  )
}

// What the assistant started and left running. Until this existed the only
// record of a queued job was the sentence on the proposal card that started it,
// which meant a search the user could neither watch nor stop - and an assistant
// that had to guess whether the last one had finished.
export function BackgroundTasks({ apiBase }: { apiBase: string }) {
  const [open, setOpen] = useState(false)
  const [jobs, setJobs] = useState<BackgroundJob[]>([])
  const [activeCount, setActiveCount] = useState(0)
  const [error, setError] = useState('')
  // Bumped after a stop, to pull the list forward rather than leaving the user
  // looking at a row that still says "running" until the next tick.
  const [reloadToken, setReloadToken] = useState(0)
  const root = useRef<HTMLDivElement | null>(null)
  const refresh = useCallback(() => setReloadToken((current) => current + 1), [])

  useEffect(() => {
    // A poll in flight when the widget closes would otherwise land on an
    // unmounted component - the same guard the status fetch in ChatProvider
    // uses, for the same reason.
    let live = true
    const load = () => {
      listBackgroundJobs(apiBase).then(
        (payload) => {
          if (!live) return
          setJobs(payload.items)
          setActiveCount(payload.active_count)
          setError('')
        },
        (reason: unknown) => {
          if (live) setError(reason instanceof Error ? reason.message : 'Could not read background tasks')
        },
      )
    }
    load()
    const timer = window.setInterval(load, open ? OPEN_POLL_MS : CLOSED_POLL_MS)
    return () => {
      live = false
      window.clearInterval(timer)
    }
  }, [apiBase, open, reloadToken])

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="bgTasks" ref={root}>
      <button
        type="button"
        className="bgTasksTrigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        title="Background tasks"
      >
        Tasks
        {activeCount ? <span className="bgTasksBadge">{activeCount}</span> : null}
      </button>

      {open ? (
        <div className="bgTasksPanel" role="dialog" aria-label="Background tasks">
          <header className="bgTasksPanelHead">
            <strong>Background tasks</strong>
            <small>{activeCount ? `${activeCount} running` : 'Nothing running'}</small>
          </header>
          {error ? <p className="bgTaskError" role="alert">{error}</p> : null}
          {!jobs.length && !error ? (
            <p className="bgTasksEmpty">No background work has been started yet.</p>
          ) : (
            <ul className="bgTasksList">
              {jobs.map((job) => (
                <JobRow key={job.run_key} job={job} apiBase={apiBase} onChanged={refresh} />
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  )
}
