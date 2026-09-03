import { useCallback, useEffect, useState } from 'react'

type FieldSpec = { key: string; label: string; coverage: string }

type Candidate = {
  left_id: number
  right_id: number
  sampler: string
  left: Record<string, string>
  right: Record<string, string>
  score: number
  confidence: string
  fields: FieldSpec[]
}

type Queue = { verdicts: string[]; remaining: number; candidates: Candidate[] }

type Summary = {
  total: number
  by_verdict: Record<string, number>
  by_split: Record<string, number>
  by_sampler: Record<string, number>
  hard_negative_share: number
  target_total: number
  target_hard_negative_share: number
}

type Loaded = { queue: Queue | null; summary: Summary | null; error: string }

type Props = { apiBase: string }

const VERDICT_LABELS: Record<string, string> = {
  same_program: 'Same programme',
  related_distinct: 'Related but distinct',
  unrelated: 'Unrelated',
  unsure: 'Unsure',
}

// Keyboard-first: 150-300 pairs by hand is the real cost of this work item, and
// anything that halves it is worth building.
const SHORTCUTS = ['1', '2', '3', '4']

export default function LabelingTool({ apiBase }: Props) {
  const [queue, setQueue] = useState<Queue | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [index, setIndex] = useState(0)
  const [reason, setReason] = useState('')
  const [revealed, setRevealed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Returns what it fetched rather than writing state, so every caller decides
  // whether the result still applies.
  const load = useCallback(async (): Promise<Loaded | null> => {
    try {
      const [queueResponse, summaryResponse] = await Promise.all([
        fetch(`${apiBase}/relationships/label-queue?limit=25`),
        fetch(`${apiBase}/relationships/labels/summary`),
      ])
      if (queueResponse.status === 404) {
        return { queue: null, summary: null, error: 'Relationship intelligence is switched off on this deployment.' }
      }
      if (!queueResponse.ok) throw new Error('Could not load the label queue.')
      return {
        queue: await queueResponse.json() as Queue,
        summary: summaryResponse.ok ? await summaryResponse.json() as Summary : null,
        error: '',
      }
    } catch (caught) {
      return { queue: null, summary: null, error: caught instanceof Error ? caught.message : 'Could not load the label queue.' }
    }
  }, [apiBase])

  // Loaded through a promise callback rather than an awaited call, matching
  // useChatSession: setState belongs in the callback, not in the effect body.
  useEffect(() => {
    let active = true
    load().then((next) => {
      if (!active || !next) return
      setQueue(next.queue)
      setSummary(next.summary)
      setIndex(0)
      setRevealed(false)
      setError(next.error)
    })
    return () => { active = false }
  }, [load])

  const current = queue?.candidates[index] ?? null

  const submit = useCallback(async (verdict: string) => {
    if (!current || busy) return
    setBusy(true)
    setError('')
    try {
      const response = await fetch(`${apiBase}/relationships/labels`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          left_opportunity_id: current.left_id,
          right_opportunity_id: current.right_id,
          verdict,
          reason,
          sampler: current.sampler,
        }),
      })
      if (!response.ok) throw new Error('That verdict did not save.')
      // Revealed only after the verdict is recorded.
      setRevealed(true)
      setReason('')
      const summaryResponse = await fetch(`${apiBase}/relationships/labels/summary`)
      if (summaryResponse.ok) setSummary(await summaryResponse.json() as Summary)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'That verdict did not save.')
    } finally {
      setBusy(false)
    }
  }, [apiBase, busy, current, reason])

  useEffect(() => {
    const verdicts = queue?.verdicts ?? []
    function onKey(event: KeyboardEvent) {
      if (event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLInputElement) return
      const position = SHORTCUTS.indexOf(event.key)
      if (position >= 0 && verdicts[position]) void submit(verdicts[position])
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [queue, submit])

  if (error && !queue) return <section className="labelingTool"><p className="subtle">{error}</p></section>
  if (!queue) return <section className="labelingTool"><p className="subtle">Loading pairs...</p></section>

  if (!current) {
    return (
      <section className="labelingTool">
        <p className="subtle">
          Nothing left to label in this batch. {queue.remaining} candidate pairs remain overall.
        </p>
        <button
          type="button"
          onClick={() => {
            load().then((next) => {
              if (!next) return
              setQueue(next.queue)
              setSummary(next.summary)
              setIndex(0)
              setRevealed(false)
              setError(next.error)
            })
          }}
        >
          Load more
        </button>
      </section>
    )
  }

  return (
    <section className="labelingTool">
      {summary ? (
        <p className="subtle labelingProgress">
          {summary.total} labelled · target {summary.target_total} ·{' '}
          {Math.round(summary.hard_negative_share * 100)}% hard negatives (target{' '}
          {Math.round(summary.target_hard_negative_share * 100)}%) · {queue.remaining} pairs left
        </p>
      ) : null}

      <p className="subtle">Pair {index + 1} of {queue.candidates.length} · found by: {current.sampler}</p>

      {/* Only the signals the scorer reads, each with its production coverage,
          so a blank reads as a gap in the data rather than in the record. */}
      <table className="labelingTable">
        <thead>
          <tr><th>Signal</th><th>Requirement {current.left_id}</th><th>Requirement {current.right_id}</th></tr>
        </thead>
        <tbody>
          {current.fields.map((field) => (
            <tr key={field.key}>
              <th scope="row">
                {field.label} <span className="subtle labelingCoverage">{field.coverage} filled</span>
              </th>
              <td>{current.left[field.key] || <span className="subtle">not available</span>}</td>
              <td>{current.right[field.key] || <span className="subtle">not available</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="labelingVerdicts">
        {queue.verdicts.map((verdict, position) => (
          <button key={verdict} type="button" disabled={busy} onClick={() => void submit(verdict)}>
            <kbd>{SHORTCUTS[position]}</kbd> {VERDICT_LABELS[verdict] ?? verdict}
          </button>
        ))}
      </div>

      <textarea
        className="labelingReason"
        value={reason}
        placeholder="Why? (optional, but it keeps the set interpretable later)"
        onChange={(event) => setReason(event.target.value)}
      />

      {/* The score is hidden until a verdict is recorded. A labeler who sees it
          first calibrates to the scorer instead of labelling the truth, and the
          set then measures the scorer's agreement with itself. */}
      {revealed ? (
        <div className="labelingRevealed">
          <p>Recorded. The scorer said <strong>{current.confidence}</strong> at {current.score.toFixed(3)}.</p>
          <button
            type="button"
            onClick={() => {
              setRevealed(false)
              setIndex((value) => value + 1)
            }}
          >
            Next pair
          </button>
        </div>
      ) : (
        <p className="subtle labelingHidden">The scorer's own verdict is hidden until you record yours.</p>
      )}

      {error ? <p className="labelingError">{error}</p> : null}
    </section>
  )
}
