import { useEffect, useState } from 'react'

import type { ActiveTool, CompletedTool } from './types'

// What each tool is actually doing, in the user's terms. A tool with no entry
// falls back to its own name rather than a vague "working..." - if a new tool
// ships without a label here, the screen should still say which one is running.
const TOOL_LABELS: Record<string, string> = {
  propose_taxonomy_bulk_review: 'Reviewing pending taxonomy values with DeepSeek',
  propose_bulk_approve_candidates: 'Preparing a bulk approval',
  propose_candidate_action: 'Preparing a bulk action',
  propose_send_email: 'Drafting an email',
  propose_nvoids_search: 'Preparing an Nvoids search',
  propose_manual_requirement: 'Reading the requirement',
  propose_record_update: 'Preparing a record update',
  propose_scheduled_task: 'Preparing a scheduled task',
  search_candidates: 'Searching your candidates',
  render_candidate_table: 'Building a table',
  get_chart: 'Building a chart',
  get_metrics: 'Reading your metrics',
  search_web: 'Searching the web',
  get_relationships: 'Looking for related requirements',
}

// Roughly how long each tool takes, measured. Used only to pace the bar, never
// to claim completion: the bar eases toward 90% over this long and waits there,
// so a slow call never shows a full bar for a turn that has not finished.
const TOOL_EXPECTED_SECONDS: Record<string, number> = {
  // Measured against the live queue: ~6s to read the pending set plus one wave
  // of DeepSeek calls whose slowest member lands between 15s and 47s.
  propose_taxonomy_bulk_review: 45,
  search_web: 10,
}
const DEFAULT_EXPECTED_SECONDS = 8

// What a finished tool's status means, in the user's terms. The server clamps
// `status` to this vocabulary before it is sent, so an unknown value here is a
// new enum member rather than tool prose - it falls back to nothing rather than
// printing a raw token at the user.
const STATUS_NOTES: Record<string, string> = {
  error: 'failed',
  missing_fields: 'needs more detail',
  refused: 'declined',
}

// Kept for the length of the turn, so a turn that ran four lookups reads as four
// steps rather than one long unexplained wait. Cleared when the next turn starts.
function CompletedTools({ completed }: { completed: CompletedTool[] }) {
  if (!completed.length) return null
  return (
    <ul className="toolProgressDone">
      {completed.map((item, index) => {
        const note = STATUS_NOTES[item.status]
        return (
          <li key={`${item.name}:${index}`}>
            <span>{TOOL_LABELS[item.name] ?? item.name}</span>
            <span className="toolProgressDoneMeta">
              {note ? `${note} · ` : ''}{(item.duration_ms / 1000).toFixed(1)}s
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export function ToolProgress({ tool, completed = [] }: { tool: ActiveTool | null; completed?: CompletedTool[] }) {
  if (!tool) {
    return (
      <div className="toolProgress" role="status" aria-live="polite">
        <CompletedTools completed={completed} />
        <span className="toolProgressLabel">Thinking…</span>
      </div>
    )
  }
  return <RunningTool tool={tool} completed={completed} />
}

function RunningTool({ tool, completed }: { tool: ActiveTool; completed: CompletedTool[] }) {
  const [elapsedMs, setElapsedMs] = useState(() => Date.now() - tool.startedAt)

  // No synchronous reset here: callers pass key={tool.startedAt}, so a new tool
  // remounts this and the useState initializer above does that job instead.
  useEffect(() => {
    const timer = window.setInterval(() => setElapsedMs(Date.now() - tool.startedAt), 250)
    return () => window.clearInterval(timer)
  }, [tool.startedAt])

  const expected = (TOOL_EXPECTED_SECONDS[tool.name] ?? DEFAULT_EXPECTED_SECONDS) * 1000
  // Asymptotic, so it never reaches 100% on its own. A bar that fills and then
  // sits there is a worse lie than a bar that is still visibly moving.
  const percent = Math.min(90, 90 * (1 - Math.exp(-elapsedMs / expected)))
  const seconds = Math.floor(elapsedMs / 1000)
  const label = TOOL_LABELS[tool.name] ?? tool.name

  return (
    <div className="toolProgress" role="status" aria-live="polite">
      <CompletedTools completed={completed} />
      <div className="toolProgressHead">
        <span className="toolProgressLabel">{label}</span>
        <span className="toolProgressElapsed">{seconds}s</span>
      </div>
      <div
        className="toolProgressTrack"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuetext={`${label}, ${seconds} seconds elapsed`}
      >
        <div className="toolProgressFill" style={{ width: `${percent}%` }} />
      </div>
      {seconds >= 20 ? (
        <p className="toolProgressNote">
          Still running. Large queues take a while — nothing has been changed yet.
        </p>
      ) : null}
    </div>
  )
}
