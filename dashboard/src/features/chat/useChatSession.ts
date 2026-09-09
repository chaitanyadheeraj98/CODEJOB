import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  createChatSession,
  deleteChatSession,
  getChatSession,
  listChatSessions,
  renameChatSession,
  sendChatMessage,
  stopChatTurn,
} from './api'
import type { ProposalResult } from './ProposalCard'
import type { ActiveTool, ChatMessage, ChatSession, CompletedTool } from './types'
import { PROPOSAL_HANDLERS, isProposalToolName } from './proposals'
import { RENDER_HANDLERS } from './renderers'


// Tool messages are kept only when something knows how to draw them: a
// confirmation card, or a rendered view. Consulting one registry and not the
// other silently deletes the other's messages from history on both surfaces.
//
// The third clause is not a registry. A propose_* row is kept even when nothing
// here can draw it, because dropping it is what made propose_nvoids_search
// invisible rather than merely broken - the assistant said a search was ready
// to confirm and there was no row left to contradict it. Kept, it renders as
// `unsupportedProposalNotice`: the app saying it cannot show this, which is a
// worse card and a much better silence.
function visibleMessages(messages: ChatMessage[]): ChatMessage[] {
  return messages.filter((message) => (
    Boolean(message.content)
    && (
      message.role !== 'tool'
      || Boolean(message.tool_name && (
        PROPOSAL_HANDLERS[message.tool_name]
        || RENDER_HANDLERS[message.tool_name]
        || isProposalToolName(message.tool_name)
      ))
    )
  ))
}

// What actually happened to each proposal card, read back from the persisted
// event rows. Without this a refresh returns every card to unresolved: its
// Confirm button goes live again and can submit a second time, and the pending
// notice would claim nothing has been saved on a card that was confirmed
// minutes ago. An event row that matches no card is ignored, never guessed at.
function resultsFromHistory(messages: ChatMessage[]): Record<number, ProposalResult> {
  const seeded: Record<number, ProposalResult> = {}
  const proposalIds = new Set(
    messages
      .filter((message) => message.role === 'tool' && message.tool_name && PROPOSAL_HANDLERS[message.tool_name])
      .map((message) => message.id),
  )
  for (const message of messages) {
    if (message.role !== 'event') continue
    const target = message.proposal_message_id
    if (typeof target !== 'number' || !proposalIds.has(target)) continue
    if (message.outcome === 'cancelled') {
      seeded[target] = 'cancelled'
    } else if (message.outcome === 'confirmed' || message.outcome === 'failed') {
      seeded[target] = { approved: message.outcome === 'confirmed', detail: message.content }
    }
  }
  return seeded
}

// Tracked against the *unfiltered* server payload. Using the visible list would
// re-request any trailing message visibleMessages drops on every poll, and using
// the rendered list at all would pick up sendMessage's optimistic negative ids.
function newestMessageId(messages: ChatMessage[]): number {
  return messages.reduce((newest, message) => (message.id > newest ? message.id : newest), 0)
}

export function useChatSession(apiBase: string, enabled: boolean) {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [activeTool, setActiveTool] = useState<ActiveTool | null>(null)
  // What has already finished this turn. Cleared when the next turn starts,
  // never accumulated across turns: it is progress, not history.
  const [completedTools, setCompletedTools] = useState<CompletedTool[]>([])
  const [error, setError] = useState('')
  const [lastMessage, setLastMessage] = useState<{ text: string; model?: string } | null>(null)
  const [unseenCount, setUnseenCount] = useState(0)
  // A ref, not state: the SSE callback closes over this and must read the id the
  // stream just issued, not the one that existed when the callback was created.
  // `stoppable` is the render-visible half, kept in step with it.
  const [stoppable, setStoppable] = useState(false)
  const activeTurnRef = useRef<{ sessionId: number; turnId: string } | null>(null)
  const newestIdRef = useRef(0)

  const markSeen = useCallback(() => setUnseenCount(0), [])

  // Derived from the thread rather than seeded at each load path. The 20s poll
  // appends a delta that may carry an outcome whose card arrived in an earlier
  // batch, so pairing has to happen against the whole thread; deriving it also
  // means switching sessions cannot leave the previous one's results behind.
  const seededResults = useMemo(() => resultsFromHistory(messages), [messages])

  // Picks up notifications a background sync posts into this session (e.g. a
  // recruiter reply worth flagging) without the user having to send a message.
  useEffect(() => {
    if (!enabled || sessionId == null) return
    const interval = window.setInterval(() => {
      if (busy) return
      const knownUpTo = newestIdRef.current
      getChatSession(apiBase, sessionId, knownUpTo || undefined).then((detail) => {
        newestIdRef.current = Math.max(knownUpTo, newestMessageId(detail.messages))
        // Re-filter rather than appending whatever came back. A server that
        // ignores since_id - an older backend, a proxy that drops the query
        // string - returns the whole thread, and appending it duplicates the
        // conversation on every poll. Seen for real against a stale container.
        const added = visibleMessages(detail.messages).filter((message) => message.id > knownUpTo)
        if (!added.length) return
        setUnseenCount((count) => count + added.length)
        setMessages((current) => [...current, ...added])
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
          newestIdRef.current = newestMessageId(detail.messages)
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
    newestIdRef.current = 0
    setMessages([])
    setError('')
    setLastMessage(null)
    return session
  }, [apiBase])

  const selectSession = useCallback(async (nextId: number) => {
    setBusy(true)
    setError('')
    setLastMessage(null)
    try {
      const detail = await getChatSession(apiBase, nextId)
      setSessionId(detail.id)
      newestIdRef.current = newestMessageId(detail.messages)
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
    setLastMessage(null)
    setError('')
    await deleteChatSession(apiBase, sessionId)
    const rows = await listChatSessions(apiBase)
    setSessions(rows)
    if (rows[0]) {
      const detail = await getChatSession(apiBase, rows[0].id)
      setSessionId(detail.id)
      newestIdRef.current = newestMessageId(detail.messages)
      setMessages(visibleMessages(detail.messages))
    } else {
      setSessionId(null)
      newestIdRef.current = 0
      setMessages([])
    }
  }, [apiBase, sessionId])

  const sendMessage = useCallback(async (rawText: string, model?: string, attachmentIds: number[] = []) => {
    const text = rawText.trim()
    if (!text || busy) return
    setBusy(true)
    setError('')
    setActiveTool(null)
    setCompletedTools([])
    try {
      const activeSessionId = sessionId ?? (await startSession()).id
      setLastMessage({ text, model })
      const now = new Date().toISOString()
      const userId = -Date.now()
      const assistantId = userId - 1
      setMessages((current) => [
        ...current,
        { id: userId, role: 'user', content: text, tool_name: null, created_at: now },
        { id: assistantId, role: 'assistant', content: '', tool_name: null, created_at: now },
      ])
      await sendChatMessage(apiBase, activeSessionId, text, ({ event, data }) => {
        // The turn id arrives before any token, so Stop is live for the whole
        // wait rather than only once the model starts talking - which is the
        // half of the turn a user actually wants to be able to end.
        if (event === 'start' && typeof data.turn_id === 'string') {
          activeTurnRef.current = { sessionId: activeSessionId, turnId: data.turn_id }
          setStoppable(true)
        }
        if (event === 'error' && typeof data.message === 'string') setError(data.message)
        // Progress for a tool the assistant just started. Replaced when another
        // tool follows, and cleared the moment prose starts arriving, because
        // text on screen is its own proof that the turn is still alive.
        if (event === 'tool' && typeof data.name === 'string') {
          setActiveTool({ name: data.name, startedAt: Date.now() })
        }
        // Keeps the finished call on screen once the bar for it disappears, so a
        // turn that ran four lookups does not read as one long unexplained wait.
        if (event === 'tool_done' && typeof data.name === 'string') {
          setCompletedTools((current) => [...current, {
            name: data.name as string,
            duration_ms: typeof data.duration_ms === 'number' ? data.duration_ms : 0,
            status: typeof data.status === 'string' ? data.status : 'ok',
          }])
        }
        if (event === 'message' && typeof data.delta === 'string') {
          setActiveTool(null)
          setMessages((current) => current.map((message) => (
            message.id === assistantId ? { ...message, content: message.content + data.delta } : message
          )))
        }
        if (event === 'done' && typeof data.message_id === 'number') {
          setMessages((current) => current.map((message) => (
            message.id === assistantId ? { ...message, id: data.message_id as number } : message
          )))
        }
      }, model, attachmentIds)
      const detail = await getChatSession(apiBase, activeSessionId)
      newestIdRef.current = newestMessageId(detail.messages)
      setMessages(visibleMessages(detail.messages))
      const rows = await listChatSessions(apiBase)
      setSessions(rows)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Chat failed')
    } finally {
      // Cleared in `finally` as well as on the first delta: a turn that fails or
      // times out mid-tool must not leave a progress bar running forever.
      setActiveTool(null)
      setBusy(false)
      activeTurnRef.current = null
      setStoppable(false)
    }
  }, [apiBase, busy, sessionId, startSession])

  // Asks the server to cancel and then stops - it does not touch `messages`.
  // The stream is still open, the server still has the partial answer to write,
  // and the reload at the end of sendMessage is what brings back the transcript
  // including the "[System: the user stopped..." row. Clearing anything here
  // would show the user less than was actually saved.
  const stop = useCallback(async () => {
    const active = activeTurnRef.current
    if (!active) return
    setStoppable(false)
    try {
      await stopChatTurn(apiBase, active.sessionId, active.turnId)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Failed to stop the assistant')
    }
  }, [apiBase])

  return {
    sessions,
    sessionId,
    messages,
    busy,
    activeTool,
    completedTools,
    error,
    unseenCount,
    markSeen,
    startSession,
    selectSession,
    renameCurrentSession,
    removeCurrentSession,
    sendMessage,
    // Safe while tools only read or propose; writes still require a card click.
    retry: error && lastMessage ? () => sendMessage(lastMessage.text, lastMessage.model) : null,
    // Null rather than a disabled control until the server has issued a turn id,
    // so Stop is never offered for a turn nothing can cancel.
    stop: stoppable ? stop : null,
    seededResults,
  }
}

// Named so ChatProvider can widen it without re-listing every member, and so a
// new field here reaches the context automatically.
export type ChatSessionApi = ReturnType<typeof useChatSession>
