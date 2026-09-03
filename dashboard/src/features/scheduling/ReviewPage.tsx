import { useCallback, useEffect, useState } from 'react'

import { SchedulingDisabled, approveRun, discardRun, fetchPendingWork, fetchRuns } from './api'
import type { PendingWorkItem, PreparedItem, ScheduledRun } from './api'

type Props = { apiBase: string }

type Loaded = { items: PendingWorkItem[]; error: string; disabled: boolean }

function stamp(value: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString()
}

// One review surface. Whatever produced the pending work - a scheduled run or
// an application suggestion - it appears here. Two inboxes is a product defect
// the user experiences as missed work.
export default function ReviewPage({ apiBase }: Props) {
  const [items, setItems] = useState<PendingWorkItem[]>([])
  const [error, setError] = useState('')
  const [disabled, setDisabled] = useState(false)
  const [openRunId, setOpenRunId] = useState<number | null>(null)
  const [prepared, setPrepared] = useState<PreparedItem[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [edits, setEdits] = useState<Record<string, Record<string, unknown>>>({})
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')

  const load = useCallback(async (): Promise<Loaded> => {
    try {
      return { items: await fetchPendingWork(apiBase), error: '', disabled: false }
    } catch (caught) {
      if (caught instanceof SchedulingDisabled) return { items: [], error: '', disabled: true }
      return { items: [], error: (caught as Error).message, disabled: false }
    }
  }, [apiBase])

  useEffect(() => {
    let active = true
    load().then((result) => {
      if (!active) return
      setItems(result.items)
      setError(result.error)
      setDisabled(result.disabled)
    })
    return () => { active = false }
  }, [load])

  const refresh = useCallback(() => {
    load().then((result) => {
      setItems(result.items)
      setError(result.error)
      setDisabled(result.disabled)
      setOpenRunId(null)
      setPrepared([])
      setSelected(new Set())
      setEdits({})
    })
  }, [load])

  const openRun = useCallback((item: PendingWorkItem) => {
    if (item.source !== 'scheduled_run') return
    if (openRunId === item.source_id) {
      setOpenRunId(null)
      return
    }
    // task_id, not subject_id: subject_id is the record the work is about.
    fetchRuns(apiBase, item.task_id)
      .then((result) => {
        const run: ScheduledRun | undefined = result.runs.find((row) => row.id === item.source_id)
        setPrepared(run?.items ?? [])
        setSelected(new Set((run?.items ?? []).map((row) => row.item_id)))
        setOpenRunId(item.source_id)
      })
      .catch((caught: Error) => setError(caught.message))
  }, [apiBase, openRunId])

  const toggle = useCallback((itemId: string) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(itemId)) next.delete(itemId)
      else next.add(itemId)
      return next
    })
  }, [])

  const approve = useCallback((runId: number, itemIds: string[]) => {
    setBusy(true)
    approveRun(apiBase, runId, itemIds, edits)
      .then((result) => {
        setNotice(
          result.failed
            ? `${result.approved} approved, ${result.failed} failed: ${result.errors.join('; ')}`
            : `${result.approved} approved.`,
        )
        setError('')
        refresh()
      })
      .catch((caught: Error) => setError(caught.message))
      .finally(() => setBusy(false))
  }, [apiBase, edits, refresh])

  const discard = useCallback((runId: number) => {
    setBusy(true)
    discardRun(apiBase, runId)
      .then(() => { setNotice('Discarded.'); setError(''); refresh() })
      .catch((caught: Error) => setError(caught.message))
      .finally(() => setBusy(false))
  }, [apiBase, refresh])

  if (disabled) {
    return (
      <section className="scheduling-page">
        <p className="scheduling-empty">Scheduling is not enabled on this deployment.</p>
      </section>
    )
  }

  return (
    <section className="scheduling-page">
      {error ? <p className="scheduling-error">{error}</p> : null}
      {notice ? <p className="scheduling-notice">{notice}</p> : null}
      {!items.length && !error ? (
        <p className="scheduling-empty">Nothing is waiting for your review.</p>
      ) : null}

      <ul className="scheduling-list">
        {items.map((item) => (
          <li key={`${item.source}-${item.source_id}`} className="scheduling-pending">
            <div className="scheduling-task-head">
              <span className="scheduling-task-title">{item.title}</span>
              <span className="scheduling-badge">
                {item.source === 'scheduled_run' ? 'Scheduled run' : 'Suggestion'}
              </span>
            </div>
            <p className="scheduling-pending-detail">{item.detail}</p>
            <dl className="scheduling-task-facts">
              <div><dt>Prepared</dt><dd>{stamp(item.prepared_at)}</dd></div>
              <div><dt>Expires</dt><dd>{stamp(item.expires_at)}</dd></div>
              <div><dt>Items</dt><dd>{item.item_count}</dd></div>
            </dl>

            {item.source === 'scheduled_run' ? (
              <>
                <div className="scheduling-task-actions">
                  <button type="button" onClick={() => openRun(item)} aria-expanded={openRunId === item.source_id}>
                    {openRunId === item.source_id ? 'Hide items' : 'Review items'}
                  </button>
                  <button type="button" disabled={busy} onClick={() => approve(item.source_id, [])}>
                    Approve all
                  </button>
                  <button type="button" disabled={busy} onClick={() => discard(item.source_id)}>
                    Discard
                  </button>
                </div>

                {openRunId === item.source_id && prepared.length ? (
                  <div className="scheduling-prepared">
                    <ul>
                      {prepared.map((row) => (
                        <li key={row.item_id}>
                          <label>
                            <input
                              type="checkbox"
                              checked={selected.has(row.item_id)}
                              onChange={() => toggle(row.item_id)}
                            />
                            <span>{row.summary}</span>
                          </label>
                          {row.editable_fields.map((field) => (
                            <label key={field} className="scheduling-edit">
                              <span>{field.replace(/_/g, ' ')}</span>
                              <input
                                type="text"
                                defaultValue={String(row.payload[field] ?? '')}
                                onChange={(event) => setEdits((current) => ({
                                  ...current,
                                  [row.item_id]: { ...(current[row.item_id] ?? {}), [field]: event.target.value },
                                }))}
                              />
                            </label>
                          ))}
                        </li>
                      ))}
                    </ul>
                    <button
                      type="button"
                      disabled={busy || !selected.size}
                      onClick={() => approve(item.source_id, [...selected])}
                    >
                      Approve {selected.size} selected
                    </button>
                  </div>
                ) : null}
              </>
            ) : (
              <p className="scheduling-pending-detail">
                Review this suggestion on the Application tracking page.
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
