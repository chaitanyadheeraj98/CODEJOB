import { useRef } from 'react'

import { AnsweredBy } from './AnsweredBy'
import { SentAttachmentChips } from './AttachmentChips'
import { useChat } from './chatContext'
import { renderMarkdownLite, type Citations } from './markdown'
import { MessageActions } from './MessageActions'
import { ToolProgress } from './ToolProgress'
import type { ChatMessage } from './types'

type Props = {
  message: ChatMessage
  citations: Citations
  onEdit?: (message: ChatMessage) => void
}

// One bubble, used by both the widget and the page.
//
// It exists because the two surfaces had the same twenty lines twice, and every
// addition since - attachment chips, the fallback-model note, tool progress -
// had to be made in both places or silently appear in one. Copy and Edit would
// have been the fourth.
export default function MessageBubble({ message, citations, onEdit }: Props) {
  const chat = useChat()
  // Copy reads the HTML from here, so the paste carries what is on screen.
  const content = useRef<HTMLDivElement | null>(null)
  const attachments = chat.attachments.filter((item) => item.message_id === message.id)

  return (
    <div className={`chatBubble ${message.role}`}>
      <div className="chatBubbleBody" ref={content}>
        {message.content
          ? renderMarkdownLite(message.content, citations)
          : chat.busy && message.role === 'assistant'
            ? <ToolProgress key={chat.activeTool?.startedAt ?? 'idle'} tool={chat.activeTool} completed={chat.completedTools} />
            : ''}
      </div>
      <SentAttachmentChips apiBase={chat.apiBase} attachments={attachments} />
      <AnsweredBy message={message} />
      <MessageActions message={message} contentRef={content} onEdit={onEdit} disabled={chat.busy} />
    </div>
  )
}
