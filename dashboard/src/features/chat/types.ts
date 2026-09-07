export type ChatSession = {
  id: number
  title: string | null
  created_at: string
  updated_at: string
}

export type ChatMessage = {
  id: number
  // 'event' is the app's own record of what happened to a proposal card. It is
  // not something anyone said, which is why it is neither user nor assistant.
  role: 'user' | 'assistant' | 'tool' | 'event'
  content: string
  tool_name: string | null
  created_at: string
  // Set only on event rows: which card this outcome belongs to, and what it was.
  proposal_message_id?: number | null
  outcome?: 'confirmed' | 'cancelled' | 'failed' | null
}

export type ChatSessionDetail = ChatSession & {
  messages: ChatMessage[]
}

export type ChatStatus = {
  enabled: boolean
  ollama_running: boolean
  ollama_last_error: string | null
  ollama_last_success_at: string | null
  chat_last_error: string | null
  mcp_status: string
  model: string
  available_models: string[]
}

export type ChatAttachment = {
  id: number
  session_id: number
  message_id: number | null
  file_name: string
  mime_type: string
  byte_size: number
  extraction_error: string | null
  created_at: string
}

// The tool the assistant is running right now, for the progress indicator. Held
// only in memory for the length of one turn - it is never persisted, and never
// replayed into the model's history.
export type ActiveTool = {
  name: string
  startedAt: number
}
