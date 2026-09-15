import type { ApplicationCard, ApplicationStatus } from '../premium_numbers/types'

export const APPLICATION_STATUSES: ApplicationStatus[] = [
  'matched', 'contacted', 'recruiter_responded', 'resume_shared', 'rtr_requested', 'rtr_confirmed',
  'submitted_to_client', 'client_reviewing', 'interview_1', 'interview_2', 'final_interview', 'offer',
  'hired', 'rejected', 'withdrawn', 'no_response', 'position_closed', 'duplicate',
]

export const CLOSED_APPLICATION_STATUSES: ApplicationStatus[] = [
  'hired', 'rejected', 'withdrawn', 'no_response', 'position_closed', 'duplicate',
]

export type JourneyNode = {
  id: string
  kind: 'status' | 'rtr' | 'interview' | 'note' | 'terminal'
  title: string
  at: string | null
  state: 'done' | 'waiting' | 'failed' | 'corrected'
  detail: Record<string, string>
  parentId: string | null
}

export type JourneyAction = {
  kind:
    | 'add_note'
    | 'add_call_note'
    | 'link_email'
    | 'recruiter_responded'
    | 'mark_no_response'
    | 'request_rtr'
    | 'confirm_rtr'
    | 'expire_rtr'
    | 'revoke_rtr'
    | 'follow_up'
    | 'submit_to_client'
    | 'add_interview'
    | 'record_result'
    | 'mark_hired'
    | 'close'
  label: string
}

const label = (value: string) => value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())

function metadata(value: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value) as unknown
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {}
  } catch {
    return {}
  }
}

function statusFrom(value: unknown): ApplicationStatus | null {
  return typeof value === 'string' && APPLICATION_STATUSES.includes(value as ApplicationStatus) ? value as ApplicationStatus : null
}

function nodeTime(value: string | null): number {
  if (!value) return Number.POSITIVE_INFINITY
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : parsed
}

function milestoneFor(status: ApplicationStatus): string | null {
  if (status === 'offer') return 'offered'
  if (['interview_1', 'interview_2', 'final_interview'].includes(status)) return 'interview_scheduled'
  return ['hired', 'rejected', 'withdrawn'].includes(status) ? status : null
}

function statusDetail(application: ApplicationCard, status: ApplicationStatus): Record<string, string> {
  const detail: Record<string, string> = { status }
  const milestone = milestoneFor(status)
  if (milestone && application.milestones_reached[milestone]) detail.first_reached = application.milestones_reached[milestone]
  if (status === application.status && CLOSED_APPLICATION_STATUSES.includes(status)) {
    if (application.closed_reason_code) detail.closed_reason_code = application.closed_reason_code
    if (application.closed_reason) detail.closed_reason = application.closed_reason
    if (application.rejection_detail_tags.length) detail.rejection_tags = application.rejection_detail_tags.map((tag) => tag.value).join(', ')
  }
  return detail
}

export function toJourney(application: ApplicationCard): JourneyNode[] {
  const statusNodes: JourneyNode[] = []
  const seenStatusIds = new Set<string>()
  const otherNodes: JourneyNode[] = []

  for (const event of application.events) {
    if (event.event_type !== 'status_changed') continue
    const parsed = metadata(event.metadata_json)
    const to = statusFrom(parsed.to)
    if (!to) {
      otherNodes.push({
        id: `event:${event.id}`,
        kind: 'note',
        title: label(event.event_type),
        at: event.occurred_at,
        state: 'done',
        detail: { note: event.note, source: event.event_source },
        parentId: null,
      })
      continue
    }
    const from = statusFrom(parsed.from)
    const baseId = `status:${to}`
    const id = seenStatusIds.has(baseId) ? `${baseId}:${event.id}` : baseId
    seenStatusIds.add(baseId)
    const movedBackwards = Boolean(
      from
      && APPLICATION_STATUSES.indexOf(to) < APPLICATION_STATUSES.indexOf(from),
    )
    statusNodes.push({
      id,
      kind: CLOSED_APPLICATION_STATUSES.includes(to) ? 'terminal' : 'status',
      title: label(to),
      at: event.occurred_at,
      state: movedBackwards ? 'corrected' : CLOSED_APPLICATION_STATUSES.includes(to) ? 'failed' : 'done',
      detail: {
        ...statusDetail(application, to),
        ...(from ? { from } : {}),
        ...(typeof parsed.trigger === 'string' ? { trigger: parsed.trigger } : movedBackwards ? { trigger: 'user_correction' } : {}),
      },
      parentId: null,
    })
  }

  if (!statusNodes.some((node) => node.detail.status === application.status)) {
    statusNodes.push({
      id: `status:${application.status}`,
      kind: CLOSED_APPLICATION_STATUSES.includes(application.status) ? 'terminal' : 'status',
      title: label(application.status),
      at: application.status_changed_at || application.created_at,
      state: CLOSED_APPLICATION_STATUSES.includes(application.status) ? 'failed' : 'done',
      detail: statusDetail(application, application.status),
      parentId: null,
    })
  }

  const orderedStatuses = [...statusNodes].sort((left, right) => nodeTime(left.at) - nodeTime(right.at) || left.id.localeCompare(right.id))
  const parentAt = (at: string) => [...orderedStatuses].reverse().find((node) => nodeTime(node.at) <= nodeTime(at))?.id ?? null

  for (const event of application.events) {
    if (event.event_type === 'status_changed') continue
    const parsed = metadata(event.metadata_json)
    otherNodes.push({
      id: `event:${event.id}`,
      kind: 'note',
      title: event.event_type === 'created' ? 'Application' : label(event.event_type),
      at: event.occurred_at,
      state: 'done',
      detail: {
        ...(event.note ? { note: event.note } : {}),
        source: event.event_source,
        ...(event.linked_recruiter_email_id ? { linked_recruiter_email_id: String(event.linked_recruiter_email_id) } : {}),
        ...(typeof parsed.trigger === 'string' ? { trigger: parsed.trigger } : {}),
      },
      parentId: parentAt(event.occurred_at),
    })
  }

  for (const rtr of application.rtr_history) {
    otherNodes.push({
      id: `rtr:${rtr.id}`,
      kind: 'rtr',
      title: `RTR ${label(rtr.status)}`,
      at: rtr.requested_at,
      state: rtr.status === 'requested' ? 'waiting' : ['expired', 'revoked'].includes(rtr.status) ? 'failed' : 'done',
      detail: {
        status: rtr.status,
        role_scope: rtr.role_scope,
        end_client_scope: rtr.end_client_scope,
        ...(rtr.expires_at ? { expires_at: rtr.expires_at } : {}),
        ...(rtr.confirmed_at ? { confirmed_at: rtr.confirmed_at } : {}),
        ...(rtr.note ? { note: rtr.note } : {}),
      },
      parentId: null,
    })
  }

  for (const interview of application.interviews) {
    otherNodes.push({
      id: `interview:${interview.id}`,
      kind: 'interview',
      title: label(interview.round_type),
      at: interview.scheduled_at,
      state: interview.result === 'rescheduled' || interview.result === 'scheduled' ? 'waiting' : ['failed', 'cancelled'].includes(interview.result) ? 'failed' : 'done',
      detail: {
        result: interview.result,
        format: interview.format,
        interviewers: interview.interviewer_names,
        ...(interview.feedback ? { feedback: interview.feedback } : {}),
        ...(interview.follow_up_task_note ? { follow_up: interview.follow_up_task_note } : {}),
      },
      parentId: null,
    })
  }

  return [...statusNodes, ...otherNodes].sort((left, right) => nodeTime(left.at) - nodeTime(right.at) || left.id.localeCompare(right.id))
}

const action = (kind: JourneyAction['kind'], title: string): JourneyAction => ({ kind, label: title })
const note = action('add_note', 'Add Note')
const call = action('add_call_note', 'Add Call Note')
const email = action('link_email', 'Link Email')
const close = action('close', 'Close')

export function legalActions(application: ApplicationCard): JourneyAction[] {
  switch (application.status) {
    case 'matched':
    case 'contacted':
      return [note, call, email, action('recruiter_responded', 'Recruiter Responded'), action('mark_no_response', 'Mark No Response')]
    case 'recruiter_responded':
      return [action('request_rtr', 'Request RTR'), note, call, email, action('follow_up', 'Follow Up'), close]
    case 'resume_shared':
      return [action('request_rtr', 'Request RTR'), action('recruiter_responded', 'Recruiter Responded'), note, call, email, close]
    case 'rtr_requested':
      return [action('confirm_rtr', 'Confirm RTR'), action('expire_rtr', 'Mark RTR Expired'), action('revoke_rtr', 'Mark RTR Revoked'), note, close]
    case 'rtr_confirmed':
      return [action('submit_to_client', 'Submit to Client'), note, call, email, close]
    case 'submitted_to_client':
    case 'client_reviewing':
      return [action('add_interview', 'Add Interview'), note, close]
    case 'interview_1':
    case 'interview_2':
    case 'final_interview':
      return [action('record_result', 'Record Result'), action('add_interview', 'Add Interview'), note, close]
    case 'offer':
      return [action('mark_hired', 'Mark Hired'), note, close]
    default:
      return [note]
  }
}
