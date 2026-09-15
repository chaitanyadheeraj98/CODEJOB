import type { ApplicationCard } from '../premium_numbers/types'
import { toJourney } from './journey'

function dateTimeLabel(value: string | null): string {
  if (!value) return 'Unscheduled'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '--' : date.toLocaleString()
}

export default function JourneyColumn({ application }: { application: ApplicationCard }) {
  const nodes = toJourney(application)

  return (
    <section className="applicationJourney" aria-label="Application journey">
      <h4>Application journey</h4>
      <ol className="applicationJourneyList">
        {nodes.map((node) => (
          <li key={node.id} className={`applicationJourneyNode applicationJourneyNode--${node.state}${node.parentId ? ' applicationJourneyNode--branch' : ''}`}>
            <span className="applicationJourneyMarker" aria-hidden="true">{node.kind === 'terminal' || node.state === 'failed' ? '×' : node.state === 'waiting' ? '…' : '✓'}</span>
            <div>
              <strong>{node.title}</strong>
              <time dateTime={node.at ?? undefined}>{dateTimeLabel(node.at)}</time>
              {node.detail.note ? <p>{node.detail.note}</p> : null}
              {node.detail.first_reached ? <small>First reached {dateTimeLabel(node.detail.first_reached)}</small> : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}
