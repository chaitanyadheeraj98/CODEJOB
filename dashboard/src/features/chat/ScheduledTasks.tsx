import type { ScheduledTasksData } from './renderers'

type Props = { data: ScheduledTasksData; surface: 'page' | 'compact' }

const KIND_LABELS: Record<string, string> = {
  reminder: 'Reminder',
  digest: 'Digest',
  monitor: 'Monitor',
  workflow: 'Workflow',
  checklist: 'Checklist',
}

function formatNextRun(value: string | null): string {
  if (!value) return 'Not scheduled'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'Not scheduled'
  return parsed.toLocaleString()
}

// Every task states three things: when it runs, what it may do, and whether it
// is currently running at all. A scheduled task the user cannot see is one they
// cannot stop, so nothing here is collapsed behind a toggle.
export default function ScheduledTasks({ data, surface }: Props) {
  if (!data.tasks.length) {
    return <div className="chat-scheduled chat-scheduled-empty">Nothing is scheduled.</div>
  }

  return (
    <div className={`chat-scheduled chat-scheduled-${surface}`}>
      <ul className="chat-scheduled-list">
        {data.tasks.map((task) => (
          <li key={task.id} className="chat-scheduled-item">
            <div className="chat-scheduled-head">
              <span className="chat-scheduled-title">{task.title}</span>
              <span className={`chat-scheduled-status chat-scheduled-status-${task.status}`}>
                {task.status}
              </span>
            </div>
            <div className="chat-scheduled-meta">
              <span className="chat-scheduled-kind">{KIND_LABELS[task.kind] ?? task.kind}</span>
              <span className="chat-scheduled-trigger">{task.trigger}</span>
            </div>
            <div className="chat-scheduled-next">Next run: {formatNextRun(task.next_run_at)}</div>
            {/* Server-computed from the kind, never a static label the client
                chose: a user reading "changes nothing" must be reading what
                the task will actually do. */}
            <div className="chat-scheduled-permits">{task.permitted_actions}</div>
            {task.last_error ? (
              <div className="chat-scheduled-error">Last error: {task.last_error}</div>
            ) : null}
          </li>
        ))}
      </ul>
      {data.truncated ? (
        <p className="chat-scheduled-note">Some tasks are not shown. Open Scheduled tasks for the full list.</p>
      ) : null}
      {data.granularityNote ? (
        <p className="chat-scheduled-note">{data.granularityNote}</p>
      ) : null}
    </div>
  )
}
