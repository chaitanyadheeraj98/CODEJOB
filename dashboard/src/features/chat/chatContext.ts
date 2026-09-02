import { createContext, useContext } from 'react'

import type { ProposalResult } from './ProposalCard'
import type { ProposalFields, ProposalHandler } from './proposals'
import type { ChatStatus } from './types'
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
}

export const ChatContext = createContext<ChatContextValue | null>(null)

export function useChat(): ChatContextValue {
  const value = useContext(ChatContext)
  if (value === null) {
    throw new Error('useChat must be called inside <ChatProvider>. Wrap the component under test in one.')
  }
  return value
}
