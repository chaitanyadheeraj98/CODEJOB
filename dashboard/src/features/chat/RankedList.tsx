import { useChat } from './chatContext'
import Provenance from './Provenance'
import type { RankedListData } from './renderers'

type Props = { data: RankedListData; surface: 'page' | 'compact' }

// Rank, subject, measure, and the stored reasons. Every "why" here is a reason
// string the scorer already produced - the moment one has to be derived rather
// than read, the work has crossed into v3.
export default function RankedList({ data, surface }: Props) {
  const chat = useChat()

  if (!data.rows.length) {
    return (
      <figure className="chatAnalysisFigure">
        <p className="subtle">{data.title}: nothing matched.</p>
        <Provenance data={data.provenance} />
      </figure>
    )
  }

  return (
    <figure className="chatAnalysisFigure">
      {data.title ? <strong>{data.title}</strong> : null}
      <ol className="chatRankedList">
        {data.rows.map((row) => (
          <li key={row.record_id}>
            <div className="chatRankedHead">
              {surface === 'page' && row.drill_to ? (
                <button type="button" className="chatRankedLabel" onClick={() => chat.navigateToQueue(row.drill_to!)}>
                  {row.label}
                </button>
              ) : (
                <span className="chatRankedLabel">{row.label}</span>
              )}
              <span className="chatRankedScore">{row.score}</span>
            </div>
            {row.detail ? <p className="subtle">{row.detail}</p> : null}
            {surface === 'page' && row.reasons.length ? (
              <ul className="chatRankedReasons">
                {row.reasons.map((reason) => <li key={reason}>{reason}</li>)}
              </ul>
            ) : null}
          </li>
        ))}
      </ol>
      <Provenance data={data.provenance} />
    </figure>
  )
}
