import { requestJson } from '../premium_numbers/api'

export type LabelOverviewItem = {
  external_label_id: string
  name: string
  color_background: string | null
  color_text: string | null
  thread_count: number
  unread_count: number
  last_message_at: string | null
}

export type LabelOverview = {
  items: LabelOverviewItem[]
  tracked_thread_total: number
  untracked_label_count: number
}

/** How a person on a message relates to the submission. */
export type ContactKind = 'recruiter' | 'employer' | 'self' | 'other'

export type DossierContact = {
  address: string
  name: string
  domain: string
  kind: ContactKind
  message_count: number
  watched: boolean
}

export type DossierMessage = {
  id: number
  conversation_id: number
  external_thread_id: string
  direction: string
  sender: string
  sender_address: string
  to_header: string | null
  cc_header: string | null
  subject: string
  snippet: string
  body: string
  occurred_at: string
  read_at: string | null
  /** 'watch' means it arrived outside the labeled thread and was pulled in. */
  origin: string
  gmail_link: string | null
}

export type ThreadDossier = {
  thread_id: string
  subject: string
  labels: string[]
  last_message_at: string
  conversation_id: number | null
  thread_count: number
  unread_count: number
  gmail_thread_link: string | null
  appts_application_id: number | null
  record_id: string | null
  watches: string[]
  contacts: DossierContact[]
  messages: DossierMessage[]
}

export function fetchLabelOverview(apiBase: string) {
  return requestJson<LabelOverview>(`${apiBase}/labels/overview`)
}

export function fetchThreadDossier(apiBase: string, threadId: string) {
  return requestJson<ThreadDossier>(`${apiBase}/labels/threads/${encodeURIComponent(threadId)}/dossier`)
}

export function markThreadRead(apiBase: string, threadId: string) {
  return requestJson<ThreadDossier>(`${apiBase}/labels/threads/${encodeURIComponent(threadId)}/read`, { method: 'POST' })
}
