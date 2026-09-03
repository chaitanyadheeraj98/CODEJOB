import { useState } from 'react'

import { useChat } from './chatContext'
import Confidence from './Confidence'
import EvidencePanel from './EvidencePanel'
import { recordRelationshipJudgment } from './api'
import type { RelationshipClusterData } from './renderers'

type Props = { data: RelationshipClusterData; surface: 'page' | 'compact' }

type Verdict = 'confirmed' | 'rejected'

const VERDICT_LABELS: Record<Verdict, string> = {
  confirmed: 'These belong together',
  rejected: 'Not related',
}

// A grouping nobody entered, shown with the badge the service computed and the
// evidence behind it. The badge is rendered from the payload field, so a model
// describing a Possible link in confident prose still leaves "Possible" beside
// it.
export default function RelationshipCluster({ data, surface }: Props) {
  const chat = useChat()
  const [pending, setPending] = useState<Verdict | null>(null)
  const [status, setStatus] = useState(data.status)
  const [error, setError] = useState('')

  const inferred = Object.entries(data.inferred).filter(([, value]) => value)

  async function judge(verdict: Verdict) {
    setPending(verdict)
    setError('')
    try {
      await recordRelationshipJudgment(chat.apiBase, data.cluster_id, verdict)
      setStatus(verdict === 'confirmed' ? 'confirmed' : 'rejected')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'That did not save.')
    } finally {
      setPending(null)
    }
  }

  return (
    <figure className="chatAnalysisFigure chatRelationship">
      <div className="chatRelationshipHead">
        {data.title ? <strong>{data.title}</strong> : null}
        <Confidence level={data.provenance.confidence} />
      </div>
      {/* Server-composed. The model never writes the sentence beside the badge. */}
      <p className="chatRelationshipClaim">{data.claim}</p>

      <ul className="chatRelationshipMembers">
        {data.members.map((member) => (
          <li key={member.opportunity_id}>
            {surface === 'page' && member.drill_to ? (
              <button type="button" className="chatRankedLabel" onClick={() => chat.navigateToQueue(member.drill_to!)}>
                {member.label}
              </button>
            ) : (
              <span className="chatRankedLabel">{member.label}</span>
            )}
            <Confidence level={member.confidence} />
            {member.detail ? <span className="subtle"> {member.detail}</span> : null}
          </li>
        ))}
      </ul>

      {inferred.length ? (
        // Labelled "inferred" and visually separated: an inferred attribute
        // must never be mistakable for a recorded one.
        <p className="chatRelationshipInferred">
          <span className="chatRelationshipInferredTag">inferred</span>
          {inferred.map(([key, value]) => `${key.replace(/_/g, ' ')}: ${value}`).join(' · ')}
        </p>
      ) : null}

      <EvidencePanel data={data.provenance} />

      {/* Controls on the page surface only. The widget has no room for a
          three-way decision, and a mis-click there is a persisted judgment. */}
      {surface === 'page' && status === 'proposed' ? (
        <div className="chatRelationshipActions">
          {(Object.keys(VERDICT_LABELS) as Verdict[]).map((verdict) => (
            <button
              key={verdict}
              type="button"
              disabled={pending !== null}
              onClick={() => judge(verdict)}
            >
              {pending === verdict ? 'Working...' : VERDICT_LABELS[verdict]}
            </button>
          ))}
        </div>
      ) : null}
      {surface === 'page' && status !== 'proposed' ? (
        <p className="subtle chatRelationshipSettled">
          {status === 'confirmed' ? 'You confirmed this.' : 'You rejected this. It will not be suggested again.'}
        </p>
      ) : null}
      {error ? <p className="chatRelationshipError">{error}</p> : null}
    </figure>
  )
}
