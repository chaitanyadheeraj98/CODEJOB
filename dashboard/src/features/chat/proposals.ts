import type { ChatMessage } from './types'

export type ProposalFields = Record<string, unknown>

export type ProposalMethod = 'POST' | 'PATCH' | 'DELETE'

export type ProposalHandler = {
  endpoint: string | ((fields: ProposalFields) => string)
  // A function so one family with an operation enum can span verbs. Splitting
  // create/pause/edit/delete into four handlers to keep this a literal would
  // be the tool-per-operation mistake one layer down.
  method: ProposalMethod | ((fields: ProposalFields) => ProposalMethod)
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

const RECORD_UPDATE_ENDPOINTS: Record<string, (id: number) => string> = {
  opportunity: (id) => `/recruiter-opportunities/${id}`,
  application: (id) => `/applications/${id}`,
  contact: (id) => `/recruiter-numbers/${id}`,
}

const SCHEDULED_TASK_ENDPOINTS: Record<string, (id: number) => string> = {
  create: () => '/scheduled-tasks',
  pause: (id) => `/scheduled-tasks/${id}`,
  resume: (id) => `/scheduled-tasks/${id}`,
  edit: (id) => `/scheduled-tasks/${id}`,
  delete: (id) => `/scheduled-tasks/${id}`,
}

const SCHEDULED_TASK_METHODS: Record<string, ProposalMethod> = {
  create: 'POST',
  pause: 'PATCH',
  resume: 'PATCH',
  edit: 'PATCH',
  delete: 'DELETE',
}

const SCHEDULED_TASK_LABELS: Record<string, string> = {
  create: 'Create Task',
  pause: 'Pause Task',
  resume: 'Resume Task',
  edit: 'Save Changes',
  delete: 'Delete Task',
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
  propose_record_update: {
    endpoint: (fields) => RECORD_UPDATE_ENDPOINTS[String(fields.record_kind)]?.(Number(fields.record_id)) ?? '',
    method: 'PATCH',
    buildBody: (fields) => record(fields.fields),
    confirmLabel: (fields) => `Update ${text(fields.record_kind) || 'record'}`,
    summary: (fields) => [
      ['Record', text(fields.record_label)],
      // from -> to, with the "from" read from the database by the tool. Only
      // the server can supply that half honestly.
      ...(Array.isArray(fields.changes)
        ? (fields.changes as Array<Record<string, unknown>>).map((change): [string, string] => [
          text(change.field),
          `${text(change.from) || '(empty)'} → ${text(change.to) || '(empty)'}`,
        ])
        : []),
    ],
  },
  propose_add_note: {
    endpoint: (fields) => (
      fields.record_kind === 'application'
        ? `/applications/${Number(fields.record_id)}/events`
        : `/recruiter-opportunities/${Number(fields.record_id)}`
    ),
    method: 'POST',
    buildBody: (fields) => (
      fields.record_kind === 'application'
        ? { event_type: 'note', note: fields.note }
        : { notes: fields.combined_notes }
    ),
    confirmLabel: () => 'Add Note',
    summary: (fields) => [
      ['Record', text(fields.record_label)],
      ['Note', text(fields.note)],
      // An opportunity's notes column is replaced, not appended, so the card
      // shows what is already there and exactly what will be stored.
      ...(fields.replaces === true
        ? [
          ['Existing note', text(fields.existing_notes) || '(none)'] as [string, string],
          ['Will be stored', text(fields.combined_notes)] as [string, string],
        ]
        : [['Appended as', 'A new note event on the application'] as [string, string]]),
      ['Written by', 'The assistant, from your records - check it before saving'],
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
  propose_scheduled_task: {
    // Client-side endpoint table, keyed by the operation the tool chose. The
    // server sends no URL and the client routes to none.
    endpoint: (fields) => (
      SCHEDULED_TASK_ENDPOINTS[String(fields.operation)]?.(Number(fields.task_id ?? 0)) ?? ''
    ),
    method: (fields) => SCHEDULED_TASK_METHODS[String(fields.operation)] ?? 'POST',
    buildBody: (fields) => (
      String(fields.operation) === 'create'
        ? {
          title: fields.title,
          kind: fields.kind,
          when: fields.when_phrase ?? '',
          note: fields.note ?? '',
          subject_type: fields.subject_type ?? '',
          subject_id: fields.subject_id ?? '',
        }
        : { operation: fields.operation }
    ),
    confirmLabel: (fields) => SCHEDULED_TASK_LABELS[String(fields.operation)] ?? 'Confirm',
    summary: (fields) => [
      ['Task', text(fields.title)],
      ['Kind', text(fields.kind)],
      // The system's own reading of the schedule, in the user's zone. The point
      // of the card is to confirm what was understood, not what was typed.
      ['Runs', text(fields.trigger)],
      ...(text(fields.first_run) ? [['First run', text(fields.first_run)] as [string, string]] : []),
      ['May do', text(fields.permitted_actions)],
      ...(text(fields.granularity_note)
        ? [['Timing', text(fields.granularity_note)] as [string, string]]
        : []),
      ['Reversible', fields.reversible === true ? 'Yes' : 'No'],
      ...(text(fields.reversible_detail)
        ? [['Detail', text(fields.reversible_detail)] as [string, string]]
        : []),
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
  // Every PATCH and event POST echoes the row back, so `id` alone cannot tell
  // a saved contact from an updated opportunity. The proposal knows which.
  if (fields.action === 'propose_record_update') return `${text(fields.record_label) || 'Record'} updated.`
  if (fields.action === 'propose_add_note') return 'Note added.'
  if (typeof payload.id === 'number') return `Saved as contact ${payload.id}.`
  return 'Action completed.'
}
