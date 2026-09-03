import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'

import { getChatStatus, runProposalAction } from './api'
import { proposalForMessage, proposalResultDetail } from './proposals'
import type { ChatStatus } from './types'
import { useChatSession } from './useChatSession'


type ChatWidgetProps = {
  apiBase: string
}

function renderInline(line: string) {
  return line.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/).map((chunk, i) => {
    if (chunk.startsWith('`') && chunk.endsWith('`') && chunk.length > 1) return <code key={i}>{chunk.slice(1, -1)}</code>
    if (chunk.startsWith('**') && chunk.endsWith('**')) return <strong key={i}>{chunk.slice(2, -2)}</strong>
    if (chunk.startsWith('*') && chunk.endsWith('*') && chunk.length > 1) return <em key={i}>{chunk.slice(1, -1)}</em>
    return chunk
  })
}

const TABLE_SEPARATOR_ROW = /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$/

function splitTableRow(line: string): string[] {
  return line.replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
}

// ponytail: headings/bold/italic/bullets/numbered lists/tables/inline code only, not full markdown. Swap for a real parser if fenced code blocks or links show up.
function renderMarkdownLite(text: string) {
  const blocks: ReactNode[] = []
  let paragraph: string[] = []
  let list: string[] = []
  let listOrdered = false

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
    const ListTag = listOrdered ? 'ol' : 'ul'
    blocks.push(
      <ListTag key={blocks.length}>
        {list.map((line, i) => (
          <li key={i}>{renderInline(line)}</li>
        ))}
      </ListTag>,
    )
    list = []
  }

  const lines = text.split('\n')
  let i = 0
  while (i < lines.length) {
    const line = lines[i].trim()
    if (!line) {
      flushParagraph()
      flushList()
      i += 1
      continue
    }
    const nextLine = (lines[i + 1] ?? '').trim()
    const isTable = line.startsWith('|') && line.endsWith('|') && TABLE_SEPARATOR_ROW.test(nextLine)
    if (isTable) {
      flushParagraph()
      flushList()
      const header = splitTableRow(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitTableRow(lines[i].trim()))
        i += 1
      }
      blocks.push(
        <table key={blocks.length}>
          <thead>
            <tr>{header.map((cell, c) => <th key={c}>{renderInline(cell)}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>{row.map((cell, c) => <td key={c}>{renderInline(cell)}</td>)}</tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.*)/)
    const orderedItem = line.match(/^\d+\.\s+(.*)/)
    const bulletItem = line.match(/^[-*]\s+(.*)/)
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
    } else if (orderedItem || bulletItem) {
      flushParagraph()
      const ordered = Boolean(orderedItem)
      if (list.length && listOrdered !== ordered) flushList()
      listOrdered = ordered
      list.push(ordered ? orderedItem![1] : bulletItem![1])
    } else {
      flushList()
      paragraph.push(line)
    }
    i += 1
  }
  flushParagraph()
  flushList()
  return blocks
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
        {!open && chat.unseenCount > 0 ? (
          <span className="chatBadge" aria-label={`${chat.unseenCount} new`}>{chat.unseenCount}</span>
        ) : null}
      </button>
    </aside>
  )
}
