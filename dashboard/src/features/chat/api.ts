import type { ChatAttachment, ChatSession, ChatSessionDetail, ChatStatus, OllamaCredential } from './types'
import type { ProposalFields, ProposalHandler } from './proposals'

export type TelegramLink = {
  linked: boolean
  chat_masked: string | null
  telegram_username: string
  linked_at: string | null
  alerts_enabled: boolean
  pin_set: boolean
  bot_username: string
  pending_code_expires_at: string | null
}

export type TelegramDeepLink = {
  deep_link: string
  expires_at: string
}

async function responseError(response: Response, fallback: string): Promise<Error> {
  const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null
  const detail = payload?.detail
  if (typeof detail === 'string') return new Error(detail)
  // Not every detail is a sentence. `_enqueue_background_job` answers a busy
  // queue with `{code, job_id, run_key}`, and until a proposal could reach a
  // queued route nothing here had to read one - `new Error(object)` renders as
  // "[object Object]" on the card. Same shape `manual_intake/api.ts` already
  // handles, for the same reason.
  if (detail && typeof detail === 'object' && typeof (detail as { code?: unknown }).code === 'string') {
    const code = (detail as { code: string }).code
    return new Error(code === 'another_job_in_progress' ? 'Another job is already running. Try again once it finishes.' : code)
  }
  return new Error(fallback)
}

export async function getChatStatus(apiBase: string): Promise<ChatStatus> {
  const response = await fetch(`${apiBase}/chat/status`)
  if (!response.ok) throw await responseError(response, 'Failed to check chat status')
  return (await response.json()) as ChatStatus
}

export async function saveOllamaCredential(
  apiBase: string,
  apiKey: string,
  baseUrl: string,
): Promise<OllamaCredential> {
  const response = await fetch(`${apiBase}/chat/credentials/ollama`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey, base_url: baseUrl }),
  })
  if (!response.ok) throw await responseError(response, 'Failed to validate Ollama credentials')
  return (await response.json()) as OllamaCredential
}

export async function getTelegramLink(apiBase: string): Promise<TelegramLink> {
  const response = await fetch(`${apiBase}/telegram/link`)
  if (!response.ok) throw await responseError(response, 'Failed to load Telegram link')
  return (await response.json()) as TelegramLink
}

export async function createTelegramLink(apiBase: string): Promise<TelegramDeepLink> {
  const response = await fetch(`${apiBase}/telegram/link/code`, { method: 'POST' })
  if (!response.ok) throw await responseError(response, 'Failed to create Telegram link')
  return (await response.json()) as TelegramDeepLink
}

export async function unlinkTelegram(apiBase: string): Promise<void> {
  const response = await fetch(`${apiBase}/telegram/link`, { method: 'DELETE' })
  if (!response.ok) throw await responseError(response, 'Failed to unlink Telegram')
}

export async function updateTelegramLink(
  apiBase: string,
  settings: { alerts_enabled?: boolean; action_pin?: string },
): Promise<TelegramLink> {
  const response = await fetch(`${apiBase}/telegram/link/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
  if (!response.ok) throw await responseError(response, 'Failed to update Telegram settings')
  return (await response.json()) as TelegramLink
}

export async function listChatSessions(apiBase: string): Promise<ChatSession[]> {
  const response = await fetch(`${apiBase}/chat/sessions`)
  if (!response.ok) throw await responseError(response, 'Failed to load chat history')
  return (await response.json()) as ChatSession[]
}

export async function createChatSession(apiBase: string): Promise<ChatSession> {
  const response = await fetch(`${apiBase}/chat/sessions`, { method: 'POST' })
  if (!response.ok) throw await responseError(response, 'Failed to start chat')
  return (await response.json()) as ChatSession
}

// `sinceId` asks for only the messages newer than that id. Session metadata
// still comes back in full, so callers that need the whole thread simply omit it.
export async function getChatSession(
  apiBase: string,
  sessionId: number,
  sinceId?: number,
): Promise<ChatSessionDetail> {
  const query = sinceId ? `?since_id=${sinceId}` : ''
  const response = await fetch(`${apiBase}/chat/sessions/${sessionId}${query}`)
  if (!response.ok) throw await responseError(response, 'Failed to load chat')
  return (await response.json()) as ChatSessionDetail
}

export async function deleteChatSession(apiBase: string, sessionId: number): Promise<void> {
  const response = await fetch(`${apiBase}/chat/sessions/${sessionId}`, { method: 'DELETE' })
  if (!response.ok) throw await responseError(response, 'Failed to delete chat')
}

export async function renameChatSession(apiBase: string, sessionId: number, title: string): Promise<ChatSession> {
  const response = await fetch(`${apiBase}/chat/sessions/${sessionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  if (!response.ok) throw await responseError(response, 'Failed to rename chat')
  return (await response.json()) as ChatSession
}

export type ChatStreamEvent = {
  event: string
  data: Record<string, unknown>
}

function nextBoundary(buffer: string): { index: number; length: number } | null {
  const lf = buffer.indexOf('\n\n')
  const crlf = buffer.indexOf('\r\n\r\n')
  if (lf < 0 && crlf < 0) return null
  if (crlf >= 0 && (lf < 0 || crlf < lf)) return { index: crlf, length: 4 }
  return { index: lf, length: 2 }
}

function parseEvent(block: string): ChatStreamEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (!data.length) return null
  return { event, data: JSON.parse(data.join('\n')) as Record<string, unknown> }
}

export async function consumeSseStream(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    let boundary = nextBoundary(buffer)
    while (boundary) {
      const parsed = parseEvent(buffer.slice(0, boundary.index))
      if (parsed) onEvent(parsed)
      buffer = buffer.slice(boundary.index + boundary.length)
      boundary = nextBoundary(buffer)
    }
    if (done) break
  }
  const parsed = parseEvent(buffer)
  if (parsed) onEvent(parsed)
}

export async function uploadChatAttachment(
  apiBase: string,
  sessionId: number,
  file: File,
): Promise<ChatAttachment> {
  const body = new FormData()
  body.append('file', file)
  const response = await fetch(`${apiBase}/chat/sessions/${sessionId}/attachments`, { method: 'POST', body })
  if (!response.ok) throw await responseError(response, `Could not attach ${file.name}`)
  return (await response.json()) as ChatAttachment
}

export async function listChatAttachments(apiBase: string, sessionId: number): Promise<ChatAttachment[]> {
  const response = await fetch(`${apiBase}/chat/sessions/${sessionId}/attachments`)
  if (!response.ok) throw await responseError(response, 'Failed to load attachments')
  return (await response.json()) as ChatAttachment[]
}

export async function deleteChatAttachment(apiBase: string, attachmentId: number): Promise<void> {
  const response = await fetch(`${apiBase}/chat/attachments/${attachmentId}`, { method: 'DELETE' })
  if (!response.ok) throw await responseError(response, 'Failed to remove attachment')
}

export async function sendChatMessage(
  apiBase: string,
  sessionId: number,
  text: string,
  onEvent: (event: ChatStreamEvent) => void,
  model?: string,
  attachmentIds: number[] = [],
): Promise<void> {
  const response = await fetch(`${apiBase}/chat/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, model: model || undefined, attachment_ids: attachmentIds }),
  })
  if (!response.ok) throw await responseError(response, 'Failed to send message')
  if (!response.body) throw new Error('Chat response did not include a stream')
  let completed = false
  await consumeSseStream(response.body, (event) => {
    if (event.event === 'done') completed = true
    onEvent(event)
  })
  if (!completed) throw new Error('The connection ended before the reply finished. Try again.')
}

// Cancels the running turn server-side. The stream itself is left to end on its
// own: the server closes it after writing the partial transcript, so racing it
// from here would be the one thing that could lose that write.
export async function stopChatTurn(
  apiBase: string,
  sessionId: number,
  turnId: string,
): Promise<void> {
  const response = await fetch(`${apiBase}/chat/sessions/${sessionId}/turns/${turnId}`, {
    method: 'DELETE',
  })
  // 404 means the turn already finished between the click and the request. That
  // is the same outcome the user asked for, so it is not an error to report.
  if (!response.ok && response.status !== 404) {
    throw await responseError(response, 'Failed to stop the assistant')
  }
}

// Enumerated values and a number, never prose. The server composes the sentence
// this becomes, because the row is replayed into the model's history and a
// client-supplied string there would be a write into trusted framing.
export async function recordProposalOutcome(
  apiBase: string,
  sessionId: number,
  outcome: {
    tool_name: string
    outcome: 'confirmed' | 'cancelled' | 'failed'
    proposal_message_id: number
    characters?: number
  },
): Promise<void> {
  const response = await fetch(`${apiBase}/chat/sessions/${sessionId}/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(outcome),
  })
  if (!response.ok) throw await responseError(response, 'Failed to record the outcome')
}

export async function runProposalAction(
  apiBase: string,
  handler: ProposalHandler,
  fields: ProposalFields,
): Promise<Record<string, unknown>> {
  const endpoint = typeof handler.endpoint === 'function' ? handler.endpoint(fields) : handler.endpoint
  // A function endpoint resolves to '' when the payload names something the
  // client does not know. Without this the request would go to the API root.
  if (!endpoint) throw new Error('This action is not one the app can perform.')
  const method = typeof handler.method === 'function' ? handler.method(fields) : handler.method
  const response = await fetch(`${apiBase}${endpoint}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(handler.buildBody(fields)),
  })
  if (!response.ok) throw await responseError(response, 'Action failed')
  return (await response.json()) as Record<string, unknown>
}

// The only v3 write path, and it is reached by the user's click on a rendered
// control - never by a model-issued call. v3 registers no propose_* tool.
export async function recordRelationshipJudgment(
  apiBase: string,
  clusterId: string,
  verdict: 'confirmed' | 'rejected',
): Promise<Record<string, unknown>> {
  const response = await fetch(`${apiBase}/relationships/clusters/${encodeURIComponent(clusterId)}/judgment`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ verdict }),
  })
  if (!response.ok) throw await responseError(response, 'That did not save')
  return (await response.json()) as Record<string, unknown>
}
