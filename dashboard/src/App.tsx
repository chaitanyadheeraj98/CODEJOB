import { useEffect, useState } from 'react'
import './App.css'

type GmailStatus = {
  configured: boolean
  authenticated: boolean
  token_path: string
  last_sync_at: string | null
  detail: string
}

type SettingsPayload = {
  enabled: boolean
  gmail_query: string
  min_salary: number | null
  accepted_locations: string[]
  visa_required_allowed: boolean
  remote_preference: string
  role_keywords: string[]
  must_have_skills: string[]
  free_text_guidance: string
  qualification_threshold: number
  feature_auto_polling: boolean
  feature_auto_send: boolean
  feature_retry_queue: boolean
}

type AutomationRunResponse = {
  status: string
  detail: string
  email_id: number | null
}

type Candidate = {
  id: number
  subject: string
  sender: string
  body: string
  recipient_email: string | null
  cc_email: string | null
  draft_reply: string
  resume_file_name: string | null
  state: string
  last_error: string | null
}

type CandidateListResponse = {
  items: Candidate[]
  next_cursor: number | null
  has_next: boolean
}

function App() {
  const apiBase = 'http://localhost:8000'
  const [status, setStatus] = useState<GmailStatus | null>(null)
  const [settings, setSettings] = useState<SettingsPayload>({
    enabled: true,
    gmail_query: 'tx',
    min_salary: null,
    accepted_locations: [],
    visa_required_allowed: false,
    remote_preference: 'any',
    role_keywords: [],
    must_have_skills: [],
    free_text_guidance: '',
    qualification_threshold: 0.6,
    feature_auto_polling: false,
    feature_auto_send: false,
    feature_retry_queue: false,
  })
  const [resumeFile, setResumeFile] = useState<File | null>(null)
  const [running, setRunning] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [logs, setLogs] = useState<AutomationRunResponse[]>([])
  const [queue, setQueue] = useState<Candidate[]>([])
  const [sendingId, setSendingId] = useState<number | null>(null)
  const [rejectingId, setRejectingId] = useState<number | null>(null)
  const [draftEdits, setDraftEdits] = useState<Record<number, string>>({})
  const [failedQueue, setFailedQueue] = useState<Candidate[]>([])
  const [routingFixes, setRoutingFixes] = useState<Record<number, { to: string; cc: string }>>({})
  const [fixingId, setFixingId] = useState<number | null>(null)

  const loadStatus = async () => {
    const res = await fetch(`${apiBase}/gmail/status`)
    if (!res.ok) throw new Error('Failed to load Gmail status')
    setStatus((await res.json()) as GmailStatus)
  }

  const loadSettings = async () => {
    const res = await fetch(`${apiBase}/settings`)
    if (!res.ok) throw new Error('Failed to load settings')
    setSettings((await res.json()) as SettingsPayload)
  }

  const loadQueue = async () => {
    const res = await fetch(`${apiBase}/candidates?state=needs_review&limit=20&sort=newest`)
    if (!res.ok) throw new Error('Failed to load review queue')
    const data = (await res.json()) as CandidateListResponse
    setQueue(data.items)
    setDraftEdits((prev) => {
      const next = { ...prev }
      for (const c of data.items) {
        if (!(c.id in next)) next[c.id] = c.draft_reply ?? ''
      }
      return next
    })
  }

  const loadFailedQueue = async () => {
    const res = await fetch(`${apiBase}/candidates?state=failed&limit=20&sort=newest`)
    if (!res.ok) throw new Error('Failed to load failed queue')
    const data = (await res.json()) as CandidateListResponse
    setFailedQueue(data.items)
    setRoutingFixes((prev) => {
      const next = { ...prev }
      for (const c of data.items) {
        if (!(c.id in next)) {
          next[c.id] = { to: c.recipient_email ?? '', cc: c.cc_email ?? '' }
        }
      }
      return next
    })
  }

  useEffect(() => {
    loadStatus().catch((e) => setError((e as Error).message))
    loadSettings().catch((e) => setError((e as Error).message))
    loadQueue().catch((e) => setError((e as Error).message))
    loadFailedQueue().catch((e) => setError((e as Error).message))
  }, [])

  const saveSettings = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      })
      if (!res.ok) throw new Error('Failed to save settings')
      await loadSettings()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const uploadResume = async () => {
    if (!resumeFile) return
    setError('')
    const fd = new FormData()
    fd.append('file', resumeFile)
    try {
      const res = await fetch(`${apiBase}/settings/resume`, { method: 'POST', body: fd })
      if (!res.ok) throw new Error('Failed to upload resume')
      setResumeFile(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const runAutomation = async () => {
    setRunning(true)
    setError('')
    try {
      const res = await fetch(`${apiBase}/automation/run-once`, { method: 'POST' })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Automation run failed')
      }
      const data = (await res.json()) as AutomationRunResponse
      setLogs((prev) => [data, ...prev].slice(0, 20))
      await loadStatus()
      await loadQueue()
      await loadFailedQueue()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRunning(false)
    }
  }

  const approveSend = async (candidate: Candidate) => {
    setSendingId(candidate.id)
    setError('')
    try {
      const res = await fetch(`${apiBase}/candidates/${candidate.id}/approve-send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edited_reply: draftEdits[candidate.id] ?? candidate.draft_reply }),
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Approve & send failed')
      }
      await loadQueue()
      await loadFailedQueue()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSendingId(null)
    }
  }

  const rejectSend = async (candidateId: number) => {
    setRejectingId(candidateId)
    setError('')
    try {
      const res = await fetch(`${apiBase}/candidates/${candidateId}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Rejected by user before send' }),
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Reject failed')
      }
      await loadQueue()
      await loadFailedQueue()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRejectingId(null)
    }
  }

  const saveRoutingAndRequeue = async (candidateId: number) => {
    const fix = routingFixes[candidateId]
    if (!fix?.to || !fix?.cc) return
    setFixingId(candidateId)
    setError('')
    try {
      const res = await fetch(`${apiBase}/candidates/${candidateId}/resolve-recipients`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to_email: fix.to, cc_email: fix.cc }),
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Failed to save recipient mapping')
      }
      await loadQueue()
      await loadFailedQueue()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setFixingId(null)
    }
  }

  return (
    <main className="gmailShell">
      <aside className="leftRail">
        <div className="brand">CodeJob MailOps</div>
        <button className="composeBtn" type="button" onClick={runAutomation} disabled={running}>
          {running ? 'Running...' : 'Run Queue (1)'}
        </button>
        <nav className="navList">
          <div className="navItem active">Needs Review <span>{queue.length}</span></div>
          <div className="navItem">Failed Mapping <span>{failedQueue.length}</span></div>
          <div className="navItem">Recent Runs <span>{logs.length}</span></div>
        </nav>
      </aside>

      <section className="mainPane">
        <header className="topBar">
          <input
            className="search"
            value={settings.gmail_query}
            onChange={(e) => setSettings({ ...settings, gmail_query: e.target.value })}
            placeholder="Search/filter query"
          />
          <button type="button" onClick={runAutomation} disabled={running}>
            {running ? 'Running...' : 'Sync + Queue'}
          </button>
        </header>

        <section className="card slim">
          <h2>Gmail Access</h2>
          <p><strong>Configured:</strong> {status?.configured ? 'Yes' : 'No'} | <strong>Authenticated:</strong> {status?.authenticated ? 'Yes' : 'No'}</p>
          <p><strong>Status:</strong> {status?.detail ?? 'Loading...'}</p>
          <p><strong>Last Sync:</strong> {status?.last_sync_at ?? 'Never'}</p>
        </section>

        <form className="card slim" onSubmit={saveSettings}>
          <h2>Automation Filters</h2>
          <label>
            Qualification Threshold
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={settings.qualification_threshold}
              onChange={(e) => setSettings({ ...settings, qualification_threshold: Number(e.target.value) })}
            />
          </label>
          <label>
            Must-have Skills (comma-separated)
            <input
              value={settings.must_have_skills.join(',')}
              onChange={(e) => setSettings({ ...settings, must_have_skills: e.target.value.split(',').map((v) => v.trim()).filter(Boolean) })}
            />
          </label>
          <div className="rowBtns">
            <button type="submit" disabled={saving}>{saving ? 'Saving...' : 'Save Filters'}</button>
          </div>
        </form>

        <section className="card slim">
          <h2>Resume</h2>
          <input type="file" accept=".pdf,.doc,.docx" onChange={(e) => setResumeFile(e.target.files?.[0] ?? null)} />
          <button type="button" onClick={uploadResume} disabled={!resumeFile}>Upload Resume</button>
        </section>

        {error ? <p className="error">{error}</p> : null}

        <section className="card">
          <h2>Needs Review (Manual Approval Required)</h2>
          {queue.length === 0 ? <p>No queued emails.</p> : null}
          {queue.map((item) => {
            const editedDraft = draftEdits[item.id] ?? item.draft_reply
            const canApprove =
              Boolean(item.recipient_email) &&
              Boolean(item.cc_email) &&
              Boolean(editedDraft?.trim()) &&
              Boolean(item.resume_file_name)
            return (
              <article key={item.id} className="emailItem">
                <p><strong>Email ID:</strong> {item.id}</p>
                <p><strong>From:</strong> {item.sender}</p>
                <p><strong>Subject:</strong> {item.subject}</p>
                <p><strong>To:</strong> {item.recipient_email ?? '-'}</p>
                <p><strong>CC:</strong> {item.cc_email ?? '-'}</p>
                <p><strong>Resume:</strong> {item.resume_file_name ?? '-'}</p>
                <p><strong>Draft:</strong></p>
                <textarea
                  value={editedDraft}
                  rows={8}
                  onChange={(e) => setDraftEdits((prev) => ({ ...prev, [item.id]: e.target.value }))}
                />
                {item.last_error ? <p className="error"><strong>Last Error:</strong> {item.last_error}</p> : null}
                <div className="rowBtns">
                  <button
                    type="button"
                    onClick={() => approveSend(item)}
                    disabled={!canApprove || sendingId === item.id}
                    title={!canApprove ? 'To, CC, body, and resume are required before send' : 'Approve and send'}
                  >
                    {sendingId === item.id ? 'Sending...' : 'Approve & Send'}
                  </button>
                  <button
                    type="button"
                    onClick={() => rejectSend(item.id)}
                    disabled={rejectingId === item.id}
                  >
                    {rejectingId === item.id ? 'Rejecting...' : 'Reject'}
                  </button>
                </div>
              </article>
            )
          })}
        </section>

        <section className="card">
          <h2>Failed Recipient Mapping (Teach the model)</h2>
          {failedQueue.length === 0 ? <p>No failed emails.</p> : null}
          {failedQueue.map((item) => {
            const fix = routingFixes[item.id] ?? { to: '', cc: '' }
            return (
              <article key={`failed-${item.id}`} className="emailItem">
                <p><strong>Email ID:</strong> {item.id}</p>
                <p><strong>From:</strong> {item.sender}</p>
                <p><strong>Subject:</strong> {item.subject}</p>
                <p><strong>Reason:</strong> {item.last_error ?? item.state}</p>
                <label>
                  Full Email Content (for recipient mapping)
                  <textarea value={item.body ?? ''} readOnly rows={8} />
                </label>
                <label>
                  Correct To
                  <input
                    value={fix.to}
                    onChange={(e) =>
                      setRoutingFixes((prev) => ({ ...prev, [item.id]: { ...fix, to: e.target.value } }))
                    }
                  />
                </label>
                <label>
                  Correct CC
                  <input
                    value={fix.cc}
                    onChange={(e) =>
                      setRoutingFixes((prev) => ({ ...prev, [item.id]: { ...fix, cc: e.target.value } }))
                    }
                  />
                </label>
                <button
                  type="button"
                  onClick={() => saveRoutingAndRequeue(item.id)}
                  disabled={fixingId === item.id || !fix.to || !fix.cc}
                >
                  {fixingId === item.id ? 'Saving...' : 'Save Mapping & Move to Review'}
                </button>
              </article>
            )
          })}
        </section>

        <section className="card">
          <h2>Recent Runs</h2>
          {logs.length === 0 ? <p>No runs yet.</p> : null}
          {logs.map((item, index) => (
            <article key={`${item.email_id ?? 'none'}-${index}`} className="emailItem">
              <p><strong>Status:</strong> {item.status}</p>
              <p><strong>Detail:</strong> {item.detail}</p>
              <p><strong>Email ID:</strong> {item.email_id ?? '-'}</p>
            </article>
          ))}
        </section>
      </section>
    </main>
  )
}

export default App
