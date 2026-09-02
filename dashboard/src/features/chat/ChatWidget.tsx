import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'

import { getChatStatus, runProposalAction } from './api'
import { renderMarkdownLite } from './markdown'
import ProposalCard from './ProposalCard'
import { proposalForMessage, proposalResultDetail } from './proposals'
import type { ChatStatus } from './types'
import { useChatSession } from './useChatSession'


type ChatWidgetProps = {
  apiBase: string
}


const MODEL_STORAGE_KEY = 'codejob.chat.model'

export default function ChatWidget({ apiBase }: ChatWidgetProps) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState<ChatStatus | null>(null)
  const [statusError, setStatusError] = useState('')
  const [selectedModel, setSelectedModel] = useState(() => {
    try {
      return window.localStorage.getItem(MODEL_STORAGE_KEY) || 'auto'
    } catch {
      return 'auto'
    }
  })
  const [proposalResults, setProposalResults] = useState<Record<number, { approved: boolean; detail: string } | 'cancelled'>>({})
  const [proposalBusyId, setProposalBusyId] = useState<number | null>(null)
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

  useEffect(() => {
    if (open) chat.markSeen()
  }, [open, chat.markSeen])

  // A model saved from a previous session may no longer be configured -
  // fall back to Auto rather than silently sending an unknown model name.
  useEffect(() => {
    if (!status || selectedModel === 'auto') return
    if (!(status.available_models ?? []).includes(selectedModel)) setSelectedModel('auto')
  }, [status, selectedModel])

  const selectModel = (model: string) => {
    setSelectedModel(model)
    try {
      window.localStorage.setItem(MODEL_STORAGE_KEY, model)
    } catch {
      // ignore - per-device convenience only
    }
  }

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

  const approveProposal = async (messageId: number, proposal: NonNullable<ReturnType<typeof proposalForMessage>>) => {
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
                      onChange={(event) => selectModel(event.target.value)}
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
                    const result = proposalResults[message.id]
                    return (
                      <ProposalCard
                        key={message.id}
                        handler={proposal.handler}
                        fields={proposal.fields}
                        result={result}
                        busy={proposalBusyId === message.id}
                        disabled={proposalBusyId != null}
                        onApprove={() => void approveProposal(message.id, proposal)}
                        onCancel={() => setProposalResults((current) => ({ ...current, [message.id]: 'cancelled' }))}
                      />
                    )
                  }
                  if (message.role === 'tool') return null
                  return (
                    <div key={message.id} className={`chatBubble ${message.role}`}>
                      {message.content
                        ? renderMarkdownLite(message.content)
                        : chat.busy && message.role === 'assistant' ? 'Thinking...' : ''}
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
