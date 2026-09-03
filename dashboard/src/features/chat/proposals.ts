import type { ChatMessage } from './types'

export type ProposalFields = Record<string, unknown>

export type ProposalHandler = {
  endpoint: string | ((fields: ProposalFields) => string)
  method: 'POST'
  buildBody: (fields: ProposalFields) => unknown
  confirmLabel: (fields: ProposalFields) => string
  summary: (fields: ProposalFields) => Array<[string, string]>
}

function record(value: unknown): ProposalFields {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as ProposalFields : {}
}

function text(value: unknown): string {
  return value == null ? '' : String(value)
}

// Endpoints keyed by the action the tool chose. The server sends its own
// endpoint too, but the client never routes to a server-supplied URL - that
// would let a malformed payload aim a POST anywhere.
const CANDIDATE_ACTION_ENDPOINTS: Record<string, string> = {
  reject: '/candidates/reject-bulk',
  track: '/candidates/track-bulk',
  untrack: '/candidates/track-bulk',
  regenerate: '/candidates/regenerate-bulk',
  send_to_failed_mapping: '/candidates/send-to-failed-mapping-bulk',
}

export const PROPOSAL_HANDLERS: Record<string, ProposalHandler> = {
  propose_candidate_action: {
    endpoint: (fields) => CANDIDATE_ACTION_ENDPOINTS[String(fields.candidate_action)] ?? '',
    method: 'POST',
    buildBody: (fields) => {
      const body: Record<string, unknown> = { ids: fields.candidate_ids }
      if (text(fields.reason)) body.reason = fields.reason
      if (typeof fields.tracked === 'boolean') body.tracked = fields.tracked
      return body
    },
    confirmLabel: (fields) => `${text(fields.label) || 'Apply'} ${Number(fields.count ?? 0)}`,
    summary: (fields) => [
      ['Action', text(fields.label)],
      ['Affected emails', String(Number(fields.count ?? 0))],
      // Reversibility comes from a field the tool set, per the endpoint audit -
      // never from the model's prose. This card is the last thing the user
      // reads before a write.
      ['Reversible', fields.reversible === true ? 'Yes' : 'No'],
      ...(text(fields.reversible_detail) ? [['Detail', text(fields.reversible_detail)] as [string, string]] : []),
      ...(Array.isArray(fields.roles) && fields.roles.length ? [['Roles', fields.roles.join(', ')] as [string, string]] : []),
      ...(text(fields.reason) ? [['Reason', text(fields.reason)] as [string, string]] : []),
      ...(Array.isArray(fields.dropped) && fields.dropped.length
        ? [['Not included', `${fields.dropped.length} email(s) skipped`] as [string, string]]
        : []),
    ],
  },
  propose_bulk_approve_candidates: {
    endpoint: '/candidates/approve-bulk',
    method: 'POST',
    // idempotency_key is optional and absent from model-generated proposals, which
    // are one card and one click. A table selection is far easier to double-submit,
    // so CandidateTable mints a key when it builds the action.
    buildBody: (fields) => ({ ids: fields.candidate_ids, idempotency_key: fields.idempotency_key }),
    confirmLabel: (fields) => `Approve ${Number(fields.count ?? 0)} Emails`,
    summary: (fields) => [
      ['Action', 'Approve and send candidate emails'],
      ['Email IDs', Array.isArray(fields.candidate_ids) ? fields.candidate_ids.join(', ') : ''],
    ],
  },
  propose_create_premium_contact: {
    endpoint: '/premium-numbers/contacts',
    method: 'POST',
    buildBody: (fields) => record(fields.fields),
    confirmLabel: () => 'Save Contact',
    summary: (fields) => {
      const values = record(fields.fields)
      return [
        ['Name', text(values.name)],
        ['Title', text(values.title)],
        ['Company', text(values.company)],
        ['Email', text(values.email)],
        ['Phone', text(values.phone_display || values.phone)],
        ['Role', text(values.role)],
        ...(fields.duplicate_of_id ? [['Existing contact', `ID ${fields.duplicate_of_id}`] as [string, string]] : []),
      ]
    },
  },
  propose_send_email: {
    endpoint: (fields) => `/candidates/${Number(fields.candidate_email_id)}/send-chat-reply`,
    method: 'POST',
    buildBody: (fields) => ({ body: fields.body, subject: fields.subject }),
    confirmLabel: () => 'Send Email',
    summary: (fields) => [
      ['To', text(fields.to)],
      ['CC', text(fields.cc)],
      ['Subject', text(fields.subject)],
      ['Body', text(fields.body)],
    ],
  },
  propose_create_github_issue: {
    endpoint: '/support/github-issues',
    method: 'POST',
    buildBody: (fields) => ({
      title: fields.title,
      user_report: fields.user_report,
      ai_summary: fields.ai_summary,
      context: fields.context,
    }),
    confirmLabel: () => 'Create GitHub Issue',
    summary: (fields) => [
      ['Title', text(fields.title)],
      ['Your report', text(fields.user_report)],
      ['AI summary', text(fields.ai_summary)],
      ...(text(fields.context) ? [['Context', text(fields.context)] as [string, string]] : []),
    ],
  },
}

export function proposalForMessage(message: ChatMessage): { handler: ProposalHandler; fields: ProposalFields } | null {
  if (message.role !== 'tool' || !message.tool_name) return null
  const handler = PROPOSAL_HANDLERS[message.tool_name]
  if (!handler) return null
  try {
    const fields = JSON.parse(message.content) as ProposalFields
    if (!fields || typeof fields !== 'object' || !fields.action) return null
    return { handler, fields }
  } catch {
    return null
  }
}

export function proposalResultDetail(payload: Record<string, unknown>, fields: ProposalFields = {}): string {
  if (Array.isArray(payload.succeeded_ids)) {
    const failed = Array.isArray(payload.failed) ? payload.failed.length : 0
    // Every bulk route returns this shape, so the verb has to come from the
    // proposal rather than being hardcoded - a reject reporting "3 approved"
    // is a report of the wrong action having happened.
    const verb = text(fields.label).toLowerCase() || 'approved'
    return `${payload.succeeded_ids.length} ${verb}${failed ? `; ${failed} failed` : ''}.`
  }
  if (payload.sent) return 'Email sent.'
  if (typeof payload.issue_number === 'number') return `Issue #${payload.issue_number} created.`
  if (typeof payload.id === 'number') return `Saved as contact ${payload.id}.`
  return 'Action completed.'
}
