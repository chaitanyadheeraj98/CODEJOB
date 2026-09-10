import { useEffect, useRef, useState, type RefObject } from 'react'

import { copyMessage } from './copyMessage'
import type { ChatMessage } from './types'

type Props = {
  message: ChatMessage
  // The rendered element, so Copy can take the HTML the user is looking at
  // rather than a second rendering of the same markdown.
  contentRef: RefObject<HTMLElement | null>
  onEdit?: (message: ChatMessage) => void
  disabled?: boolean
}

// Copy on any message with text; Edit on the user's own messages only.
//
// Edit does not rewrite history - it loads the old prompt into the composer so
// the user can adjust and send it as a new turn. Rewriting an earlier turn in
// place would mean deciding what happens to the proposal cards and approval
// events that came after it, and this app records those as the authority for
// anything that changed a record. That is a product decision, not a UI one.
export function MessageActions({ message, contentRef, onEdit, disabled }: Props) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])

  if (!message.content) return null
  const canEdit = message.role === 'user' && Boolean(onEdit)

  const copy = async () => {
    try {
      await copyMessage(message.content, contentRef.current?.innerHTML)
      setCopied(true)
      // Local, on the button that was pressed. A global toast cannot say *which*
      // message was copied, which is the only thing the user needs to know.
      if (timer.current) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="messageActions">
      <button type="button" onClick={() => void copy()} aria-label={`Copy this ${message.role} message`}>
        {copied ? 'Copied' : 'Copy'}
      </button>
      {canEdit ? (
        <button type="button" onClick={() => onEdit?.(message)} disabled={disabled} aria-label="Edit this message and send it again">
          Edit
        </button>
      ) : null}
    </div>
  )
}
