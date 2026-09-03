import type { ChatMessage } from './types'

export type CandidateTableCell = string | number | null

export type CandidateTableRow = {
  candidate_id: number
  record_id: string | null
} & Record<string, CandidateTableCell>

export type CandidateTableData = {
  title: string
  columns: string[]
  rows: CandidateTableRow[]
  dropped: Array<{ candidate_id?: number; column?: string; reason: string }>
  truncated: boolean
}

// One member per visualization. The discriminant is what lets a single mount
// point hold more than one kind of output; without it a second handler parses
// correctly and then draws as a candidate table.
export type RenderedPayload =
  | { kind: 'candidate_table'; data: CandidateTableData }

export type RenderHandler = {
  parse: (message: ChatMessage) => RenderedPayload | null
}

function asRows(value: unknown): CandidateTableRow[] | null {
  if (!Array.isArray(value)) return null
  const rows: CandidateTableRow[] = []
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') return null
    const row = entry as Record<string, unknown>
    if (typeof row.candidate_id !== 'number') return null
    rows.push(row as CandidateTableRow)
  }
  return rows
}

// Sibling to PROPOSAL_HANDLERS, for tool messages that display something rather
// than prepare a write. Same defensive shape: parse returns null on anything
// unexpected so a malformed payload degrades to "nothing rendered" instead of
// throwing inside the message list.
export const RENDER_HANDLERS: Record<string, RenderHandler> = {
  render_candidate_table: {
    parse: (message) => {
      try {
        const payload = JSON.parse(message.content) as Record<string, unknown>
        if (!payload || payload.action !== 'render_candidate_table') return null
        const rows = asRows(payload.rows)
        if (rows === null) return null
        const columns = Array.isArray(payload.columns) ? payload.columns.filter((c): c is string => typeof c === 'string') : []
        if (!columns.length) return null
        return {
          kind: 'candidate_table',
          data: {
            title: typeof payload.title === 'string' ? payload.title : '',
            columns,
            rows,
            dropped: Array.isArray(payload.dropped) ? payload.dropped as CandidateTableData['dropped'] : [],
            truncated: payload.truncated === true,
          },
        }
      } catch {
        return null
      }
    },
  },
}

export function renderForMessage(message: ChatMessage): RenderedPayload | null {
  if (message.role !== 'tool' || !message.tool_name) return null
  const handler = RENDER_HANDLERS[message.tool_name]
  if (!handler) return null
  return handler.parse(message)
}
