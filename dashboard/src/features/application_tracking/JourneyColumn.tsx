import { useState } from 'react'

import type { ApplicationCard, ApplicationDuplicateSummary } from '../premium_numbers/types'
import JourneyPanel from './JourneyPanel'
import { legalActions, toJourney, type JourneyAction, type JourneyNode } from './journey'

function dateTimeLabel(value: string | null): string {
  if (!value) return 'Unscheduled'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '--' : date.toLocaleString()
}

type Props = {
  application: ApplicationCard
  apiBase: string
  duplicateConflicts: ApplicationDuplicateSummary[]
  onUpdated: (application: ApplicationCard) => void
  onDuplicateConflicts: (conflicts: ApplicationDuplicateSummary[]) => void
  onError: (message: string) => void
}

export default function JourneyColumn({ application, apiBase, duplicateConflicts, onUpdated, onDuplicateConflicts, onError }: Props) {
  const nodes = toJourney(application)
  const actions = legalActions(application)
  const [menuOpen, setMenuOpen] = useState(false)
  const [selectedAction, setSelectedAction] = useState<JourneyAction | null>(null)
  const [selectedNode, setSelectedNode] = useState<JourneyNode | null>(null)

  return (
    <section className="applicationJourney" aria-label="Application journey">
      <h4>Application journey</h4>
      <ol className="applicationJourneyList">
        {nodes.map((node) => (
          <li key={node.id} className={`applicationJourneyNode applicationJourneyNode--${node.state}${node.parentId ? ' applicationJourneyNode--branch' : ''}`}>
            <span className="applicationJourneyMarker" aria-hidden="true">{node.kind === 'terminal' || node.state === 'failed' ? '×' : node.state === 'waiting' ? '…' : '✓'}</span>
            <button type="button" className="applicationJourneyNodeButton" onClick={() => setSelectedNode(node)}>
              <strong>{node.title}</strong>
              <time dateTime={node.at ?? undefined}>{dateTimeLabel(node.at)}</time>
              {node.detail.note ? <p>{node.detail.note}</p> : null}
              {node.detail.first_reached ? <small>First reached {dateTimeLabel(node.detail.first_reached)}</small> : null}
            </button>
          </li>
        ))}
      </ol>
      {actions.length ? (
        <div className="applicationJourneyAdd">
          <button type="button" className="applicationJourneyAddButton" aria-label="Add journey action" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>+</button>
          {menuOpen ? (
            <div className="applicationJourneyActions" role="menu">
              {actions.map((journeyAction) => (
                <button key={journeyAction.kind} type="button" role="menuitem" onClick={() => { setSelectedAction(journeyAction); setMenuOpen(false) }}>{journeyAction.label}</button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {selectedAction ? <JourneyPanel key={selectedAction.kind} application={application} action={selectedAction} duplicateConflicts={duplicateConflicts} apiBase={apiBase} onUpdated={onUpdated} onDuplicateConflicts={onDuplicateConflicts} onError={onError} onClose={() => setSelectedAction(null)} /> : null}
      {selectedNode ? <JourneyPanel application={application} node={selectedNode} duplicateConflicts={duplicateConflicts} apiBase={apiBase} onUpdated={onUpdated} onDuplicateConflicts={onDuplicateConflicts} onError={onError} onClose={() => setSelectedNode(null)} /> : null}
    </section>
  )
}
