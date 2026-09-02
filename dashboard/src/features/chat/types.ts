export type ChatSession = {
  id: number
  title: string | null
  created_at: string
  updated_at: string
}

export type ChatMessage = {
  id: number
  role: 'user' | 'assistant' | 'tool'
  content: string
  tool_name: string | null
  created_at: string
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
