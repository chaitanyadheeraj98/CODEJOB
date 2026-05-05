import { useEffect, useRef, useState } from 'react'
import './App.css'
import Sidebar from './components/Sidebar'

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
  mail_date: string | null
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

type ResumeAsset = {
  id: number
  file_name: string
  mime_type: string
  sha256: string
  version: number
  is_current: boolean
  created_at: string
  updated_at: string
}

type Candidate = {
  id: number
  subject: string
  sender: string
  body: string
  gmail_message_url: string | null
  recipient_email: string | null
  cc_email: string | null
  routing_status: string
  routing_confidence: number
  routing_reason: string
  routing_evidence: RoutingEvidence[]
  routing_candidates: RoutingEvidence[]
  routing_confirmed: boolean
  draft_reply: string
  resume_file_name: string | null
  state: string
  last_error: string | null
}

type RoutingEvidence = {
  role: string
  email: string
  source: string
  detail: string
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
    mail_date: null,
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
  const [activeResume, setActiveResume] = useState<ResumeAsset | null>(null)
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
  const [activePage, setActivePage] = useState<'run_queue' | 'needs_review' | 'failed_mapping' | 'recent_runs'>('run_queue')
  const datePickerRef = useRef<HTMLInputElement | null>(null)

  const candidatesUrl = (state: 'needs_review' | 'failed') => {
    const params = new URLSearchParams({
      state,
      limit: '20',
      sort: 'newest',
    })
    if (settings.mail_date) params.set('mail_date', settings.mail_date)
    return `${apiBase}/candidates?${params.toString()}`
  }

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
    const res = await fetch(candidatesUrl('needs_review'))
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

  const loadActiveResume = async () => {
    const res = await fetch(`${apiBase}/settings/resumes`)
    if (!res.ok) throw new Error('Failed to load resumes')
    const items = (await res.json()) as ResumeAsset[]
    const current = items.find((item) => item.is_current) ?? null
    setActiveResume(current)
  }

  const loadFailedQueue = async () => {
    const res = await fetch(candidatesUrl('failed'))
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
    loadActiveResume().catch((e) => setError((e as Error).message))
    loadQueue().catch((e) => setError((e as Error).message))
    loadFailedQueue().catch((e) => setError((e as Error).message))
  }, [])

  useEffect(() => {
    loadQueue().catch((e) => setError((e as Error).message))
    loadFailedQueue().catch((e) => setError((e as Error).message))
  }, [settings.mail_date])

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
      await loadActiveResume()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const runAutomation = async () => {
    setRunning(true)
    setError('')
    try {
      const res = await fetch(`${apiBase}/automation/run-once`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mail_date: settings.mail_date || null }),
      })
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

  const gmailConnected = Boolean(status?.configured && status?.authenticated)
  const totalActionItems = queue.length + failedQueue.length
  const canTrustRouting = (candidate: Candidate) =>
    candidate.routing_confirmed ||
    (['safe', 'confirmed'].includes(candidate.routing_status) && candidate.routing_confidence >= 0.8)

  const sourceLabel = (source: string) =>
    source
      .split('_')
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(' ')

  const openDatePicker = () => {
    const picker = datePickerRef.current
    if (!picker) return
    if (typeof picker.showPicker === 'function') {
      picker.showPicker()
      return
    }
    picker.focus()
    picker.click()
  }

  const formattedMailDate = settings.mail_date
    ? new Date(`${settings.mail_date}T00:00:00`).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      })
    : ''

  const renderRoutingPanel = (item: Candidate) => (
    <div className={`routingPanel ${canTrustRouting(item) ? 'safe' : 'blocked'}`}>
      <div className="routingPanelHeader">
        <strong>Routing: {item.routing_status || 'unverified'}</strong>
        <span>{Math.round((item.routing_confidence ?? 0) * 100)}% confidence</span>
      </div>
      <p>{item.routing_reason || 'No routing evidence captured yet.'}</p>
      {item.routing_evidence?.length ? (
        <div className="evidenceGrid">
          {item.routing_evidence.map((evidence, index) => (
            <div key={`${item.id}-evidence-${index}`} className="evidenceItem">
              <small>{evidence.role.toUpperCase()} from {sourceLabel(evidence.source)}</small>
              <span>{evidence.email}</span>
            </div>
          ))}
        </div>
      ) : null}
      {!canTrustRouting(item) ? (
        <p className="routingWarning">Approval is blocked until routing is safe or manually confirmed.</p>
      ) : null}
    </div>
  )

  const renderCandidateEmails = (item: Candidate) => {
    if (!item.routing_candidates?.length) return null
    return (
      <div className="candidateEmailList">
        <strong>Extracted email candidates</strong>
        {item.routing_candidates.map((candidate, index) => (
          <p key={`${item.id}-candidate-${index}`}>
            <span>{candidate.role.toUpperCase()}</span> {candidate.email} <small>({sourceLabel(candidate.source)})</small>
          </p>
        ))}
      </div>
    )
  }

  return (
    <main className="gmailShell">
      <Sidebar
        running={running}
        queueCount={queue.length}
        failedCount={failedQueue.length}
        runCount={logs.length}
        activePage={activePage}
        onNavigate={setActivePage}
      />

      <section className="mainPane">
        <header className="pageHeader">
          <div>
            <p className="eyebrow">Email Automation Dashboard</p>
            <h1>Review, route, and ship candidate replies</h1>
            <p className="subtle">
              Keep the pipeline moving with one clear place for sync, approvals, and failed mapping recovery.
            </p>
          </div>
          <div className="statusPills">
            <span className={`pill ${gmailConnected ? 'ok' : 'warn'}`}>
              Gmail {gmailConnected ? 'Connected' : 'Needs attention'}
            </span>
            <span className="pill neutral">Open items {totalActionItems}</span>
          </div>
        </header>

        <section className="statsGrid">
          <article className="statCard">
            <p>Needs Review</p>
            <strong>{queue.length}</strong>
          </article>
          <article className="statCard">
            <p>Failed Mapping</p>
            <strong>{failedQueue.length}</strong>
          </article>
          <article className="statCard">
            <p>Recent Runs</p>
            <strong>{logs.length}</strong>
          </article>
        </section>

        <header className="topBar">
          <input
            className="search"
            value={settings.gmail_query}
            onChange={(e) => setSettings({ ...settings, gmail_query: e.target.value })}
            placeholder="Search/filter query"
          />
          <div className="topBarRight">
            <span className="dateTrigger">
              <button type="button" className="iconBtn" onClick={openDatePicker} title="Filter by date">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M7 2a1 1 0 0 1 1 1v1h8V3a1 1 0 1 1 2 0v1h1a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h1V3a1 1 0 0 1 1-1Zm12 8H5v9h14v-9ZM6 6v2h12V6H6Z" />
                </svg>
              </button>
              <input
                ref={datePickerRef}
                className="datePickerNative"
                type="date"
                value={settings.mail_date ?? ''}
                onChange={(e) => setSettings({ ...settings, mail_date: e.target.value || null })}
                aria-label="Mail date filter"
              />
            </span>
            {settings.mail_date ? (
              <button
                type="button"
                className="dateChip"
                onClick={() => setSettings({ ...settings, mail_date: null })}
                title="Clear date filter"
              >
                {formattedMailDate} x
              </button>
            ) : null}
            <button type="button" className="topBarAction" onClick={runAutomation} disabled={running}>
              {running ? 'Running...' : 'Sync + Queue'}
            </button>
          </div>
        </header>

        {activePage === 'run_queue' ? (
          <>
            <section className="card slim">
              <h2>Gmail Access</h2>
              <p><strong>Configured:</strong> {status?.configured ? 'Yes' : 'No'}</p>
              <p><strong>Authenticated:</strong> {status?.authenticated ? 'Yes' : 'No'}</p>
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
              {activeResume ? (
                <p className="subtle">
                  Active resume: <strong>{activeResume.file_name}</strong> (v{activeResume.version})
                </p>
              ) : (
                <p className="subtle">No active resume uploaded yet.</p>
              )}
              <input type="file" accept=".pdf,.doc,.docx" onChange={(e) => setResumeFile(e.target.files?.[0] ?? null)} />
              <button type="button" onClick={uploadResume} disabled={!resumeFile}>
                {activeResume ? 'Replace Resume' : 'Upload Resume'}
              </button>
            </section>
          </>
        ) : null}

        {error ? <p className="error">{error}</p> : null}

        {activePage === 'needs_review' ? (
          <section className="card">
          <h2>Needs Review (Manual Approval Required)</h2>
          {queue.length === 0 ? <p className="subtle">No queued emails.</p> : null}
          {queue.map((item) => {
            const editedDraft = draftEdits[item.id] ?? item.draft_reply
            const canApprove =
              Boolean(item.recipient_email) &&
              Boolean(item.cc_email) &&
              Boolean(editedDraft?.trim()) &&
              Boolean(item.resume_file_name) &&
              canTrustRouting(item)
            return (
              <article key={item.id} className="emailItem">
                <p><strong>Email ID:</strong> {item.id}</p>
                <p><strong>From:</strong> {item.sender}</p>
                <p><strong>Subject:</strong> {item.subject}</p>
                {item.gmail_message_url ? (
                  <p>
                    <strong>Open:</strong>{' '}
                    <a href={item.gmail_message_url} target="_blank" rel="noreferrer">
                      Open exact email in Gmail
                    </a>
                  </p>
                ) : null}
                <p><strong>To:</strong> {item.recipient_email ?? '-'}</p>
                <p><strong>CC:</strong> {item.cc_email ?? '-'}</p>
                {renderRoutingPanel(item)}
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
                    title={!canApprove ? 'Safe routing, To, CC, body, and resume are required before send' : 'Approve and send'}
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
        ) : null}

        {activePage === 'failed_mapping' ? (
          <section className="card">
          <h2>Failed Recipient Mapping (Teach the model)</h2>
          {failedQueue.length === 0 ? <p className="subtle">No failed emails.</p> : null}
          {failedQueue.map((item) => {
            const fix = routingFixes[item.id] ?? { to: '', cc: '' }
            return (
              <article key={`failed-${item.id}`} className="emailItem">
                <p><strong>Email ID:</strong> {item.id}</p>
                <p><strong>From:</strong> {item.sender}</p>
                <p><strong>Subject:</strong> {item.subject}</p>
                {item.gmail_message_url ? (
                  <p>
                    <strong>Open:</strong>{' '}
                    <a href={item.gmail_message_url} target="_blank" rel="noreferrer">
                      Open exact email in Gmail
                    </a>
                  </p>
                ) : null}
                <p><strong>Reason:</strong> {item.last_error ?? item.state}</p>
                {renderRoutingPanel(item)}
                {renderCandidateEmails(item)}
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
        ) : null}

        {activePage === 'recent_runs' ? (
          <section className="card">
          <h2>Recent Runs</h2>
          {logs.length === 0 ? <p className="subtle">No runs yet.</p> : null}
          {logs.map((item, index) => (
            <article key={`${item.email_id ?? 'none'}-${index}`} className="emailItem">
              <p><strong>Status:</strong> {item.status}</p>
              <p><strong>Detail:</strong> {item.detail}</p>
              <p><strong>Email ID:</strong> {item.email_id ?? '-'}</p>
            </article>
          ))}
          </section>
        ) : null}
      </section>
    </main>
  )
}

export default App
