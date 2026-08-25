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
