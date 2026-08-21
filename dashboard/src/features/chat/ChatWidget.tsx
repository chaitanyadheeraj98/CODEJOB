import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'

import { getChatStatus, runProposalAction } from './api'
import { proposalForMessage, proposalResultDetail } from './proposals'
import type { ChatStatus } from './types'
import { useChatSession } from './useChatSession'


type ChatWidgetProps = {
  apiBase: string
}

function renderInline(line: string) {
  return line.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/).map((chunk, i) => {
    if (chunk.startsWith('**') && chunk.endsWith('**')) return <strong key={i}>{chunk.slice(2, -2)}</strong>
    if (chunk.startsWith('*') && chunk.endsWith('*') && chunk.length > 1) return <em key={i}>{chunk.slice(1, -1)}</em>
    return chunk
  })
}

// ponytail: headings/bold/italic/bullets only, not full markdown. Swap for a real parser if tables/links/code blocks show up.
function renderMarkdownLite(text: string) {
  const blocks: ReactNode[] = []
  let paragraph: string[] = []
  let list: string[] = []

  const flushParagraph = () => {
    if (!paragraph.length) return
    blocks.push(
      <p key={blocks.length}>
        {paragraph.map((line, i) => (
          <span key={i}>
            {renderInline(line)}
            {i < paragraph.length - 1 ? <br /> : null}
          </span>
        ))}
      </p>,
    )
    paragraph = []
  }
  const flushList = () => {
    if (!list.length) return
    blocks.push(
      <ul key={blocks.length}>
        {list.map((line, i) => (
          <li key={i}>{renderInline(line)}</li>
        ))}
      </ul>,
    )
    list = []
  }

  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) {
      flushParagraph()
      flushList()
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.*)/)
    const listItem = line.match(/^[-*]\s+(.*)/)
    if (heading) {
      flushParagraph()
      flushList()
      const level = Math.min(heading[1].length, 3)
      const headingContent = renderInline(heading[2])
      blocks.push(
        level === 1 ? (
          <h4 key={blocks.length}>{headingContent}</h4>
        ) : level === 2 ? (
          <h5 key={blocks.length}>{headingContent}</h5>
        ) : (
          <h6 key={blocks.length}>{headingContent}</h6>
        ),
      )
    } else if (listItem) {
      flushParagraph()
      list.push(listItem[1])
    } else {
      flushList()
      paragraph.push(line)
    }
  }
  flushParagraph()
  flushList()
  return blocks
}

export default function ChatWidget({ apiBase }: ChatWidgetProps) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState<ChatStatus | null>(null)
  const [statusError, setStatusError] = useState('')
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

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const text = draft.trim()
    if (!text || chat.busy) return
    setDraft('')
    await chat.sendMessage(text)
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
                      <div key={message.id} className="chatProposal">
                        <strong>Confirm action</strong>
                        <dl>
                          {proposal.handler.summary(proposal.fields).map(([label, value]) => (
                            <div key={label}><dt>{label}</dt><dd>{value || '-'}</dd></div>
                          ))}
                        </dl>
                        {result === 'cancelled' ? <p>Cancelled. No changes were made.</p> : null}
                        {result && result !== 'cancelled' ? (
                          <p className={result.approved ? '' : 'chatError'}>{result.detail}</p>
                        ) : null}
                        {!result ? (
                          <div className="chatProposalActions">
                            <button
                              type="button"
                              onClick={() => void approveProposal(message.id, proposal)}
                              disabled={proposalBusyId != null}
                            >
                              {proposalBusyId === message.id ? 'Working...' : proposal.handler.confirmLabel(proposal.fields)}
                            </button>
                            <button
                              type="button"
                              onClick={() => setProposalResults((current) => ({ ...current, [message.id]: 'cancelled' }))}
                              disabled={proposalBusyId != null}
                            >
                              Cancel
                            </button>
                          </div>
                        ) : null}
                      </div>
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
      </button>
    </aside>
  )
}
