import { useEffect, useRef, useState, type FormEvent } from 'react'

import { useChat } from './chatContext'
import { renderMarkdownLite } from './markdown'
import ProposalCard from './ProposalCard'
import { proposalForMessage } from './proposals'
import CandidateTableCompact from './CandidateTableCompact'
import { renderForMessage } from './renderers'
import { SentAttachmentChips } from './AttachmentChips'


// `open` and `draft` stay local: they are genuinely per-surface. Everything
// else - session, messages, status, model, proposal results - comes from
// ChatProvider so the workspace page and this widget never diverge.
export default function ChatWidget() {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const chat = useChat()
  const { ready, status, statusError, selectedModel } = chat

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: 'nearest' })
  }, [chat.messages])

  useEffect(() => {
    if (open) chat.markSeen()
  }, [open, chat.markSeen])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const text = draft.trim()
    if (!text || chat.busy) return
    setDraft('')
    await chat.sendMessage(text, selectedModel)
  }

  const currentSession = chat.sessions.find((session) => session.id === chat.sessionId)

  const renameCurrent = async () => {
    const next = window.prompt('Rename chat', currentSession?.title ?? '')
    if (next == null || !next.trim() || next.trim() === currentSession?.title) return
    await chat.renameCurrentSession(next.trim())
  }

  return (
    <aside className="chatWidget" aria-label="CodeJob assistant">
      {open ? (
        <section className="chatPanel" role="dialog" aria-label="CodeJob assistant chat">
          <header className="chatHeader">
            <div>
              <strong>CodeJob Assistant</strong>
              <small>
                {!status ? 'Checking Ollama...' : selectedModel === 'auto' ? `Auto · ${status.model}` : selectedModel}
              </small>
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
              <button type="button" onClick={() => void chat.refreshStatus()}>Retry</button>
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
              <button type="button" onClick={() => void chat.refreshStatus()}>Check again</button>
            </div>
          ) : null}

          {ready ? (
            <>
              <div className="chatHistoryBar">
                <div className="chatHistoryRow">
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
                    onClick={() => void renameCurrent()}
                    disabled={chat.busy || chat.sessionId == null}
                    aria-label="Rename current chat"
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    onClick={() => void chat.removeCurrentSession()}
                    disabled={chat.busy || chat.sessionId == null}
                    aria-label="Delete current chat"
                  >
                    Delete
                  </button>
                </div>
                <div className="chatModelRow">
                  <label className="chatModelPicker">
                    <span>Model</span>
                    <select
                      aria-label="Chat model"
                      value={selectedModel}
                      onChange={(event) => chat.selectModel(event.target.value)}
                      disabled={chat.busy}
                      title="Auto picks the primary model and falls back automatically if it's unavailable"
                    >
                      <option value="auto">Auto</option>
                      {(status?.available_models ?? []).map((model) => (
                        <option key={model} value={model}>{model}</option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>
              <div className="chatMessages" aria-live="polite">
                {!chat.messages.length ? (
                  <div className="chatWelcome">
                    <strong>Ask about CodeJob</strong>
                    <p>Try "How many candidates need review?" or "Summarize my latest run."</p>
                  </div>
                ) : null}
                {chat.messages.map((message) => {
                  const proposal = proposalForMessage(message)
                  if (proposal) {
                    return (
                      <ProposalCard
                        key={message.id}
                        handler={proposal.handler}
                        fields={proposal.fields}
                        result={chat.proposalResults[message.id]}
                        busy={chat.proposalBusyId === message.id}
                        disabled={chat.proposalBusyId != null}
                        onApprove={() => void chat.approveProposal(message.id, proposal)}
                        onCancel={() => chat.cancelProposal(message.id)}
                      />
                    )
                  }
                  // The second filter. visibleMessages keeps render messages;
                  // without this they still vanish here.
                  const rendered = renderForMessage(message)
                  if (rendered) {
                    return (
                      <div key={message.id} className="chatBubble assistant">
                        <CandidateTableCompact data={rendered.data} />
                      </div>
                    )
                  }
                  if (message.role === 'tool') return null
                  return (
                    <div key={message.id} className={`chatBubble ${message.role}`}>
                      {message.content
                        ? renderMarkdownLite(message.content)
                        : chat.busy && message.role === 'assistant' ? 'Thinking...' : ''}
                      <SentAttachmentChips
                        apiBase={chat.apiBase}
                        attachments={chat.attachments.filter((item) => item.message_id === message.id)}
                      />
                    </div>
                  )
                })}
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
        {!open && chat.unseenCount > 0 ? (
          <span className="chatBadge" aria-label={`${chat.unseenCount} new`}>{chat.unseenCount}</span>
        ) : null}
      </button>
    </aside>
  )
}
