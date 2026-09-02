import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { getChatStatus, runProposalAction } from './api'
import { ChatContext, type ChatContextValue } from './chatContext'
import type { ProposalResult } from './ProposalCard'
import { proposalResultDetail, type ProposalFields, type ProposalHandler } from './proposals'
import type { ChatStatus } from './types'
import { useChatSession } from './useChatSession'


type ChatProviderProps = {
  apiBase: string
  onFocusCandidate?: (candidateId: number) => void
  children: ReactNode
}

const MODEL_STORAGE_KEY = 'codejob.chat.model'

// Owns everything the floating widget and the Assistant workspace must agree
// on. Two independent useChatSession instances would mean two polls, two unseen
// counters that clear separately, and - the user-visible one - two message
// lists, so a message sent on one surface would not appear on the other until
// its own 20s poll fired.
export default function ChatProvider({ apiBase, onFocusCandidate, children }: ChatProviderProps) {
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
  const ready = Boolean(status?.enabled && status.ollama_running)
  const chat = useChatSession(apiBase, ready)

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
    setProposalBusyId(messageId)
    try {
      const result = await runProposalAction(apiBase, proposal.handler, proposal.fields)
      setProposalResults((current) => ({
        ...current,
        [messageId]: { approved: true, detail: proposalResultDetail(result) },
      }))
    } catch (reason) {
      setProposalResults((current) => ({
        ...current,
        [messageId]: { approved: false, detail: reason instanceof Error ? reason.message : 'Action failed' },
      }))
    } finally {
      setProposalBusyId(null)
    }
  }, [apiBase])

  const cancelProposal = useCallback((messageId: number) => {
    setProposalResults((current) => ({ ...current, [messageId]: 'cancelled' }))
  }, [])

  const focusCandidate = useCallback((candidateId: number) => {
    onFocusCandidate?.(candidateId)
  }, [onFocusCandidate])

  const value: ChatContextValue = {
    ...chat,
    apiBase,
    status,
    statusError,
    refreshStatus,
    ready,
    selectedModel,
    selectModel,
    proposalResults,
    proposalBusyId,
    approveProposal,
    cancelProposal,
    focusCandidate,
  }

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>
}
