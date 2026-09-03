import { useMemo, useState } from 'react'

import { useChat } from './chatContext'
import ProposalCard from './ProposalCard'
import { PROPOSAL_HANDLERS } from './proposals'
import type { CandidateTableCell, CandidateTableData } from './renderers'

type CandidateTableProps = {
  messageId: number
  data: CandidateTableData
}

const COLUMN_LABELS: Record<string, string> = {
  role: 'Role',
  role_canonical: 'Canonical role',
  location: 'Location',
  sender: 'From',
  subject: 'Subject',
  score: 'AI score',
  ats_score: 'ATS score',
  state: 'Status',
  decision: 'Decision',
  created_at: 'Received',
}

function label(column: string): string {
  return COLUMN_LABELS[column] ?? column.replace(/_/g, ' ')
}

function display(value: CandidateTableCell): string {
  if (value == null || value === '') return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2)
  return value
}

// Nulls sort last in both directions - an unscored candidate is not "the worst
// match", it is one nobody has scored, and burying it under real zeros hides that.
function compare(a: CandidateTableCell, b: CandidateTableCell): number {
  if (a == null && b == null) return 0
  if (a == null) return 1
  if (b == null) return -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a).localeCompare(String(b))
}

export default function CandidateTable({ messageId, data }: CandidateTableProps) {
  const chat = useChat()
  const [sort, setSort] = useState<{ column: string; direction: 'asc' | 'desc' } | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [pendingKey, setPendingKey] = useState<string | null>(null)

  const rows = useMemo(() => {
    if (!sort) return data.rows
    const sorted = [...data.rows].sort((a, b) => compare(a[sort.column], b[sort.column]))
    return sort.direction === 'desc' ? sorted.reverse() : sorted
  }, [data.rows, sort])

  const toggleSort = (column: string) => setSort((current) => (
    current?.column === column && current.direction === 'asc'
      ? { column, direction: 'desc' }
      : { column, direction: 'asc' }
  ))

  const toggle = (candidateId: number) => setSelected((current) => (
    current.includes(candidateId)
      ? current.filter((id) => id !== candidateId)
      : [...current, candidateId]
  ))

  const result = chat.proposalResults[messageId]
  const approvable = rows.filter((row) => row.state === 'needs_review').map((row) => row.candidate_id)
  const selectedApprovable = selected.filter((id) => approvable.includes(id))

  // Built here, not by the model. Tool results are never replayed into the
  // model's history, so it cannot act on a table it drew earlier - and it does
  // not need to: this routes to the same /candidates/approve-bulk the Needs
  // Review selection bar uses, through the same confirmation card.
  const proposal = pendingKey ? {
    handler: PROPOSAL_HANDLERS.propose_bulk_approve_candidates,
    fields: {
      action: 'approve_candidates',
      candidate_ids: selectedApprovable,
      count: selectedApprovable.length,
      idempotency_key: pendingKey,
    },
  } : null

  if (!data.rows.length) {
    return (
      <div className="candidateTable">
        {data.title ? <h4>{data.title}</h4> : null}
        <p className="subtle">No candidates matched.</p>
      </div>
    )
  }

  return (
    <div className="candidateTable">
      {data.title ? <h4>{data.title}</h4> : null}
      <div className="candidateTableScroll">
        <table>
          <thead>
            <tr>
              <th scope="col"><span className="visuallyHidden">Select</span></th>
              {data.columns.map((column) => (
                <th key={column} scope="col">
                  <button
                    type="button"
                    className="candidateTableSort"
                    onClick={() => toggleSort(column)}
                    aria-label={`Sort by ${label(column)}`}
                  >
                    {label(column)}
                    {sort?.column === column ? <span aria-hidden="true">{sort.direction === 'asc' ? ' ▲' : ' ▼'}</span> : null}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.candidate_id} className={selected.includes(row.candidate_id) ? 'selected' : ''}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.includes(row.candidate_id)}
                    onChange={() => toggle(row.candidate_id)}
                    aria-label={`Select candidate ${row.candidate_id}`}
                  />
                </td>
                {data.columns.map((column, index) => (
                  <td key={column}>
                    {index === 0 ? (
                      <button
                        type="button"
                        className="candidateTableOpen"
                        onClick={() => chat.focusCandidate(row.candidate_id)}
                      >
                        {display(row[column])}
                      </button>
                    ) : display(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.truncated ? <p className="subtle">Showing the first {data.rows.length} candidates.</p> : null}
      {data.dropped.length ? (
        <p className="subtle">
          {data.dropped.length} not shown: {data.dropped.map((entry) => entry.candidate_id ?? entry.column).join(', ')}.
        </p>
      ) : null}

      {selected.length && !proposal && !result ? (
        <div className="candidateTableActions">
          <span>{selected.length} selected</span>
          <button
            type="button"
            onClick={() => setPendingKey(crypto.randomUUID())}
            disabled={!selectedApprovable.length}
            title={selectedApprovable.length ? undefined : 'Only candidates in Needs Review can be approved'}
          >
            Approve {selectedApprovable.length || ''}
          </button>
          <button type="button" onClick={() => setSelected([])}>Clear</button>
        </div>
      ) : null}

      {proposal ? (
        <ProposalCard
          handler={proposal.handler}
          fields={proposal.fields}
          result={result}
          busy={chat.proposalBusyId === messageId}
          disabled={chat.proposalBusyId != null}
          onApprove={() => void chat.approveProposal(messageId, proposal)}
          // Only drops the pending action. Recording a 'cancelled' result here
          // would be permanent - the table would go inert and the user could
          // never select again, because that result is keyed by the message id
          // the table itself lives on.
          onCancel={() => { setPendingKey(null); setSelected([]) }}
        />
      ) : null}
    </div>
  )
}
