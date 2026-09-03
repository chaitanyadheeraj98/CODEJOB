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
export type QueueLinkData = {
  page: string
  tab: string | null
  label: string
  title: string
  filters: Record<string, string>
  dropped: Array<{ key: string; reason: string }>
}

export type RenderedPayload =
  | { kind: 'candidate_table'; data: CandidateTableData }
  | { kind: 'queue_link'; data: QueueLinkData }

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

function asStringMap(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const out: Record<string, string> = {}
  for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
    if (typeof entry === 'string') out[key] = entry
  }
  return out
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
  navigate_to_queue: {
    parse: (message) => {
      try {
        const payload = JSON.parse(message.content) as Record<string, unknown>
        if (!payload || payload.action !== 'navigate_to_queue') return null
        if (typeof payload.page !== 'string' || !payload.page) return null
        return {
          kind: 'queue_link',
          data: {
            page: payload.page,
            tab: typeof payload.tab === 'string' ? payload.tab : null,
            label: typeof payload.label === 'string' ? payload.label : payload.page,
            title: typeof payload.title === 'string' ? payload.title : '',
            filters: asStringMap(payload.filters),
            dropped: Array.isArray(payload.dropped) ? payload.dropped as QueueLinkData['dropped'] : [],
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
