import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { getChatStatus, listChatAttachments, recordProposalOutcome, runProposalAction } from './api'
import { ChatContext, type ChatContextValue } from './chatContext'
import type { ProposalResult } from './ProposalCard'
import { proposalResultDetail, type ProposalFields, type ProposalHandler } from './proposals'
import type { QueueTarget } from '../../queueNavigation'
import type { ChatAttachment, ChatStatus } from './types'
import { useChatSession } from './useChatSession'


type ChatProviderProps = {
  apiBase: string
  onFocusCandidate?: (candidateId: number) => void
  onNavigateToQueue?: (target: QueueTarget) => void
  // Called after a confirmed profile write, so the Settings panel stops showing
  // the pre-write filename and character count. Supplied by App, which owns the
  // settings state; a no-op without it.
  onProfileChanged?: () => void
  children: ReactNode
}

const MODEL_STORAGE_KEY = 'codejob.chat.model'

// Owns everything the floating widget and the Assistant workspace must agree
// on. Two independent useChatSession instances would mean two polls, two unseen
// counters that clear separately, and - the user-visible one - two message
// lists, so a message sent on one surface would not appear on the other until
// its own 20s poll fired.
export default function ChatProvider({ apiBase, onFocusCandidate, onNavigateToQueue, onProfileChanged, children }: ChatProviderProps) {
  const [status, setStatus] = useState<ChatStatus | null>(null)
  const [statusError, setStatusError] = useState('')
  const [selectedModel, setSelectedModel] = useState(() => {
    try {
      return window.localStorage.getItem(MODEL_STORAGE_KEY) || 'auto'
    } catch {
      return 'auto'
    }
  })
  const [proposalResults, setProposalResults] = useState<Record<number, ProposalResult>>({})
  const [proposalBusyId, setProposalBusyId] = useState<number | null>(null)
  const [attachments, setAttachments] = useState<ChatAttachment[]>([])
  const ready = Boolean(status?.enabled && status.ollama_running)
  const chat = useChatSession(apiBase, ready)
  const { sessionId, busy, seededResults } = chat

  // Posted for every proposal tool, not just profile writes: the gap is nine
  // tools wide, the row costs nothing, and scoping it to one would mean building
  // the same mechanism again the next time it is needed. Never blocks the user -
  // a write that succeeded must not report failure because bookkeeping did.
  const postOutcome = useCallback(async (
    messageId: number,
    toolName: string,
    outcome: 'confirmed' | 'cancelled' | 'failed',
    characters?: number,
  ) => {
    if (sessionId == null) return
    try {
      await recordProposalOutcome(apiBase, sessionId, {
        tool_name: toolName,
        outcome,
        proposal_message_id: messageId,
        ...(typeof characters === 'number' ? { characters } : {}),
      })
    } catch {
      // Bookkeeping only. The action itself already happened or did not.
    }
  }, [apiBase, sessionId])

  const refreshAttachments = useCallback(async () => {
    if (!ready || sessionId == null) return
    try {
      setAttachments(await listChatAttachments(apiBase, sessionId))
    } catch {
      // Chips are a convenience; a failed refresh must not break the thread.
    }
  }, [apiBase, ready, sessionId])

  // Reloads on session change and whenever a send finishes, which is the
  // only moment an upload becomes bound to a message.
  useEffect(() => {
    if (!ready || sessionId == null) {
      setAttachments([])
      return
    }
    if (busy) return
    let active = true
    listChatAttachments(apiBase, sessionId).then(
      (rows) => { if (active) setAttachments(rows) },
      () => {},
    )
    return () => { active = false }
  }, [apiBase, ready, sessionId, busy])

  const refreshStatus = useCallback(async () => {
    setStatusError('')
    try {
      setStatus(await getChatStatus(apiBase))
    } catch (reason) {
      setStatusError(reason instanceof Error ? reason.message : 'Chat status unavailable')
    }
  }, [apiBase])

  useEffect(() => {
    let active = true
    getChatStatus(apiBase).then(
      (nextStatus) => {
        if (active) setStatus(nextStatus)
      },
      (reason: unknown) => {
        if (active) setStatusError(reason instanceof Error ? reason.message : 'Chat status unavailable')
      },
    )
    return () => {
      active = false
    }
  }, [apiBase])

  // A model saved from a previous session may no longer be configured -
  // fall back to Auto rather than silently sending an unknown model name.
  useEffect(() => {
    if (!status || selectedModel === 'auto') return
    if (!(status.available_models ?? []).includes(selectedModel)) setSelectedModel('auto')
  }, [status, selectedModel])

  const selectModel = useCallback((model: string) => {
    setSelectedModel(model)
    try {
      window.localStorage.setItem(MODEL_STORAGE_KEY, model)
    } catch {
      // ignore - per-device convenience only
    }
  }, [])

  const approveProposal = useCallback(async (
    messageId: number,
    proposal: { handler: ProposalHandler; fields: ProposalFields },
  ) => {
    const toolName = String(proposal.fields.action ?? '')
    setProposalBusyId(messageId)
    try {
      const result = await runProposalAction(apiBase, proposal.handler, proposal.fields)
      setProposalResults((current) => ({
        ...current,
        [messageId]: { approved: true, detail: proposalResultDetail(result, proposal.fields) },
      }))
      void postOutcome(
        messageId,
        toolName,
        'confirmed',
        typeof result.characters === 'number' ? result.characters : undefined,
      )
      if (proposal.fields.action === 'propose_profile_update') onProfileChanged?.()
    } catch (reason) {
      setProposalResults((current) => ({
        ...current,
        [messageId]: { approved: false, detail: reason instanceof Error ? reason.message : 'Action failed' },
      }))
      void postOutcome(messageId, toolName, 'failed')
    } finally {
      setProposalBusyId(null)
    }
  }, [apiBase, onProfileChanged, postOutcome])

  const cancelProposal = useCallback((messageId: number, toolName = '') => {
    setProposalResults((current) => ({ ...current, [messageId]: 'cancelled' }))
    // Recorded too. A later turn that cannot tell a cancel from a confirm will
    // repeat whatever it claimed last time.
    void postOutcome(messageId, toolName, 'cancelled')
  }, [postOutcome])

  const focusCandidate = useCallback((candidateId: number) => {
    onFocusCandidate?.(candidateId)
  }, [onFocusCandidate])

  const navigateToQueue = useCallback((target: QueueTarget) => {
    onNavigateToQueue?.(target)
  }, [onNavigateToQueue])

  const value: ChatContextValue = {
    ...chat,
    apiBase,
    status,
    statusError,
    refreshStatus,
    ready,
    selectedModel,
    selectModel,
    // History first, this session's clicks over the top: a card confirmed before
    // a reload keeps its outcome, and one confirmed just now shows the detail
    // the route actually returned.
    proposalResults: { ...seededResults, ...proposalResults },
    proposalBusyId,
    approveProposal,
    cancelProposal,
    focusCandidate,
    navigateToQueue,
    attachments,
    refreshAttachments,
  }

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>
}
