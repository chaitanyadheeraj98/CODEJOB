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
  // Shown on the card while it is still unresolved, in the app's own words. It
  // exists because the model's prose beside the card can claim the write already
  // happened, and the card is the one thing on screen that knows it has not.
  // Optional: every proposal tool has the same latent problem with its own verb,
  // and this is the seam to fix them through when someone wants to.
  pendingNotice?: string
}

function record(value: unknown): ProposalFields {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as ProposalFields : {}
}

function text(value: unknown): string {
  return value == null ? '' : String(value)
}

// Strict about the element type on purpose: Number(null) and Number('') are both
// 0, so coercing first would turn a malformed payload into a request for
// document id 0 rather than dropping the entry.
function numbers(value: unknown): number[] {
  return Array.isArray(value) ? value.filter((item): item is number => typeof item === 'number' && Number.isFinite(item)) : []
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(text).filter(Boolean) : []
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

const PROFILE_ENDPOINTS: Record<string, string> = {
  append: '/settings/candidate-profile/entries',
  replace: '/settings/candidate-profile/from-attachment',
  delete: '/settings/candidate-profile',
}

const PROFILE_METHODS: Record<string, ProposalMethod> = {
  append: 'POST',
  replace: 'POST',
  delete: 'DELETE',
}

const PROFILE_LABELS: Record<string, string> = {
  append: 'Save to Profile',
  replace: 'Replace Profile',
  delete: 'Delete Profile',
}

// Which of the two ways in produced this card. The user should be able to tell
// from the card whether they asked for this or the assistant offered it.
const PROFILE_PROVENANCE: Record<string, string> = {
  assistant_asked: 'Your own words, from your answer',
  user_directed: 'Your own words, from what you asked me to save',
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
  propose_manual_requirement: {
    endpoint: '/manual-requirements/from-chat',
    method: 'POST',
    buildBody: (fields) => ({
      attachment_id: fields.attachment_id ?? null,
      message_id: fields.message_id ?? null,
      acknowledged_duplicate_of: record(fields.duplicate_of).id ?? null,
    }),
    confirmLabel: () => 'Add to Needs Review',
    pendingNotice: 'Nothing has been created yet. This is queued for review only when you click Confirm.',
    summary: (fields) => {
      const duplicate = record(fields.duplicate_of)
      return [
        ['Source', text(fields.source_label)],
        ['Characters', Number(fields.characters ?? 0).toLocaleString()],
        ...(duplicate.id
          ? [['Possible duplicate', `#${Number(duplicate.id)} - ${text(duplicate.role) || 'role unknown'} at ${text(duplicate.client) || 'client unknown'}, pasted ${text(duplicate.created_at)}. It will still be created.`] as [string, string]]
          : []),
        ['Complete requirement that will be ingested', text(fields.jd_text)],
      ]
    },
  },
  // The tool for this shipped without a handler here, and this registry is what
  // `visibleMessages` consults: a tool row in neither it nor RENDER_HANDLERS is
  // filtered out of the session before anything renders. So the payload arrived,
  // the row was dropped, and the assistant described a search "ready to confirm"
  // beside an empty space - with no refusal notice either, since that renders
  // from a row that no longer existed.
  //
  // Same failure as the profile card, one layer further out: there the tool
  // refused and said so, here the tool was fine and the client discarded it.
  propose_nvoids_search: {
    endpoint: '/jobs/nvoids-client-search',
    method: 'POST',
    // The criteria only. `generated_query` is on the payload and is shown on the
    // card, but the server composes the query it actually runs - sending the
    // string back would make a display field into the instruction.
    buildBody: (fields) => {
      const criteria = record(fields.criteria)
      return {
        end_client: text(criteria.end_client) || text(fields.company),
        job_role: text(criteria.job_role),
        search_location: text(criteria.search_location),
        query_mode: text(criteria.query_mode) || 'composed',
        batch_limit: Number(criteria.batch_limit ?? 10),
      }
    },
    confirmLabel: (fields) => `Search Nvoids for ${text(fields.company) || 'this company'}`,
    summary: (fields) => {
      const criteria = record(fields.criteria)
      const composed = text(criteria.query_mode) !== 'end_client_only'
      return [
        ['End client', text(criteria.end_client) || text(fields.company)],
        // Role and location are dropped in end_client_only mode, so listing them
        // regardless would show two criteria that will not be applied.
        ...(composed && text(criteria.job_role) ? [['Role', text(criteria.job_role)] as [string, string]] : []),
        ...(composed && text(criteria.search_location) ? [['Location', text(criteria.search_location)] as [string, string]] : []),
        ['Mode', composed ? 'Composed - role and location applied' : 'End client only'],
        ['Batch limit', String(Number(criteria.batch_limit ?? 10))],
        // What will actually be sent to nvoids. Display-only; see buildBody.
        ['Query', text(criteria.generated_query)],
        ['Already stored', `${Number(fields.already_stored ?? 0)} record(s) for this company`],
      ]
    },
    pendingNotice: 'Nothing has been searched yet. Nvoids is contacted only when you click Confirm.',
  },
  propose_taxonomy_bulk_review: {
    endpoint: '/settings/taxonomy/bulk-review/apply',
    method: 'POST',
    // expected_count is derived from the keys on the card, not from the tool's own
    // count field. If those two ever disagree the server answers 409 and writes
    // nothing, so the number the user reads is the number that gets applied.
    buildBody: (fields) => {
      const keys = strings(fields.keys)
      return {
        scope: text(fields.scope),
        action: text(fields.taxonomy_action),
        keys,
        expected_count: keys.length,
      }
    },
    confirmLabel: (fields) =>
      `${text(fields.label) || 'Apply'} ${strings(fields.keys).length} ${text(fields.scope)} value(s)`,
    summary: (fields) => {
      const keys = strings(fields.keys)
      const remaining = Number(fields.remaining_after_batch ?? 0)
      const needsHuman = Number(fields.needs_human_count ?? 0)
      return [
        ['Action', `${text(fields.label)} pending ${text(fields.scope)} values`],
        ['Values', String(keys.length)],
        // A sample, not the whole list. The full set is on the card's keys, which
        // is what the request sends - this line is for recognising the batch.
        ['For example', strings(fields.sample_names).join(', ')],
        ['Reversible', fields.reversible === true ? 'Yes' : 'No'],
        ...(text(fields.reversible_detail) ? [['Detail', text(fields.reversible_detail)] as [string, string]] : []),
        ...(remaining > 0
          ? [['Not in this batch', `${remaining} more - ask again to continue`] as [string, string]]
          : []),
        ...(needsHuman > 0
          ? [['Left for you', `${needsHuman} undecided - use Bulk review in Settings`] as [string, string]]
          : []),
        ...(text(fields.model_error) ? [['Model', text(fields.model_error)] as [string, string]] : []),
      ]
    },
    pendingNotice: 'Nothing has been approved or dismissed yet. The taxonomy changes only when you click Confirm.',
  },
  propose_send_email: {
    endpoint: (fields) => `/candidates/${Number(fields.candidate_email_id)}/send-chat-reply`,
    method: 'POST',
    buildBody: (fields) => ({
      body: fields.body,
      subject: fields.subject,
      // Ids, never the names the card displays. The server resolves them again
      // at send time, so a document deleted between proposal and click fails
      // the send rather than silently matching a different file.
      document_ids: numbers(fields.document_ids),
    }),
    confirmLabel: () => 'Send Email',
    summary: (fields) => {
      const attached = strings(fields.document_names)
      return [
        ['To', text(fields.to)],
        ['CC', text(fields.cc)],
        ['Subject', text(fields.subject)],
        // Above the body, which can run long: the files leaving with the mail
        // are the part of this card that is worth reading twice.
        ...(attached.length ? [['Attachments', attached.join(', ')] as [string, string]] : []),
        ['Body', text(fields.body)],
      ]
    },
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
  propose_profile_update: {
    // One tool, three verbs, one handler - and a client-side endpoint table, so
    // an unknown operation resolves to '' and runProposalAction refuses it
    // rather than aiming a request at the API root.
    endpoint: (fields) => PROFILE_ENDPOINTS[String(fields.operation)] ?? '',
    method: (fields) => PROFILE_METHODS[String(fields.operation)] ?? 'POST',
    // base_sha256 travels with every verb: the write is refused with a 409 if
    // the profile changed between this card being built and the click.
    buildBody: (fields) => {
      const operation = String(fields.operation)
      if (operation === 'append') return { entry: fields.entry, base_sha256: fields.base_sha256 }
      if (operation === 'replace') return { attachment_id: fields.attachment_id, base_sha256: fields.base_sha256 }
      return { base_sha256: fields.base_sha256 }
    },
    confirmLabel: (fields) => (
      String(fields.operation) === 'append' && strings(fields.replaces).length
        ? 'Update Profile'
        : PROFILE_LABELS[String(fields.operation)] ?? 'Confirm'
    ),
    pendingNotice: 'Nothing has been saved yet. Your Candidate Profile changes when you click below.',
    summary: (fields) => {
      const operation = String(fields.operation)
      if (operation === 'delete') {
        return [
          ['Characters', Number(fields.characters_before ?? 0).toLocaleString()],
          // The complete document, because what is being destroyed is the thing
          // that needs checking.
          ['Complete profile that will be deleted', text(fields.existing_profile)],
        ]
      }
      if (operation === 'replace') {
        return [
          ['From file', text(fields.source_file_name)],
          ['Characters', `${Number(fields.characters_before ?? 0).toLocaleString()} → ${Number(fields.characters_after ?? 0).toLocaleString()}`],
          ['Complete profile after replacing', text(fields.resulting_profile)],
        ]
      }
      const replaces = strings(fields.replaces)
      const conflicts = strings(fields.conflicts)
      return [
        ['Field', text(fields.field)],
        ['Value', text(fields.value)],
        // The old value, when there is one. A correction that showed only what
        // it was adding would hide the half the user most needs to check.
        ...(replaces.length ? [['Replacing', replaces.join('\n')] as [string, string]] : []),
        [replaces.length ? 'Entry after this change' : 'Entry added', text(fields.entry)],
        // Cannot be rewritten from here - it is the user's own uploaded text -
        // so the profile is about to state two things and only they can settle it.
        ...(conflicts.length
          ? [['Your uploaded text also says', `${conflicts.join('\n')}\n\nThis is left as it is; only the "Saved from chat" section changes.`] as [string, string]]
          : []),
        // Not a diff and not a summary: this becomes text the assistant treats
        // as authoritative on every future turn, so the card shows all of it.
        ['Complete profile after saving', text(fields.resulting_profile)],
        ['Saved as', PROFILE_PROVENANCE[String(fields.provenance)] ?? 'Your own words'],
      ]
    },
  },
  propose_resume_draft: {
    endpoint: '/resume-editor/drafts',
    method: 'POST',
    // No content_markdown on purpose. Left out, the server copies the variant's
    // own text into the draft, so what opens in the Editor is the resume the
    // user actually has rather than a model's recollection of it.
    buildBody: (fields) => fields.from_scratch === true
      ? { name: fields.name, content_markdown: fields.initial_content }
      : { name: fields.name, source_resume_id: fields.source_resume_id },
    confirmLabel: () => 'Create Draft',
    summary: (fields) => [
      ['Draft name', text(fields.name)],
      ...(fields.from_scratch === true ? [['Initial draft', text(fields.initial_content)] as [string, string]] : []),
      ['Copied from', [text(fields.source_variant_code), text(fields.source_file_name)].filter(Boolean).join(' · ')],
      // The size of the copy, not the text. A resume is too long to re-read on a
      // card, and the character count is the part that says which one it is.
      ['Text', `${Number(fields.source_characters ?? 0).toLocaleString()} characters`],
    ],
    pendingNotice: 'No draft exists until you click. Your stored resumes are never changed.',
  },
  propose_resume_section: {
    endpoint: (fields) => `/resume-editor/drafts/${Number(fields.draft_id)}/section`,
    method: 'PATCH',
    // base_sha256 travels with the write: the server refuses with a 409 if the
    // section changed between this card being built and the click, so a rewrite
    // never silently discards an edit made in the Editor meanwhile.
    buildBody: (fields) => ({
      section: fields.section,
      replacement: fields.replacement,
      base_sha256: fields.base_sha256,
    }),
    confirmLabel: () => 'Apply Rewrite',
    summary: (fields) => [
      ['Draft', text(fields.draft_name)],
      ['Section', text(fields.section)],
      // Old above new. Both render as scrolling documents, which is the whole
      // reason this is one section and not the entire resume.
      ['Now', text(fields.current)],
      ['Becomes', text(fields.replacement)],
    ],
    pendingNotice: 'Nothing is rewritten until you click. Only this section changes.',
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

export function resumeSequenceProgress(messages: ChatMessage[], fields: ProposalFields): string | undefined {
  if (fields.action !== 'propose_resume_section') return undefined
  const planned = strings(fields.sequence_sections)
  if (!planned.length) return undefined
  const proposed = new Set<string>()
  for (const message of messages) {
    const proposal = proposalForMessage(message)
    if (proposal?.fields.action !== 'propose_resume_section' || proposal.fields.draft_id !== fields.draft_id) continue
    if (JSON.stringify(proposal.fields.sequence_sections) !== JSON.stringify(planned)) continue
    const section = text(proposal.fields.section)
    if (section === planned[0]) proposed.clear()
    if (planned.includes(section)) proposed.add(section)
    if (JSON.stringify(proposal.fields) === JSON.stringify(fields)) break
  }
  return `${proposed.size} of ${planned.length} sections proposed. Each needs its own approval.`
}

// Why a propose_* tool produced no card: it refused, or it wants more first.
// Read off the payload rather than mapped per tool, because every proposal tool
// answers in the same four shapes.
function refusalReason(payload: ProposalFields): string {
  const missing = strings(payload.missing)
  if (missing.length) return `More information is needed first: ${missing.join(', ')}.`
  return text(payload.detail) || text(payload.reason) || text(payload.error) || ''
}

// A propose_* tool row that carries no proposal. It renders as nothing today,
// which leaves the model's prose beside it as the only account of the turn -
// and that is exactly where it claims a card exists that does not. Observed:
// propose_profile_update returned no_save_request and the reply was "I've
// prepared a proposal ... click the confirmation card to save it."
//
// Same principle as pendingNotice, one step earlier: where the app knows what
// happened, the app says so. Deliberately not scoped to one tool - the same
// turn's propose_create_premium_contact returned missing_fields and drew the
// same false claim.
export function proposalRefusalForMessage(message: ChatMessage): string | null {
  if (message.role !== 'tool' || !message.tool_name) return null
  if (!PROPOSAL_HANDLERS[message.tool_name]) return null
  let payload: unknown
  try {
    payload = JSON.parse(message.content)
  } catch {
    return null
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null
  const fields = payload as ProposalFields
  // A real proposal renders as a card; this is only for the rows that do not.
  if (fields.action) return null
  return refusalReason(fields) || 'The assistant did not say why.'
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
  // CandidateProfileResponse has no id and no `sent`, so without this every
  // profile write would report the generic "Action completed."
  if (fields.action === 'propose_profile_update') {
    if (String(fields.operation) === 'delete') return 'Profile deleted.'
    return `Profile updated - ${Number(payload.characters ?? 0).toLocaleString()} characters.`
  }
  if (fields.action === 'propose_manual_requirement') {
    return 'Queued. It becomes a Needs Review card once ingestion finishes - ask me to check it.'
  }
  if (fields.action === 'propose_resume_section') {
    return `"${text(fields.section)}" rewritten in ${text(fields.draft_name)} - the draft is now ${Number(payload.character_count ?? 0).toLocaleString()} characters.`
  }
  // Above the generic `id` branch below: a draft response carries an id too, and
  // falling through would report a new resume draft as a saved contact.
  if (fields.action === 'propose_resume_draft') {
    return `Draft "${text(payload.name)}" created - open the Editor tab in Resume Tracking to write it.`
  }
  if (typeof payload.id === 'number') return `Saved as contact ${payload.id}.`
  // A queued job, not a finished one. Every other branch here reports something
  // that has already happened, so the generic "Action completed." would be the
  // one sentence on screen claiming results exist - and for a crawl that has
  // just been enqueued, none do yet.
  if (typeof payload.run_key === 'string') return 'Queued. It runs in the background - ask me for the result.'
  return 'Action completed.'
}

// The prefix every proposal tool's name carries. It is the only thing the
// client can use to recognise a proposal from a version of the backend it does
// not know about - by definition there is no handler to look the row up in.
const PROPOSAL_TOOL_PREFIX = 'propose_'

export function isProposalToolName(toolName: string): boolean {
  return toolName.startsWith(PROPOSAL_TOOL_PREFIX)
}

// A propose_* row this build has no handler for. Before, the row was filtered
// out of the session by visibleMessages and vanished, which is how
// propose_nvoids_search shipped and stayed broken: the assistant announced a
// search "ready to confirm" and there was nothing on screen to disagree with
// it. `test_proposal_card_coverage.py` now fails CI when a tool ships without a
// handler, and this is what the user sees if one ever reaches them anyway -
// a deployment skew, or a flag enabling a tool the built dashboard predates.
//
// It names the tool because the only person who can act on this is whoever
// reads the message, and "propose_nvoids_search" is the searchable half.
export function unsupportedProposalNotice(message: ChatMessage): string | null {
  if (message.role !== 'tool' || !message.tool_name) return null
  if (!isProposalToolName(message.tool_name)) return null
  if (PROPOSAL_HANDLERS[message.tool_name]) return null
  return (
    `The assistant proposed an action this version of the app cannot show a confirmation `
    + `card for (${message.tool_name}), so nothing has happened and nothing was sent.`
  )
}
