import { useCallback, useEffect, useState } from 'react'

import {
  TERMINAL_STATUSES,
  createRequirement,
  fetchJobStatus,
  previewRequirement,
} from './api'
import type { ManualDuplicate } from './api'

type Props = { apiBase: string }

const PLACEHOLDER = `Paste the requirement exactly as you received it, signature and all.

Job Title:  Senior Full Stack Developer
Experience: 8+ Years
Client: TECH M
Job Type: Contract -C2C
Rate/Salary: $58/Hr on C2C
Job Location:  Plano, TX (onsite)

Thanks & regards
Pat Recruiter
Acme Staffing
Email: pat@acmestaffing.com / Contact: +1 (555) 123-4567`

const MAX_CHARS = 20000
const POLL_MS = 1500
// Long enough that it does not fire mid-sentence, short enough that the warning
// is on screen before the user reaches for Create.
const PREVIEW_DEBOUNCE_MS = 600

function stamp(value: string): string {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleDateString()
}

// The third way a requirement gets in, beside the Gmail sweep and the Nvoids
// feed: one the user was sent somewhere the application cannot read. It ends at
// a card - Needs Review picks it up from there like any other.
export default function ManualIntakePage({ apiBase }: Props) {
  const [text, setText] = useState('')
  // The duplicate is stored with the text it was found for, so an edit hides a
  // stale warning without the effect having to clear state as it runs.
  const [preview, setPreview] = useState<{ text: string; duplicate: ManualDuplicate | null }>({
    text: '',
    duplicate: null,
  })
  const [runKey, setRunKey] = useState('')
  const [status, setStatus] = useState('')
  const [detail, setDetail] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const trimmed = text.trim()
  const tooLong = text.length > MAX_CHARS
  const running = Boolean(runKey) && !TERMINAL_STATUSES.has(status)
  const duplicate = preview.text === trimmed ? preview.duplicate : null

  // Debounced rather than per-keystroke: a paste is thousands of characters and
  // the check is a round trip.
  useEffect(() => {
    if (!trimmed || trimmed.length > MAX_CHARS) return
    let cancelled = false
    const timer = window.setTimeout(() => {
      previewRequirement(apiBase, trimmed)
        .then((result) => {
          if (!cancelled) setPreview({ text: trimmed, duplicate: result.duplicate_of })
        })
        // A failed duplicate check must never block creating the card: it is a
        // warning, and losing it is better than losing the requirement.
        .catch(() => {
          if (!cancelled) setPreview({ text: trimmed, duplicate: null })
        })
    }, PREVIEW_DEBOUNCE_MS)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [apiBase, trimmed])

  useEffect(() => {
    if (!runKey || TERMINAL_STATUSES.has(status)) return
    let cancelled = false
    const timer = window.setInterval(async () => {
      try {
        const job = await fetchJobStatus(apiBase, runKey)
        if (cancelled) return
        setStatus(job.status)
        setDetail(job.detail || '')
        if (TERMINAL_STATUSES.has(job.status)) setBusy(false)
      } catch (caught) {
        if (cancelled) return
        // Stop rather than spin: an unreadable run is a finished one as far as
        // this page is concerned.
        setError((caught as Error).message)
        setStatus('failed')
        setBusy(false)
      }
    }, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [apiBase, runKey, status])

  const submit = useCallback(async () => {
    if (!trimmed) return
    setBusy(true)
    setError('')
    setDetail('')
    setStatus('')
    try {
      const job = await createRequirement(apiBase, trimmed, duplicate?.id ?? null)
      setRunKey(job.run_key)
      setStatus(job.status)
    } catch (caught) {
      setError((caught as Error).message)
      setBusy(false)
    }
  }, [apiBase, duplicate, trimmed])

  const startAnother = useCallback(() => {
    setText('')
    setPreview({ text: '', duplicate: null })
    setRunKey('')
    setStatus('')
    setDetail('')
    setError('')
  }, [])

  const finished = Boolean(runKey) && TERMINAL_STATUSES.has(status)

  return (
    <section className="card manualIntakeCard">
      <h2>Manual Intake</h2>
      <p className="subtle">
        Paste a requirement you were sent anywhere the app cannot read it - WhatsApp, a
        forwarded message, a screenshot you retyped. It is screened, matched to a resume
        and drafted exactly like one that arrived by Gmail, and lands in Needs Review.
      </p>

      <label className="manualIntakeField">
        Requirement text
        <textarea
          className="manualIntakeTextarea"
          value={text}
          placeholder={PLACEHOLDER}
          aria-label="Pasted requirement"
          disabled={running}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <p className={tooLong ? 'errorMessage' : 'subtle'}>
        {text.length.toLocaleString()} / {MAX_CHARS.toLocaleString()} characters
        {tooLong ? ' - too long to accept' : ''}
      </p>

      {duplicate ? (
        <div className="manualIntakeDuplicate">
          <strong>You have already pasted this requirement.</strong>
          <span className="subtle">
            {[duplicate.role, duplicate.client].filter(Boolean).join(' - ') || 'Existing card'}
            {stamp(duplicate.created_at) ? `, added ${stamp(duplicate.created_at)}` : ''}
            {` (email ${duplicate.id})`}
          </span>
          <span className="subtle">
            Create it anyway if the recruiter re-sent it or the details changed - this is a
            warning, not a block.
          </span>
        </div>
      ) : null}

      <div className="pillRow">
        <button type="button" onClick={submit} disabled={busy || running || !trimmed || tooLong}>
          {running ? 'Working...' : 'Create Card'}
        </button>
        {finished ? (
          <button type="button" onClick={startAnother}>
            Paste Another
          </button>
        ) : null}
      </div>

      {runKey ? (
        <p className={status === 'failed' ? 'errorMessage' : 'subtle'}>
          {running ? 'Reading the requirement, screening it and writing a draft...' : detail || status}
        </p>
      ) : null}
      {error ? <p className="errorMessage">{error}</p> : null}
    </section>
  )
}
