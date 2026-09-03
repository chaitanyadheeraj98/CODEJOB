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

export const PROPOSAL_HANDLERS: Record<string, ProposalHandler> = {
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

export function proposalResultDetail(payload: Record<string, unknown>): string {
  if (Array.isArray(payload.succeeded_ids)) {
    const failed = Array.isArray(payload.failed) ? payload.failed.length : 0
    return `${payload.succeeded_ids.length} approved${failed ? `; ${failed} failed` : ''}.`
  }
  if (payload.sent) return 'Email sent.'
  if (typeof payload.issue_number === 'number') return `Issue #${payload.issue_number} created.`
  if (typeof payload.id === 'number') return `Saved as contact ${payload.id}.`
  return 'Action completed.'
}
