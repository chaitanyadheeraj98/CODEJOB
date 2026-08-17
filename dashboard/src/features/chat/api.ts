import type { ChatSession, ChatSessionDetail, ChatStatus } from './types'

async function responseError(response: Response, fallback: string): Promise<Error> {
  const payload = (await response.json().catch(() => null)) as { detail?: string } | null
  return new Error(payload?.detail ?? fallback)
}

export async function getChatStatus(apiBase: string): Promise<ChatStatus> {
  const response = await fetch(`${apiBase}/chat/status`)
  if (!response.ok) throw await responseError(response, 'Failed to check chat status')
  return (await response.json()) as ChatStatus
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

export async function getChatSession(apiBase: string, sessionId: number): Promise<ChatSessionDetail> {
  const response = await fetch(`${apiBase}/chat/sessions/${sessionId}`)
  if (!response.ok) throw await responseError(response, 'Failed to load chat')
  return (await response.json()) as ChatSessionDetail
}

export async function deleteChatSession(apiBase: string, sessionId: number): Promise<void> {
  const response = await fetch(`${apiBase}/chat/sessions/${sessionId}`, { method: 'DELETE' })
  if (!response.ok) throw await responseError(response, 'Failed to delete chat')
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

export async function sendChatMessage(
  apiBase: string,
  sessionId: number,
  text: string,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  const response = await fetch(`${apiBase}/chat/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!response.ok) throw await responseError(response, 'Failed to send message')
  if (!response.body) throw new Error('Chat response did not include a stream')
  await consumeSseStream(response.body, onEvent)
}
