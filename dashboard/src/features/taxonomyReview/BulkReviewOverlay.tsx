import { useEffect, useMemo, useState } from 'react'

import { applyBulkReview, classifyPending } from './api'
import type {
  BulkReviewApplyResponse,
  BulkReviewRecommendation,
  BulkReviewScope,
  Decision,
} from './types'

// Matches SETTINGS_REVIEW_BATCH_SIZE in App.tsx. The settings queues already page
// long lists this way, so a 1,600-row bucket needs no new virtualization.
const REVIEW_PAGE_SIZE = 50
const PREVIEW_NAME_LIMIT = 25

type Phase = 'classifying' | 'reviewing' | 'previewing' | 'applying' | 'done'

type BulkReviewOverlayProps = {
  scope: BulkReviewScope
  title: string
  apiBase: string
  onClose: () => void
  onApplied: () => void
}

const REASON_LABELS: Record<string, string> = {
  malformed_or_recoverable: 'malformed or contains known skills',
  unsafe_name: 'not a well-formed name',
  safe_repeated: 'clean name, seen 2+ times',
  safe_singleton: 'clean name, seen once',
  needs_review: 'looks like a fragment',
}

const GROUPS: Array<{ decision: Decision; label: string }> = [
  { decision: 'approve', label: 'Approve' },
  { decision: 'dismiss', label: 'Dismiss' },
  { decision: 'skip', label: 'Needs Review' },
]

// The classifier's bucket becomes the starting decision. `review` maps to `skip`,
// never to an action: the third bucket exists precisely because nothing in it
// should be written without someone looking at it.
function initialDecision(item: BulkReviewRecommendation): Decision {
  if (item.bucket === 'approve') return 'approve'
  if (item.bucket === 'dismiss') return 'dismiss'
  return 'skip'
}

function nameList(names: string[]): string {
  const shown = names.slice(0, PREVIEW_NAME_LIMIT).join(', ')
  const rest = names.length - Math.min(names.length, PREVIEW_NAME_LIMIT)
  return rest > 0 ? `${shown}, and ${rest} more` : shown
}

export function BulkReviewOverlay({ scope, title, apiBase, onClose, onApplied }: BulkReviewOverlayProps) {
  const [phase, setPhase] = useState<Phase>('classifying')
  const [useModel, setUseModel] = useState(true)
  const [recommendations, setRecommendations] = useState<BulkReviewRecommendation[]>([])
  const [decisions, setDecisions] = useState<Record<string, Decision>>({})
  const [modelError, setModelError] = useState<string | null>(null)
  const [modelUsed, setModelUsed] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [results, setResults] = useState<BulkReviewApplyResponse[]>([])
  const [visibleCounts, setVisibleCounts] = useState<Record<Decision, number>>({
    approve: REVIEW_PAGE_SIZE,
    dismiss: REVIEW_PAGE_SIZE,
    skip: REVIEW_PAGE_SIZE,
  })

  // Every setState lands in a promise callback rather than the effect body, the
  // same shape CompanyInventoryTab uses for its list fetch. `phase` already starts
  // at 'classifying' and the toggle resets it, so nothing has to be set up front.
  // Re-running on `useModel` is deliberate: flipping the toggle re-classifies.
  useEffect(() => {
    classifyPending(apiBase, scope, useModel)
      .then((payload) => {
        setRecommendations(payload.recommendations)
        setDecisions(
          Object.fromEntries(payload.recommendations.map((item) => [item.key, initialDecision(item)])),
        )
        setModelError(payload.model_error)
        setModelUsed(payload.model_used)
        setPhase('reviewing')
      })
      .catch((reason) => {
        setError((reason as Error).message)
        setPhase('reviewing')
      })
  }, [apiBase, scope, useModel])

  const grouped = useMemo(() => {
    const buckets: Record<Decision, BulkReviewRecommendation[]> = { approve: [], dismiss: [], skip: [] }
    for (const item of recommendations) buckets[decisions[item.key] ?? 'skip'].push(item)
    return buckets
  }, [recommendations, decisions])

  const setDecision = (key: string, decision: Decision) => {
    setDecisions((current) => ({ ...current, [key]: decision }))
  }

  const setGroupDecision = (from: Decision, to: Decision) => {
    setDecisions((current) => {
      const next = { ...current }
      for (const item of recommendations) {
        if ((current[item.key] ?? 'skip') !== from) continue
        // A locked record can be dismissed or left, never approved - the server
        // refuses it, so offering it here would only produce a skipped row.
        if (to === 'approve' && item.locked) continue
        next[item.key] = to
      }
      return next
    })
  }

  const approveKeys = grouped.approve.map((item) => item.key)
  const dismissKeys = grouped.dismiss.map((item) => item.key)

  const runApply = async () => {
    setPhase('applying')
    setError('')
    const collected: BulkReviewApplyResponse[] = []
    try {
      if (approveKeys.length > 0) {
        collected.push(await applyBulkReview(apiBase, scope, 'approve', approveKeys, approveKeys.length))
      }
      if (dismissKeys.length > 0) {
        collected.push(await applyBulkReview(apiBase, scope, 'dismiss', dismissKeys, dismissKeys.length))
      }
      setResults(collected)
      setPhase('done')
      onApplied()
    } catch (e) {
      setError((e as Error).message)
      setResults(collected)
      // Approve and dismiss are two requests, so the first can land before the
      // second fails. The confirmation screen says "nothing has been changed
      // yet", which stops being true the moment one of them succeeds - show the
      // result screen instead, and refresh the queue behind it.
      if (collected.length > 0) {
        setPhase('done')
        onApplied()
      } else {
        setPhase('previewing')
      }
    }
  }

  const appliedTotal = results.reduce((total, item) => total + item.applied_count, 0)
  const skippedAll = results.flatMap((item) => item.skipped)

  return (
    <div className="bulkReviewOverlay" role="dialog" aria-modal="true" aria-label={`Bulk review - ${title}`}>
      <div className="bulkReviewPanel">
        <header className="bulkReviewHeader">
          <div>
            <p className="detailPanelEyebrow">Bulk review</p>
            <h3>{title}</h3>
            <p className="subtle">
              {phase === 'classifying'
                ? 'Classifying pending records...'
                : `${recommendations.length} pending record${recommendations.length === 1 ? '' : 's'}`}
              {modelUsed ? ` · refined by ${modelUsed}` : ''}
            </p>
          </div>
          <button type="button" onClick={onClose}>Close</button>
        </header>

        {error ? <p className="skillUpgradeWarning">{error}</p> : null}
        {modelError ? (
          <p className="subtle bulkReviewNotice">
            Model refinement unavailable ({modelError}). Showing rules-only recommendations.
          </p>
        ) : null}

        {phase === 'classifying' ? <p className="subtle bulkReviewBody">Classifying...</p> : null}

        {phase === 'reviewing' ? (
          <>
            <div className="bulkReviewBody">
              <label className="bulkReviewToggle">
                <input
                  type="checkbox"
                  checked={useModel}
                  onChange={(event) => {
                    setPhase('classifying')
                    setError('')
                    setUseModel(event.target.checked)
                  }}
                />
                Refine Needs Review with DeepSeek
              </label>

              {GROUPS.map(({ decision, label }) => {
                const items = grouped[decision]
                const visible = items.slice(0, visibleCounts[decision])
                const remaining = items.length - visible.length
                return (
                  <section key={decision} className="bulkReviewGroup">
                    <div className="skillUpgradeColumnHeader">
                      <h4>
                        {label} ({items.length})
                      </h4>
                      <div className="rowBtns">
                        {decision !== 'approve' ? (
                          <button type="button" onClick={() => setGroupDecision(decision, 'approve')}>
                            Approve all here
                          </button>
                        ) : null}
                        {decision !== 'dismiss' ? (
                          <button type="button" onClick={() => setGroupDecision(decision, 'dismiss')}>
                            Dismiss all here
                          </button>
                        ) : null}
                        {decision !== 'skip' ? (
                          <button type="button" onClick={() => setGroupDecision(decision, 'skip')}>
                            Leave all here
                          </button>
                        ) : null}
                      </div>
                    </div>
                    {items.length === 0 ? <p className="subtle">Nothing in this group.</p> : null}
                    <div className="skillUpgradeList">
                      {visible.map((item) => (
                        <article key={item.key} className="skillUpgradeItem">
                          <div className="skillUpgradeItemHeader">
                            <strong className="skillUpgradeName">{item.display_name}</strong>
                            <span className="skillUpgradeBadge">
                              {item.occurrence_count} hit{item.occurrence_count === 1 ? '' : 's'}
                            </span>
                          </div>
                          <p className="subtle skillUpgradeMeta">
                            {REASON_LABELS[item.reason] ?? item.reason} · {item.source}
                          </p>
                          <p className="subtle skillUpgradeMeta">
                            Candidate IDs: {item.candidate_ids.length > 0 ? item.candidate_ids.join(', ') : '-'}
                          </p>
                          {item.locked ? (
                            <p className="skillUpgradeWarning">
                              This looks malformed. Approve is disabled; dismiss or leave it.
                            </p>
                          ) : null}
                          <div className="skillUpgradeActions">
                            <button
                              type="button"
                              className={decision === 'approve' ? 'primary' : ''}
                              disabled={item.locked}
                              aria-pressed={decision === 'approve'}
                              onClick={() => setDecision(item.key, 'approve')}
                            >
                              Approve
                            </button>
                            <button
                              type="button"
                              className={decision === 'dismiss' ? 'primary' : ''}
                              aria-pressed={decision === 'dismiss'}
                              onClick={() => setDecision(item.key, 'dismiss')}
                            >
                              Dismiss
                            </button>
                            <button
                              type="button"
                              className={decision === 'skip' ? 'primary' : ''}
                              aria-pressed={decision === 'skip'}
                              onClick={() => setDecision(item.key, 'skip')}
                            >
                              Leave
                            </button>
                          </div>
                        </article>
                      ))}
                      {remaining > 0 ? (
                        <button
                          type="button"
                          onClick={() =>
                            setVisibleCounts((counts) => ({
                              ...counts,
                              [decision]: counts[decision] + REVIEW_PAGE_SIZE,
                            }))
                          }
                        >
                          Show {Math.min(REVIEW_PAGE_SIZE, remaining)} more
                        </button>
                      ) : null}
                    </div>
                  </section>
                )
              })}
            </div>

            <footer className="bulkReviewFooter">
              <span>
                Approve {approveKeys.length} · Dismiss {dismissKeys.length} · Leave {grouped.skip.length}
              </span>
              <button
                type="button"
                className="primary"
                disabled={approveKeys.length === 0 && dismissKeys.length === 0}
                onClick={() => setPhase('previewing')}
              >
                Preview &amp; confirm
              </button>
            </footer>
          </>
        ) : null}

        {phase === 'previewing' || phase === 'applying' ? (
          <div className="bulkReviewBody">
            <h4>Confirm before anything is written</h4>
            <p className="subtle">Nothing has been changed yet. Applying writes exactly these records.</p>
            <section className="bulkReviewGroup">
              <h4>Approve {approveKeys.length}</h4>
              <p className="subtle">
                {approveKeys.length > 0 ? nameList(grouped.approve.map((item) => item.display_name)) : 'Nothing.'}
              </p>
            </section>
            <section className="bulkReviewGroup">
              <h4>Dismiss {dismissKeys.length}</h4>
              <p className="subtle">
                {dismissKeys.length > 0 ? nameList(grouped.dismiss.map((item) => item.display_name)) : 'Nothing.'}
              </p>
            </section>
            <footer className="bulkReviewFooter">
              <button type="button" onClick={() => setPhase('reviewing')} disabled={phase === 'applying'}>
                Back
              </button>
              <button type="button" className="primary" onClick={runApply} disabled={phase === 'applying'}>
                {phase === 'applying'
                  ? 'Applying...'
                  : `Apply — approve ${approveKeys.length}, dismiss ${dismissKeys.length}`}
              </button>
            </footer>
          </div>
        ) : null}

        {phase === 'done' ? (
          <div className="bulkReviewBody">
            <h4>Done</h4>
            <p>
              Applied {appliedTotal} record{appliedTotal === 1 ? '' : 's'}.
              {skippedAll.length > 0
                ? ` Skipped ${skippedAll.length} (${[...new Set(skippedAll.map((item) => item.reason))].join(', ')}).`
                : ''}
            </p>
            <footer className="bulkReviewFooter">
              <button type="button" className="primary" onClick={onClose}>Close</button>
            </footer>
          </div>
        ) : null}
      </div>
    </div>
  )
}
