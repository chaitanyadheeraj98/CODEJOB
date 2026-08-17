import { useCallback, useEffect, useState } from 'react'

import {
  createChatSession,
  deleteChatSession,
  getChatSession,
  listChatSessions,
  sendChatMessage,
} from './api'
import type { ChatMessage, ChatSession } from './types'


export function useChatSession(apiBase: string, enabled: boolean) {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!enabled) return
    let active = true
    listChatSessions(apiBase).then((rows) => {
      if (!active) return
      setSessions(rows)
      if (rows[0]) {
        getChatSession(apiBase, rows[0].id).then((detail) => {
          if (!active) return
          setSessionId(detail.id)
          setMessages(detail.messages.filter((message) => message.role !== 'tool' && message.content))
        }, (reason: unknown) => {
          if (active) setError(reason instanceof Error ? reason.message : 'Failed to load chat')
        })
      }
    }, (reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : 'Failed to load chat history')
    })
    return () => {
      active = false
    }
  }, [apiBase, enabled])

  const startSession = useCallback(async () => {
    const session = await createChatSession(apiBase)
    setSessions((current) => [session, ...current])
    setSessionId(session.id)
    setMessages([])
    setError('')
    return session
  }, [apiBase])

  const selectSession = useCallback(async (nextId: number) => {
    setBusy(true)
    setError('')
    try {
      const detail = await getChatSession(apiBase, nextId)
      setSessionId(detail.id)
      setMessages(detail.messages.filter((message) => message.role !== 'tool' && message.content))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Failed to load chat')
    } finally {
      setBusy(false)
    }
  }, [apiBase])

  const removeCurrentSession = useCallback(async () => {
    if (sessionId == null) return
    await deleteChatSession(apiBase, sessionId)
    const rows = await listChatSessions(apiBase)
    setSessions(rows)
    if (rows[0]) {
      const detail = await getChatSession(apiBase, rows[0].id)
      setSessionId(detail.id)
      setMessages(detail.messages.filter((message) => message.role !== 'tool' && message.content))
    } else {
      setSessionId(null)
      setMessages([])
    }
  }, [apiBase, sessionId])

  const sendMessage = useCallback(async (rawText: string) => {
    const text = rawText.trim()
    if (!text || busy) return
    setBusy(true)
    setError('')
    try {
      const activeSessionId = sessionId ?? (await startSession()).id
      const now = new Date().toISOString()
      const userId = -Date.now()
      const assistantId = userId - 1
      setMessages((current) => [
        ...current,
        { id: userId, role: 'user', content: text, tool_name: null, created_at: now },
        { id: assistantId, role: 'assistant', content: '', tool_name: null, created_at: now },
      ])
      await sendChatMessage(apiBase, activeSessionId, text, ({ event, data }) => {
        if (event === 'message' && typeof data.delta === 'string') {
          setMessages((current) => current.map((message) => (
            message.id === assistantId ? { ...message, content: message.content + data.delta } : message
          )))
        }
        if (event === 'done' && typeof data.message_id === 'number') {
          setMessages((current) => current.map((message) => (
            message.id === assistantId ? { ...message, id: data.message_id as number } : message
          )))
        }
      })
      const rows = await listChatSessions(apiBase)
      setSessions(rows)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Chat failed')
    } finally {
      setBusy(false)
    }
  }, [apiBase, busy, sessionId, startSession])

  return {
    sessions,
    sessionId,
    messages,
    busy,
    error,
    startSession,
    selectSession,
    removeCurrentSession,
    sendMessage,
  }
}
