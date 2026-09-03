import Provenance from './Provenance'
import type { ComparisonData } from './renderers'

type Props = { data: ComparisonData; surface: 'page' | 'compact' }

// A missing measure renders an em dash, never 0. "This recruiter has never
// replied" and "we have no record of this recruiter" are different claims, and
// a comparison table that conflates them is worse than no table.
function display(value: number | string | null): string {
  if (value == null || value === '') return '—'
  return typeof value === 'number'
    ? (Number.isInteger(value) ? String(value) : value.toFixed(2))
    : value
}

export default function ComparisonTable({ data, surface }: Props) {
  if (!data.columns.length) {
    return (
      <figure className="chatAnalysisFigure">
        <p className="subtle">{data.title}: nothing to compare.</p>
        <Provenance data={data.provenance} />
      </figure>
    )
  }

  const columns = surface === 'compact' ? data.columns.slice(0, 2) : data.columns

  return (
    <figure className="chatAnalysisFigure">
      {data.title ? <strong>{data.title}</strong> : null}
      <div className="chatComparisonScroll">
        <table className="chatComparisonTable">
          <thead>
            <tr>
              <th scope="col">Measure</th>
              {columns.map((column) => <th key={column.record_id} scope="col">{column.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {data.measures.map((measure) => (
              <tr key={measure.key}>
                <th scope="row">{measure.label}</th>
                {columns.map((column) => (
                  <td key={column.record_id}>{display(column.values[measure.key] ?? null)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.dropped.length ? (
        <p className="subtle">Not compared: {data.dropped.map((item) => item.key).join(', ')}</p>
      ) : null}
      <Provenance data={data.provenance} />
    </figure>
  )
}
