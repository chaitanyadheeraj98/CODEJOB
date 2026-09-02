import type { CandidateTableCell, CandidateTableData } from './renderers'

// The widget's rendering of the same stored message. Read-only by decision: no
// checkboxes, no sorting, and no row links - clicking a row in a floating panel
// to navigate the page underneath it is disorienting. The point is that the
// same session opened in either surface shows the same history with no gaps.
export default function CandidateTableCompact({ data }: { data: CandidateTableData }) {
  const columns = data.columns.slice(0, 3)

  const display = (value: CandidateTableCell): string => {
    if (value == null || value === '') return '—'
    if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2)
    return value
  }

  if (!data.rows.length) {
    return <p className="subtle">{data.title || 'Candidates'}: none matched.</p>
  }

  return (
    <div className="candidateTableCompact">
      {data.title ? <strong>{data.title}</strong> : null}
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column.replace(/_/g, ' ')}</th>)}</tr>
        </thead>
        <tbody>
          {data.rows.map((row) => (
            <tr key={row.candidate_id}>
              {columns.map((column) => <td key={column}>{display(row[column])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {data.truncated || data.dropped.length ? (
        <small>Open the Assistant page for the full table.</small>
      ) : null}
    </div>
  )
}
