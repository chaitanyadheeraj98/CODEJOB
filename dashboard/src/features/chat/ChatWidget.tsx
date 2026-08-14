import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'

import { getChatStatus } from './api'
import type { ChatStatus } from './types'
import { useChatSession } from './useChatSession'


type ChatWidgetProps = {
  apiBase: string
}

export default function ChatWidget({ apiBase }: ChatWidgetProps) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState<ChatStatus | null>(null)
  const [statusError, setStatusError] = useState('')
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
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

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: 'nearest' })
  }, [chat.messages])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const text = draft.trim()
    if (!text || chat.busy) return
    setDraft('')
    await chat.sendMessage(text)
  }

  return (
    <aside className="chatWidget" aria-label="CodeJob assistant">
      {open ? (
        <section className="chatPanel" role="dialog" aria-label="CodeJob assistant chat">
          <header className="chatHeader">
            <div>
              <strong>CodeJob Assistant</strong>
              <small>{status?.model ?? 'Checking Ollama...'}</small>
            </div>
            <div className="chatHeaderActions">
              <button type="button" onClick={() => void chat.startSession()} disabled={!ready || chat.busy} aria-label="New chat">+</button>
              <button type="button" onClick={() => setOpen(false)} aria-label="Close chat">x</button>
            </div>
          </header>

          {!status && !statusError ? <p className="chatState">Checking Ollama connection...</p> : null}
          {statusError ? (
            <div className="chatState">
              <p>{statusError}</p>
              <button type="button" onClick={() => void refreshStatus()}>Retry</button>
            </div>
          ) : null}
          {status && !status.enabled ? (
            <div className="chatState">
              <strong>Chat is disabled</strong>
              <p>Set <code>FEATURE_CHAT_ENABLED=true</code> and restart the backend.</p>
            </div>
          ) : null}
          {status?.enabled && !status.ollama_running ? (
            <div className="chatState">
              <strong>Ollama is not connected</strong>
              <p>Start Ollama and make sure {status.model} is available.</p>
              <button type="button" onClick={() => void refreshStatus()}>Check again</button>
            </div>
          ) : null}

          {ready ? (
            <>
              <div className="chatHistoryBar">
                <label>
                  <span className="visuallyHidden">Chat history</span>
                  <select
                    aria-label="Chat history"
                    value={chat.sessionId ?? ''}
                    onChange={(event) => void chat.selectSession(Number(event.target.value))}
                    disabled={chat.busy || !chat.sessions.length}
                  >
                    {!chat.sessions.length ? <option value="">New conversation</option> : null}
                    {chat.sessions.map((session) => (
                      <option key={session.id} value={session.id}>{session.title || `Chat ${session.id}`}</option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  onClick={() => void chat.removeCurrentSession()}
                  disabled={chat.busy || chat.sessionId == null}
                  aria-label="Delete current chat"
                >
                  Delete
                </button>
              </div>
              <div className="chatMessages" aria-live="polite">
                {!chat.messages.length ? (
                  <div className="chatWelcome">
                    <strong>Ask about CodeJob</strong>
                    <p>Try "How many candidates need review?" or "Summarize my latest run."</p>
                  </div>
                ) : null}
                {chat.messages.map((message) => (
                  <div key={message.id} className={`chatBubble ${message.role}`}>
                    {message.content || (chat.busy && message.role === 'assistant' ? 'Thinking...' : '')}
                  </div>
                ))}
                <div ref={messagesEndRef} />
              </div>
              {chat.error ? <p className="chatError" role="alert">{chat.error}</p> : null}
              <form className="chatComposer" onSubmit={(event) => void submit(event)}>
                <label className="visuallyHidden" htmlFor="chat-message">Message CodeJob Assistant</label>
                <textarea
                  id="chat-message"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  maxLength={4000}
                  rows={2}
                  placeholder="Ask about candidates, runs, inbox, or settings..."
                  disabled={chat.busy}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      event.currentTarget.form?.requestSubmit()
                    }
                  }}
                />
                <button type="submit" disabled={chat.busy || !draft.trim()}>
                  {chat.busy ? 'Working...' : 'Send'}
                </button>
              </form>
            </>
          ) : null}
        </section>
      ) : null}
      <button
        type="button"
        className="chatLauncher"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-label={open ? 'Close CodeJob assistant' : 'Open CodeJob assistant'}
      >
        {open ? 'x' : 'Chat'}
      </button>
    </aside>
  )
}
