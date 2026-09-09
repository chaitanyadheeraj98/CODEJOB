import type { ChatMessage } from './types'

// The server sets `answered_by` only where the turn failed over, so this draws
// nothing on an ordinary reply. That is the whole design: a model name under
// every message is furniture, while a model name under the occasional one is
// the app admitting that the first choice did not answer this one.
//
// It is not a warning. A fallback answer is a real answer, and the fallbacks are
// configured models the user chose; the note exists so a reply that reads
// differently from the rest of the thread has a visible reason.
export function AnsweredBy({ message }: { message: ChatMessage }) {
  if (message.role !== 'assistant' || !message.answered_by) return null
  return (
    <p className="chatAnsweredBy">
      Answered by {message.answered_by} after the first model failed
    </p>
  )
}
