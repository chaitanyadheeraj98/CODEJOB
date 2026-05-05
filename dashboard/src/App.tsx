import { useEffect, useState } from 'react'
import './App.css'

type RecruiterEmail = {
  id: number
  sender: string
  subject: string
  body: string
  role: string
  location: string
  salary_text: string
  skills_text: string
  score: number
  decision: string
  draft_reply: string
  approval_status: string
  sent_status: string
  source: string
  external_message_id: string | null
  external_thread_id: string | null
  recipient_email: string | null
  sent_at: string | null
  last_error: string | null
}

type GmailStatus = {
  configured: boolean
  authenticated: boolean
  token_path: string
  last_sync_at: string | null
  detail: string
}

function App() {
  const [emails, setEmails] = useState<RecruiterEmail[]>([])
  const [sender, setSender] = useState('Recruiter')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [loading, setLoading] = useState(false)
  const [apiError, setApiError] = useState('')
  const [draftEdits, setDraftEdits] = useState<Record<number, string>>({})
  const [gmailStatus, setGmailStatus] = useState<GmailStatus | null>(null)
  const [syncing, setSyncing] = useState(false)

  const apiBase = 'http://localhost:8000'

  const loadEmails = async () => {
    setApiError('')
    try {
      const res = await fetch(`${apiBase}/phase0/emails`)
      if (!res.ok) throw new Error('Failed to load emails')
      const data = (await res.json()) as RecruiterEmail[]
      setEmails(data)
      setDraftEdits(
        Object.fromEntries(data.map((item) => [item.id, item.draft_reply]))
      )
    } catch (err) {
      setApiError((err as Error).message)
    }
  }

  const loadGmailStatus = async () => {
    try {
      const res = await fetch(`${apiBase}/phase0/gmail/status`)
      if (!res.ok) throw new Error('Failed to load Gmail status')
      const data = (await res.json()) as GmailStatus
      setGmailStatus(data)
    } catch (err) {
      setApiError((err as Error).message)
    }
  }

  useEffect(() => {
    loadEmails()
    loadGmailStatus()
  }, [])

  const ingestEmail = async (event: React.FormEvent) => {
    event.preventDefault()
    setApiError('')
    setLoading(true)
    try {
      const res = await fetch(`${apiBase}/phase0/emails/ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sender, subject, body }),
      })
      if (!res.ok) throw new Error('Failed to ingest email')
      setSubject('')
      setBody('')
      await loadEmails()
    } catch (err) {
      setApiError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const syncGmail = async () => {
    setApiError('')
    setSyncing(true)
    try {
      const res = await fetch(`${apiBase}/phase0/gmail/sync`, { method: 'POST' })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Failed to sync Gmail')
      }
      await loadEmails()
      await loadGmailStatus()
    } catch (err) {
      setApiError((err as Error).message)
    } finally {
      setSyncing(false)
    }
  }

  const approveAndSend = async (emailId: number) => {
    setApiError('')
    try {
      const res = await fetch(`${apiBase}/phase0/emails/${emailId}/approve-send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edited_reply: draftEdits[emailId] ?? '' }),
      })
      if (!res.ok) throw new Error('Failed to approve/send')
      await loadEmails()
    } catch (err) {
      setApiError((err as Error).message)
    }
  }

  return (
    <main className="container">
      <h1>Phase 0 Manual Assist Console</h1>
      <p className="sub">Ingest, classify, draft, and manually approve/send recruiter emails.</p>

      <section className="card">
        <h2>Gmail</h2>
        <p>
          <strong>Configured:</strong> {gmailStatus?.configured ? 'Yes' : 'No'} |{' '}
          <strong>Authenticated:</strong> {gmailStatus?.authenticated ? 'Yes' : 'No'}
        </p>
        <p><strong>Status:</strong> {gmailStatus?.detail ?? 'Loading...'}</p>
        <p><strong>Token Path:</strong> {gmailStatus?.token_path ?? '-'}</p>
        <p><strong>Last Sync:</strong> {gmailStatus?.last_sync_at ?? 'Never'}</p>
        <button type="button" onClick={syncGmail} disabled={syncing}>
          {syncing ? 'Syncing...' : 'Sync Gmail'}
        </button>
      </section>

      <form className="card" onSubmit={ingestEmail}>
        <h2>Ingest Email</h2>
        <label>
          Sender
          <input value={sender} onChange={(e) => setSender(e.target.value)} required />
        </label>
        <label>
          Subject
          <input value={subject} onChange={(e) => setSubject(e.target.value)} required />
        </label>
        <label>
          Body
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={5} required />
        </label>
        <button type="submit" disabled={loading}>{loading ? 'Processing...' : 'Ingest + Classify'}</button>
      </form>

      {apiError ? <p className="error">{apiError}</p> : null}

      <section className="card">
        <h2>Review Queue</h2>
        {emails.length === 0 ? <p>No emails yet.</p> : null}
        {emails.map((email) => (
          <article key={email.id} className="emailItem">
            <h3>{email.subject}</h3>
            <p><strong>Sender:</strong> {email.sender}</p>
            <p><strong>Source:</strong> {email.source}</p>
            <p><strong>Gmail Msg ID:</strong> {email.external_message_id ?? '-'}</p>
            <p><strong>Gmail Thread ID:</strong> {email.external_thread_id ?? '-'}</p>
            <p><strong>Role:</strong> {email.role} | <strong>Location:</strong> {email.location}</p>
            <p><strong>Salary:</strong> {email.salary_text} | <strong>Skills:</strong> {email.skills_text}</p>
            <p><strong>Score:</strong> {email.score} | <strong>Decision:</strong> {email.decision}</p>
            <p><strong>Status:</strong> {email.approval_status} / {email.sent_status}</p>
            {email.last_error ? <p className="error"><strong>Last Error:</strong> {email.last_error}</p> : null}
            <label>
              Draft Reply
              <textarea
                value={draftEdits[email.id] ?? ''}
                onChange={(e) => setDraftEdits((prev) => ({ ...prev, [email.id]: e.target.value }))}
                rows={6}
              />
            </label>
            <button
              type="button"
              disabled={email.sent_status === 'sent'}
              onClick={() => approveAndSend(email.id)}
            >
              {email.sent_status === 'sent' ? 'Already Sent' : 'Approve + Send'}
            </button>
          </article>
        ))}
      </section>
    </main>
  )
}

export default App
