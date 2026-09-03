import { useCallback, useEffect, useState } from 'react'

import {
  SchedulingDisabled,
  deleteTask,
  fetchRuns,
  fetchTasks,
  patchTask,
  toggleChecklistItem,
} from './api'
import type { ChecklistItem, ScheduledRun, ScheduledTask } from './api'

type Props = { apiBase: string }

type Loaded = { tasks: ScheduledTask[]; error: string; disabled: boolean }

const KIND_LABELS: Record<string, string> = {
  reminder: 'Reminder',
  digest: 'Digest',
  monitor: 'Monitor',
  workflow: 'Workflow',
  checklist: 'Checklist',
}

const OUTCOME_LABELS: Record<string, string> = {
  pending: 'Awaiting review',
  approved: 'Approved',
  partially_approved: 'Partly approved',
  discarded: 'Discarded',
  expired: 'Expired',
  failed: 'Failed',
  notified: 'Notified',
}

function stamp(value: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString()
}

// temp157 §8.1: the user must always see what will happen, when it runs, and
// what actions it may take. Every task appears here whatever its kind - a
// scheduled task the user cannot see is one they cannot stop.
export default function ScheduledTasksPage({ apiBase }: Props) {
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [error, setError] = useState('')
  const [disabled, setDisabled] = useState(false)
  const [openTaskId, setOpenTaskId] = useState<number | null>(null)
  const [runs, setRuns] = useState<ScheduledRun[]>([])
  const [items, setItems] = useState<ChecklistItem[]>([])
  const [busy, setBusy] = useState(false)

  // Returns what it fetched rather than writing state, so every caller decides
  // whether the result still applies - the pattern useChatSession established.
  const load = useCallback(async (): Promise<Loaded> => {
    try {
      return { tasks: await fetchTasks(apiBase), error: '', disabled: false }
    } catch (caught) {
      if (caught instanceof SchedulingDisabled) {
        return { tasks: [], error: '', disabled: true }
      }
      return { tasks: [], error: (caught as Error).message, disabled: false }
    }
  }, [apiBase])

  useEffect(() => {
    let active = true
    load().then((result) => {
      if (!active) return
      setTasks(result.tasks)
      setError(result.error)
      setDisabled(result.disabled)
    })
    return () => { active = false }
  }, [load])

  const refresh = useCallback(() => {
    load().then((result) => {
      setTasks(result.tasks)
      setError(result.error)
      setDisabled(result.disabled)
    })
  }, [load])

  const openTask = useCallback((taskId: number) => {
    if (openTaskId === taskId) {
      setOpenTaskId(null)
      return
    }
    fetchRuns(apiBase, taskId)
      .then((result) => {
        setRuns(result.runs)
        setItems(result.items)
        setOpenTaskId(taskId)
      })
      .catch((caught: Error) => setError(caught.message))
  }, [apiBase, openTaskId])

  const act = useCallback((run: () => Promise<unknown>) => {
    setBusy(true)
    run()
      .then(() => { setError(''); refresh() })
      .catch((caught: Error) => setError(caught.message))
      .finally(() => setBusy(false))
  }, [refresh])

  if (disabled) {
    return (
      <section className="scheduling-page">
        <p className="scheduling-empty">
          Scheduling is not enabled on this deployment.
        </p>
      </section>
    )
  }

  return (
    <section className="scheduling-page">
      {error ? <p className="scheduling-error">{error}</p> : null}
      {!tasks.length && !error ? (
        <p className="scheduling-empty">
          Nothing is scheduled. Ask the assistant to remind you about something.
        </p>
      ) : null}

      <ul className="scheduling-list">
        {tasks.map((task) => (
          <li key={task.id} className={`scheduling-task scheduling-task-${task.status}`}>
            <div className="scheduling-task-head">
              <button
                type="button"
                className="scheduling-task-title"
                onClick={() => openTask(task.id)}
                aria-expanded={openTaskId === task.id}
              >
                {task.title}
              </button>
              <span className={`scheduling-badge scheduling-badge-${task.status}`}>{task.status}</span>
            </div>

            <dl className="scheduling-task-facts">
              <div><dt>Kind</dt><dd>{KIND_LABELS[task.kind] ?? task.kind}</dd></div>
              {/* Composed by the server from the schedule and the zone. The
                  zone is always named: "every weekday at 9am" is a different
                  promise in two different zones. */}
              <div><dt>Runs</dt><dd>{task.trigger}</dd></div>
              <div><dt>Next run</dt><dd>{stamp(task.next_run_at)}</dd></div>
              <div><dt>Last run</dt><dd>{stamp(task.last_run_at)}</dd></div>
              {/* Server-computed from the kind, never a static label per kind
                  chosen by the client. */}
              <div><dt>May do</dt><dd>{task.permitted_actions}</dd></div>
              <div><dt>Keeps work for</dt><dd>{task.retention_hours} hours</dd></div>
            </dl>

            {task.last_error ? (
              <p className="scheduling-task-error">
                Last error: {task.last_error}
                {task.consecutive_failures > 0 ? ` (${task.consecutive_failures} in a row)` : ''}
              </p>
            ) : null}

            <div className="scheduling-task-actions">
              {task.status === 'active' ? (
                <button type="button" disabled={busy} onClick={() => act(() => patchTask(apiBase, task.id, { operation: 'pause' }))}>
                  Pause
                </button>
              ) : null}
              {task.status === 'paused' || task.status === 'suspended' ? (
                <button type="button" disabled={busy} onClick={() => act(() => patchTask(apiBase, task.id, { operation: 'resume' }))}>
                  Resume
                </button>
              ) : null}
              <button type="button" disabled={busy} onClick={() => act(() => deleteTask(apiBase, task.id))}>
                Delete
              </button>
            </div>

            {openTaskId === task.id ? (
              <div className="scheduling-task-detail">
                {items.length ? (
                  <ul className="scheduling-checklist">
                    {items.map((item) => (
                      <li key={item.id}>
                        <label>
                          <input
                            type="checkbox"
                            checked={item.done}
                            disabled={busy}
                            onChange={(event) => act(() =>
                              toggleChecklistItem(apiBase, task.id, item.id, event.target.checked)
                                .then((updated) => setItems((current) =>
                                  current.map((row) => (row.id === updated.id ? updated : row)),
                                )),
                            )}
                          />
                          <span className={item.done ? 'scheduling-item-done' : ''}>{item.text}</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                ) : null}

                <h4 className="scheduling-runs-title">Run history</h4>
                {runs.length ? (
                  <ul className="scheduling-runs">
                    {runs.map((run) => (
                      <li key={run.id} className={`scheduling-run scheduling-run-${run.outcome}`}>
                        <span className="scheduling-run-when">{stamp(run.started_at)}</span>
                        <span className="scheduling-run-outcome">
                          {OUTCOME_LABELS[run.outcome] ?? run.outcome}
                        </span>
                        {run.outcome === 'partially_approved' ? (
                          <span className="scheduling-run-count">
                            {run.approved_count} of {run.item_count} approved
                          </span>
                        ) : null}
                        {/* An expired run keeps its prepared work readable and
                            offers no approval control. */}
                        {run.expiry_reason ? (
                          <span className="scheduling-run-reason">{run.expiry_reason}</span>
                        ) : null}
                        {run.error ? <span className="scheduling-run-error">{run.error}</span> : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="scheduling-empty">This task has not run yet.</p>
                )}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}
