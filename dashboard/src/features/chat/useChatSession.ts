import { useCallback, useEffect, useRef, useState } from 'react'

import {
  createChatSession,
  deleteChatSession,
  getChatSession,
  listChatSessions,
  renameChatSession,
  sendChatMessage,
} from './api'
import type { ChatMessage, ChatSession } from './types'
import { PROPOSAL_HANDLERS } from './proposals'


function visibleMessages(messages: ChatMessage[]): ChatMessage[] {
  return messages.filter((message) => (
    Boolean(message.content)
    && (message.role !== 'tool' || Boolean(message.tool_name && PROPOSAL_HANDLERS[message.tool_name]))
  ))
}

export function useChatSession(apiBase: string, enabled: boolean) {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [unseenCount, setUnseenCount] = useState(0)
  const messagesRef = useRef<ChatMessage[]>([])
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  const markSeen = useCallback(() => setUnseenCount(0), [])

  // Picks up notifications a background sync posts into this session (e.g. a
  // recruiter reply worth flagging) without the user having to send a message.
  useEffect(() => {
    if (!enabled || sessionId == null) return
    const interval = window.setInterval(() => {
      if (busy) return
      getChatSession(apiBase, sessionId).then((detail) => {
        const next = visibleMessages(detail.messages)
        const previous = messagesRef.current
        if (next.length > previous.length) {
          setUnseenCount((count) => count + (next.length - previous.length))
          setMessages(next)
        }
      }, () => {})
    }, 20000)
    return () => window.clearInterval(interval)
  }, [apiBase, enabled, sessionId, busy])

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
          setMessages(visibleMessages(detail.messages))
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
      setMessages(visibleMessages(detail.messages))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Failed to load chat')
    } finally {
      setBusy(false)
    }
  }, [apiBase])

  const renameCurrentSession = useCallback(async (title: string) => {
    if (sessionId == null) return
    const updated = await renameChatSession(apiBase, sessionId, title)
    setSessions((current) => current.map((session) => (session.id === updated.id ? updated : session)))
  }, [apiBase, sessionId])

  const removeCurrentSession = useCallback(async () => {
    if (sessionId == null) return
    await deleteChatSession(apiBase, sessionId)
    const rows = await listChatSessions(apiBase)
    setSessions(rows)
    if (rows[0]) {
      const detail = await getChatSession(apiBase, rows[0].id)
      setSessionId(detail.id)
      setMessages(visibleMessages(detail.messages))
    } else {
      setSessionId(null)
      setMessages([])
    }
  }, [apiBase, sessionId])

  const sendMessage = useCallback(async (rawText: string, model?: string) => {
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
      }, model)
      const detail = await getChatSession(apiBase, activeSessionId)
      setMessages(visibleMessages(detail.messages))
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
    unseenCount,
    markSeen,
    startSession,
    selectSession,
    renameCurrentSession,
    removeCurrentSession,
    sendMessage,
  }
}

// Named so ChatProvider can widen it without re-listing every member, and so a
// new field here reaches the context automatically.
export type ChatSessionApi = ReturnType<typeof useChatSession>
