import { createContext, useContext } from 'react'

import type { ProposalResult } from './ProposalCard'
import type { ProposalFields, ProposalHandler } from './proposals'
import type { QueueTarget } from '../../queueNavigation'
import type { ChatAttachment, ChatStatus } from './types'
import type { ChatSessionApi } from './useChatSession'

// Split from ChatProvider.tsx so that file exports only a component, which is
// what react-refresh/only-export-components wants.
export type ChatContextValue = ChatSessionApi & {
  apiBase: string
  status: ChatStatus | null
  statusError: string
  refreshStatus: () => Promise<void>
  ready: boolean
  selectedModel: string
  selectModel: (model: string) => void
  proposalResults: Record<number, ProposalResult>
  proposalBusyId: number | null
  approveProposal: (messageId: number, proposal: { handler: ProposalHandler; fields: ProposalFields }) => Promise<void>
  cancelProposal: (messageId: number) => void
  // Opens a candidate in Needs Review and highlights it. Supplied by App,
  // because the navigation state lives there; a no-op when the provider is
  // mounted without it (tests, or any future host that has no such page).
  focusCandidate: (candidateId: number) => void
  // Opens a work queue with filters pre-applied. Supplied by App for the same
  // reason focusCandidate is - the filter state lives there - and a no-op when
  // the provider is mounted without it.
  navigateToQueue: (target: QueueTarget) => void
  // Every attachment on the open session, so both surfaces can show chips on
  // the messages that carry them. Uploading stays page-only.
  attachments: ChatAttachment[]
  refreshAttachments: () => Promise<void>
}

export const ChatContext = createContext<ChatContextValue | null>(null)

export function useChat(): ChatContextValue {
  const value = useContext(ChatContext)
  if (value === null) {
    throw new Error('useChat must be called inside <ChatProvider>. Wrap the component under test in one.')
  }
  return value
}
