import { useEffect, useRef, useState, type FormEvent } from 'react'

import { useChat } from './chatContext'
import { renderMarkdownLite } from './markdown'
import ProposalCard from './ProposalCard'
import { proposalForMessage } from './proposals'
import CandidateTable from './CandidateTable'
import { renderForMessage } from './renderers'
import type { ChatSession } from './types'


const STARTER_PROMPTS = [
  'Summarise today’s Needs Review queue',
  'Which recruiters replied this week?',
  'Which resume variant matches this job description best?',
  'How many candidates am I waiting on?',
]

type SessionGroup = { label: string; sessions: ChatSession[] }

// Grouped by day rather than listed flat: the conversation list is the only
// place a past session can be found again, and "Java roles" three weeks ago
// reads very differently from "Java roles" this morning.
function groupSessions(sessions: ChatSession[]): SessionGroup[] {
  const startOfToday = new Date()
  startOfToday.setHours(0, 0, 0, 0)
  const startOfYesterday = new Date(startOfToday)
  startOfYesterday.setDate(startOfYesterday.getDate() - 1)

  const groups: SessionGroup[] = [
    { label: 'Today', sessions: [] },
    { label: 'Yesterday', sessions: [] },
    { label: 'Earlier', sessions: [] },
  ]
  for (const session of sessions) {
    const updated = new Date(session.updated_at)
    // An unparseable timestamp lands in Earlier rather than vanishing - a
    // session the user can't reach is worse than one filed under the wrong day.
    const bucket = Number.isNaN(updated.getTime()) || updated < startOfYesterday
      ? 2
      : updated < startOfToday ? 1 : 0
    groups[bucket].sessions.push(session)
  }
  return groups.filter((group) => group.sessions.length > 0)
}

function sessionLabel(session: ChatSession): string {
  const title = (session.title ?? '').trim()
  if (!title) return `Chat ${session.id}`
  return title.length > 48 ? `${title.slice(0, 47)}…` : title
}

export default function AssistantPage() {
  const chat = useChat()
  const { ready, status, statusError } = chat
  const [draft, setDraft] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [renameDraft, setRenameDraft] = useState('')
  const messagesEndRef = useRef<HTMLDivElement | null>(null)

  const currentSession = chat.sessions.find((session) => session.id === chat.sessionId)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: 'nearest' })
  }, [chat.messages])

  // Clears on mount and again whenever new messages land while the page is
  // open, because on this surface the user is by definition looking at them.
  const { markSeen } = chat
  const messageCount = chat.messages.length
  useEffect(() => {
    markSeen()
  }, [markSeen, messageCount])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const text = draft.trim()
    if (!text || chat.busy) return
    setDraft('')
    await chat.sendMessage(text, chat.selectedModel)
  }

  const commitRename = async () => {
    const next = renameDraft.trim()
    setRenaming(false)
    if (!next || next === currentSession?.title) return
    await chat.renameCurrentSession(next)
  }

  const removeCurrent = async () => {
    if (!currentSession) return
    if (!window.confirm(`Delete "${sessionLabel(currentSession)}"? This cannot be undone.`)) return
    await chat.removeCurrentSession()
  }

  if (!status && !statusError) {
    return <section className="card pageSection"><p className="chatState">Checking Ollama connection…</p></section>
  }

  // The page stays reachable when chat is off and explains itself, rather than
  // disappearing from the sidebar and leaving the entry looking broken.
  if (!ready) {
    return (
      <section className="card pageSection">
        <div className="chatState">
          {statusError ? (
            <>
              <strong>Chat status unavailable</strong>
              <p>{statusError}</p>
            </>
          ) : status && !status.enabled ? (
            <>
              <strong>Chat is disabled</strong>
              <p>Set <code>FEATURE_CHAT_ENABLED=true</code> and restart the backend.</p>
            </>
          ) : (
            <>
              <strong>Ollama is not connected</strong>
              <p>Start Ollama and make sure {status?.model} is available.</p>
            </>
          )}
          <button type="button" onClick={() => void chat.refreshStatus()}>Check again</button>
        </div>
      </section>
    )
  }

  return (
    <section className="assistantWorkspace pageSection" aria-label="CodeJob Assistant workspace">
      <aside className="assistantSessions">
        <button
          type="button"
          className="assistantNewChat"
          onClick={() => void chat.startSession()}
          disabled={chat.busy}
        >
          + New chat
        </button>
        <nav className="assistantSessionList" aria-label="Conversations">
          {!chat.sessions.length ? <p className="subtle">No conversations yet.</p> : null}
          {groupSessions(chat.sessions).map((group) => (
            <div key={group.label} className="assistantSessionGroup">
              <h3>{group.label}</h3>
              {group.sessions.map((session) => (
                <button
                  key={session.id}
                  type="button"
                  className={`assistantSessionItem ${session.id === chat.sessionId ? 'active' : ''}`}
                  onClick={() => void chat.selectSession(session.id)}
                  disabled={chat.busy}
                  aria-current={session.id === chat.sessionId}
                >
                  {sessionLabel(session)}
                </button>
              ))}
            </div>
          ))}
        </nav>
      </aside>

      <div className="assistantThread">
        <header className="assistantThreadHeader">
          {renaming ? (
            <input
              className="assistantTitleInput"
              aria-label="Conversation title"
              value={renameDraft}
              autoFocus
              onChange={(event) => setRenameDraft(event.target.value)}
              onBlur={() => void commitRename()}
              onKeyDown={(event) => {
                if (event.key === 'Enter') { event.preventDefault(); void commitRename() }
                if (event.key === 'Escape') setRenaming(false)
              }}
            />
          ) : (
            <button
              type="button"
              className="assistantTitle"
              onClick={() => { setRenameDraft(currentSession?.title ?? ''); setRenaming(true) }}
              disabled={chat.sessionId == null}
              title="Rename this conversation"
            >
              {currentSession ? sessionLabel(currentSession) : 'New conversation'}
            </button>
          )}
          <div className="assistantThreadActions">
            <label className="chatModelPicker">
              <span>Model</span>
              <select
                aria-label="Chat model"
                value={chat.selectedModel}
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
            <button
              type="button"
              onClick={() => void removeCurrent()}
              disabled={chat.busy || chat.sessionId == null}
            >
              Delete
            </button>
          </div>
        </header>

        <div className="chatMessages assistantMessages" aria-live="polite">
          {!chat.messages.length ? (
            <div className="assistantWelcome">
              <strong>What do you want to get done?</strong>
              <p>Ask a question, or start with one of these.</p>
              <div className="assistantStarters">
                {STARTER_PROMPTS.map((prompt) => (
                  <button key={prompt} type="button" onClick={() => setDraft(prompt)}>{prompt}</button>
                ))}
              </div>
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
            const rendered = renderForMessage(message)
            if (rendered) return <CandidateTable key={message.id} messageId={message.id} data={rendered.data} />
            if (message.role === 'tool') return null
            return (
              <div key={message.id} className={`chatBubble ${message.role}`}>
                {message.content
                  ? renderMarkdownLite(message.content)
                  : chat.busy && message.role === 'assistant' ? 'Thinking…' : ''}
              </div>
            )
          })}
          <div ref={messagesEndRef} />
        </div>

        {chat.error ? <p className="chatError" role="alert">{chat.error}</p> : null}

        <form className="chatComposer assistantComposer" onSubmit={(event) => void submit(event)}>
          <label className="visuallyHidden" htmlFor="assistant-message">Message CodeJob Assistant</label>
          <textarea
            id="assistant-message"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={4000}
            rows={3}
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
      </div>
    </section>
  )
}
