import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import Sidebar from './components/Sidebar'
import { withAiToggle } from './features/ai/state'
import { getDraftSourceLabel } from './features/ai/ui'
import { refreshCandidateBuckets } from './candidateBuckets'

function escapeHtml(text: string): string {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function renderInline(text: string): string {
  const escaped = escapeHtml(text)
  return escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}

function draftToPreviewHtml(draftText: string): string {
  const normalized = (draftText ?? '').replaceAll('\r\n', '\n').trim()
  if (!normalized) return '<p></p>'
  const blocks = normalized.split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean)
  return blocks
    .map((block) => {
      const lines = block.split('\n').map((line) => line.trimEnd())
      const allBullets = lines.length > 0 && lines.every((line) => line.trimStart().startsWith('- '))
      if (allBullets) {
        const items = lines
          .map((line) => line.trimStart().slice(2).trim())
          .map((line) => `<li>${renderInline(line)}</li>`)
          .join('')
        return `<ul>${items}</ul>`
      }
      const paragraph = lines
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => renderInline(line))
        .join('<br>')
      return `<p>${paragraph}</p>`
    })
    .join('')
}

type GmailStatus = {
  configured: boolean
  authenticated: boolean
  token_path: string
  last_sync_at: string | null
  detail: string
}

type AiStatus = {
  configured: boolean
  connected: boolean
  running: boolean
  provider: string
  model: string
  detail: string
  last_error: string | null
  last_started_at: string | null
  last_finished_at: string | null
  last_duration_ms: number | null
  last_draft_source: string | null
}

type TelegramStatus = {
  enabled: boolean
  polling: boolean
  alerts_enabled: boolean
  authorized_chats: number
  detail: string
}

type SettingsPayload = {
  enabled: boolean
  gmail_query: string
  default_gmail_query: string
  mail_date: string | null
  default_date_mode: 'today' | 'off'
  min_salary: number | null
  accepted_locations: string[]
  visa_required_allowed: boolean
  remote_preference: string
  role_keywords: string[]
  must_have_skills: string[]
  free_text_guidance: string
  qualification_threshold: number
  feature_auto_polling: boolean
  feature_auto_poll_interval_minutes: number
  feature_auto_send: boolean
  feature_retry_queue: boolean
  feature_ai_enabled: boolean
  fallback_draft_template: string
  signature_name: string
  signature_phone: string
  signature_email: string
  policy?: DynamicPolicy | null
  policy_profile_options?: string[] | null
  policy_profile_selected?: string | null
}

type DynamicPolicy = {
  version: number
  query: {
    force_unread: boolean
    include_labels: string[]
    exclude_labels: string[]
    date_mode: 'custom' | 'any'
  }
  run: {
    run_mode: 'all'
    batch_limit: number
    dry_run: boolean
  }
  qualification: {
    location_strictness: 'lenient' | 'balanced' | 'strict'
    score_threshold_override_enabled: boolean
    score_threshold_override_value: number
  }
}

type PolicyProfileName = 'Aggressive' | 'Balanced' | 'Strict'

type AutomationRunResponse = {
  status: string
  detail: string
  email_id: number | null
  gmail_message_url?: string | null
  decision_reason?: string | null
  skip_reason?: string | null
  routing_reason?: string | null
  effective_query?: string | null
  matched_count?: number | null
  queued_count?: number | null
  skipped_count?: number | null
  failed_count?: number | null
}

type OAuthStartResponse = {
  status: string
  detail: string
  configured: boolean
  authenticated: boolean
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
  sent_at?: string | null
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
  draft_source: string | null
  draft_model: string | null
  draft_ai_error: string | null
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

type TimeRangeKey = 'last_1h' | 'current_day' | 'current_month' | 'current_year' | 'last_5y'

type ProductivityEvent = {
  id: number
  owner_id: string
  event_type: string
  event_source: string
  entity_id: number | null
  weight: number
  metadata: Record<string, unknown>
  occurred_at: string
  created_at: string
}

type ProductivityBarPoint = {
  ts: string
  sent_count: number
  failed_count: number
  needs_review_count: number
  recent_run_count: number
}

type ProductivityTrendResponse = {
  range: TimeRangeKey
  bucket: string
  trend_direction: 'up' | 'down' | 'flat'
  trend_delta_pct: number
  kpi_total_sent: number
  previous_period_total_sent: number
  bars: ProductivityBarPoint[]
}

function App() {
  const QUEUE_LIMIT = 100
  const RECENT_RUNS_LIMIT = 100
  const apiBase = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
  const defaultPolicy: DynamicPolicy = {
    version: 1,
    query: {
      force_unread: true,
      include_labels: [],
      exclude_labels: [],
      date_mode: 'custom',
    },
    run: {
      run_mode: 'all',
      batch_limit: 20,
      dry_run: false,
    },
    qualification: {
      location_strictness: 'balanced',
      score_threshold_override_enabled: false,
      score_threshold_override_value: 0.6,
    },
  }
  const policyProfiles: Record<PolicyProfileName, DynamicPolicy> = {
    Aggressive: {
      version: 1,
      query: { force_unread: true, include_labels: [], exclude_labels: [], date_mode: 'any' },
      run: { run_mode: 'all', batch_limit: 100, dry_run: false },
      qualification: {
        location_strictness: 'lenient',
        score_threshold_override_enabled: true,
        score_threshold_override_value: 0.5,
      },
    },
    Balanced: defaultPolicy,
    Strict: {
      version: 1,
      query: { force_unread: true, include_labels: [], exclude_labels: [], date_mode: 'custom' },
      run: { run_mode: 'all', batch_limit: 10, dry_run: false },
      qualification: {
        location_strictness: 'strict',
        score_threshold_override_enabled: true,
        score_threshold_override_value: 0.75,
      },
    },
  }
  const profileNames: PolicyProfileName[] = ['Aggressive', 'Balanced', 'Strict']
  const [status, setStatus] = useState<GmailStatus | null>(null)
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null)
  const [telegramStatus, setTelegramStatus] = useState<TelegramStatus | null>(null)
  const [settings, setSettings] = useState<SettingsPayload>({
    enabled: true,
    gmail_query: 'is:unread',
    default_gmail_query: 'is:unread',
    mail_date: null,
    default_date_mode: 'today',
    min_salary: null,
    accepted_locations: [],
    visa_required_allowed: false,
    remote_preference: 'any',
    role_keywords: [],
    must_have_skills: [],
    free_text_guidance: '',
    qualification_threshold: 0.6,
    feature_auto_polling: false,
    feature_auto_poll_interval_minutes: 10,
    feature_auto_send: false,
    feature_retry_queue: false,
    feature_ai_enabled: false,
    fallback_draft_template: '',
    signature_name: '',
    signature_phone: '',
    signature_email: '',
    policy: defaultPolicy,
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
  const [sentQueue, setSentQueue] = useState<Candidate[]>([])
  const [routingFixes, setRoutingFixes] = useState<Record<number, { to: string; cc: string }>>({})
  const [fixingId, setFixingId] = useState<number | null>(null)
  const [activePage, setActivePage] = useState<'run_queue' | 'needs_review' | 'failed_mapping' | 'recent_runs' | 'sent_items'>('run_queue')
  const [dynamicPolicyBeta, setDynamicPolicyBeta] = useState(false)
  const [selectedProfileToApply, setSelectedProfileToApply] = useState<PolicyProfileName>('Balanced')
  const [lastAppliedProfile, setLastAppliedProfile] = useState<PolicyProfileName | null>(null)
  const [skillDraft, setSkillDraft] = useState('')
  const [timeRange, setTimeRange] = useState<TimeRangeKey>('current_day')
  const [productivityEvents, setProductivityEvents] = useState<ProductivityEvent[]>([])
  const [productivityTrend, setProductivityTrend] = useState<ProductivityTrendResponse | null>(null)
  const [isCandidateRefreshing, setIsCandidateRefreshing] = useState(false)
  const [candidateRefreshError, setCandidateRefreshError] = useState('')
  const datePickerRef = useRef<HTMLInputElement | null>(null)
  const lastTrackedViewRef = useRef<string | null>(null)
  const hasBootstrappedCandidatesRef = useRef(false)
  const candidateRefreshTrackerRef = useRef(0)

  const currentPolicy: DynamicPolicy = settings.policy ?? defaultPolicy
  const detectProfileFromPolicy = (policy: DynamicPolicy): PolicyProfileName | null => {
    for (const profileName of profileNames) {
      if (JSON.stringify(policyProfiles[profileName]) === JSON.stringify(policy)) return profileName
    }
    return null
  }
  const exactSelectedProfile = detectProfileFromPolicy(currentPolicy)
  const profileStatusLabel = exactSelectedProfile
    ? exactSelectedProfile
    : lastAppliedProfile
      ? `Custom (from ${lastAppliedProfile})`
      : 'Custom'

  const loadStatus = async () => {
    const res = await fetch(`${apiBase}/gmail/status`)
    if (!res.ok) throw new Error('Failed to load Gmail status')
    setStatus((await res.json()) as GmailStatus)
  }

  const loadAiStatus = async () => {
    const res = await fetch(`${apiBase}/ai/status`)
    if (!res.ok) throw new Error('Failed to load AI status')
    setAiStatus((await res.json()) as AiStatus)
  }

  const loadTelegramStatus = async () => {
    const res = await fetch(`${apiBase}/telegram/status`)
    if (!res.ok) throw new Error('Failed to load Telegram status')
    setTelegramStatus((await res.json()) as TelegramStatus)
  }

  const loadSettings = async (): Promise<SettingsPayload> => {
    const res = await fetch(`${apiBase}/settings`)
    if (!res.ok) throw new Error('Failed to load settings')
    const payload = (await res.json()) as SettingsPayload
    const normalized: SettingsPayload = {
      ...payload,
      default_gmail_query: payload.default_gmail_query || payload.gmail_query || 'is:unread',
      default_date_mode: payload.default_date_mode === 'off' ? 'off' : 'today',
      feature_auto_poll_interval_minutes: Math.max(1, Math.min(payload.feature_auto_poll_interval_minutes || 10, 1440)),
      policy: payload.policy ?? defaultPolicy,
    }
    setSettings(normalized)
    if (payload.policy_profile_selected && profileNames.includes(payload.policy_profile_selected as PolicyProfileName)) {
      setSelectedProfileToApply(payload.policy_profile_selected as PolicyProfileName)
      setLastAppliedProfile(payload.policy_profile_selected as PolicyProfileName)
      return normalized
    }
    const detected = detectProfileFromPolicy(normalized.policy ?? defaultPolicy)
    if (detected) {
      setSelectedProfileToApply(detected)
      setLastAppliedProfile(detected)
    }
    return normalized
  }

  const loadActiveResume = async () => {
    const res = await fetch(`${apiBase}/settings/resumes`)
    if (!res.ok) throw new Error('Failed to load resumes')
    const items = (await res.json()) as ResumeAsset[]
    const current = items.find((item) => item.is_current) ?? null
    setActiveResume(current)
  }

  const refreshCandidates = async (mailDate: string | null) => {
    await refreshCandidateBuckets({
      apiBase,
      limit: QUEUE_LIMIT,
      mailDate,
      tracker: candidateRefreshTrackerRef,
      onStart: () => {
        setIsCandidateRefreshing(true)
        setCandidateRefreshError('')
      },
      onSuccess: ({ queue: nextQueue, failed: nextFailed, sent: nextSent }) => {
        setQueue(nextQueue as Candidate[])
        setFailedQueue(nextFailed as Candidate[])
        setSentQueue(nextSent as Candidate[])
        setDraftEdits((prev) => {
          const next = { ...prev }
          for (const c of nextQueue as Candidate[]) {
            if (!(c.id in next)) next[c.id] = c.draft_reply ?? ''
          }
          return next
        })
        setRoutingFixes((prev) => {
          const next = { ...prev }
          for (const c of nextFailed as Candidate[]) {
            if (!(c.id in next)) {
              next[c.id] = { to: c.recipient_email ?? '', cc: c.cc_email ?? '' }
            }
          }
          return next
        })
      },
      onError: (err) => {
        setCandidateRefreshError(err.message)
      },
      onFinally: () => {
        setIsCandidateRefreshing(false)
      },
    })
  }

  const loadProductivityAnalytics = async (range: TimeRangeKey = timeRange) => {
    const [eventsRes, trendRes] = await Promise.all([
      fetch(`${apiBase}/analytics/events?range=${range}`),
      fetch(`${apiBase}/analytics/trend?range=${range}`),
    ])
    if (!eventsRes.ok) throw new Error('Failed to load productivity events')
    if (!trendRes.ok) throw new Error('Failed to load productivity trend')
    setProductivityEvents((await eventsRes.json()) as ProductivityEvent[])
    setProductivityTrend((await trendRes.json()) as ProductivityTrendResponse)
  }

  const trackViewEvent = async (page: typeof activePage) => {
    const eventMap: Record<typeof activePage, string> = {
      run_queue: 'view_run_queue',
      needs_review: 'view_needs_review',
      failed_mapping: 'view_failed_mapping',
      recent_runs: 'view_recent_runs',
      sent_items: 'view_sent_items',
    }
    const eventType = eventMap[page]
    if (lastTrackedViewRef.current === `${page}-${timeRange}`) return
    await fetch(`${apiBase}/analytics/events/view`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        event_type: eventType,
        event_source: 'ui',
        metadata: { page, range: timeRange },
      }),
    }).catch(() => {
      // Keep UI responsive even if analytics logging fails.
    })
    lastTrackedViewRef.current = `${page}-${timeRange}`
  }

  useEffect(() => {
    const bootstrap = async () => {
      try {
        await Promise.all([
          loadStatus(),
          loadActiveResume(),
          loadAiStatus(),
          loadTelegramStatus(),
          loadProductivityAnalytics(),
        ])
        const normalizedSettings = await loadSettings()
        await refreshCandidates(normalizedSettings.mail_date ?? null)
        hasBootstrappedCandidatesRef.current = true
      } catch (e) {
        setError((e as Error).message)
      }
    }
    bootstrap().catch((e) => setError((e as Error).message))
  }, [])

  useEffect(() => {
    if (!hasBootstrappedCandidatesRef.current) return
    refreshCandidates(settings.mail_date ?? null).catch((e) => setError((e as Error).message))
  }, [settings.mail_date])

  useEffect(() => {
    loadProductivityAnalytics(timeRange).catch((e) => setError((e as Error).message))
  }, [timeRange])

  useEffect(() => {
    trackViewEvent(activePage)
      .then(() => loadProductivityAnalytics(timeRange))
      .catch((e) => setError((e as Error).message))
  }, [activePage, timeRange])

  useEffect(() => {
    if (!running) return
    const intervalId = window.setInterval(() => {
      loadAiStatus().catch(() => {
        // Keep the run UI stable; the main request will surface actionable errors.
      })
    }, 1000)
    return () => window.clearInterval(intervalId)
  }, [running])

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
      const controller = new AbortController()
      const timeoutMs = settings.feature_ai_enabled ? 90000 : 45000
      const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)
      const res = await fetch(`${apiBase}/automation/run-once`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mail_date: settings.mail_date || null }),
        signal: controller.signal,
      })
      window.clearTimeout(timeoutId)
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Automation run failed')
      }
      const data = (await res.json()) as AutomationRunResponse
      setLogs((prev) => [data, ...prev].slice(0, RECENT_RUNS_LIMIT))
      if (data.status === 'oauth_required' || data.status === 'oauth_in_progress') {
        setError(data.detail)
      }
      await loadStatus()
      await loadAiStatus()
      await loadTelegramStatus()
      await refreshCandidates(settings.mail_date ?? null)
      await loadProductivityAnalytics(timeRange)
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        if (!status?.authenticated) {
          setError('Request timed out. Gmail OAuth may be waiting in backend logs. Complete Google sign-in, then retry.')
        } else if (settings.feature_ai_enabled && aiStatus?.connected) {
          setError('AI reply generation is taking longer than expected. The backend may still finish; wait a moment, then refresh the queue.')
        } else {
          setError('Sync is taking longer than expected. Wait a moment, then retry Sync + Queue.')
        }
      } else {
        setError((e as Error).message)
      }
    } finally {
      setRunning(false)
    }
  }

  const connectGmail = async () => {
    setRunning(true)
    setError('')
    try {
      const res = await fetch(`${apiBase}/gmail/oauth/start`, {
        method: 'POST',
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Failed to start Gmail OAuth')
      }
      const data = (await res.json()) as OAuthStartResponse
      setLogs((prev) => [{ status: data.status, detail: data.detail, email_id: null }, ...prev].slice(0, RECENT_RUNS_LIMIT))
      if (data.status !== 'ready') {
        setError(data.detail)
      }
      await loadStatus()
      await loadAiStatus()
      await loadTelegramStatus()
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
      await refreshCandidates(settings.mail_date ?? null)
      await loadProductivityAnalytics(timeRange)
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
      await refreshCandidates(settings.mail_date ?? null)
      await loadProductivityAnalytics(timeRange)
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
      await refreshCandidates(settings.mail_date ?? null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setFixingId(null)
    }
  }

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
  const aiLastDuration = aiStatus?.last_duration_ms
    ? `${(aiStatus.last_duration_ms / 1000).toFixed(1)}s`
    : null
  const trendBars = useMemo<ProductivityBarPoint[]>(
    () => (productivityTrend?.bars ?? []),
    [productivityTrend?.bars],
  )
  const latestScore = productivityTrend?.kpi_total_sent ?? trendBars.reduce((sum, bar) => sum + bar.sent_count, 0)
  const trendDelta = productivityTrend?.trend_delta_pct ?? 0
  const liveDirection = productivityTrend?.trend_direction === 'down' ? 'down' : (productivityTrend?.trend_direction ?? 'flat')
  const realtimeSignals = productivityEvents.slice(0, 8).map((event) => {
    const when = new Date(event.occurred_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    return `${when} ${event.event_type.replaceAll('_', ' ')}`
  })
  const visibleBars = [...trendBars].reverse()
  const maxSentInBars = Math.max(1, ...visibleBars.map((bar) => bar.sent_count))

  const formatBucketLabel = (timestamp: string, range: TimeRangeKey) => {
    const dt = new Date(timestamp)
    if (range === 'last_1h' || range === 'current_day') {
      return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    }
    if (range === 'current_month') {
      return dt.toLocaleDateString([], { month: 'short', day: 'numeric' })
    }
    return dt.toLocaleDateString([], { month: 'short', year: '2-digit' })
  }

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

  const addMustHaveSkill = (raw: string) => {
    const skill = raw.trim()
    if (!skill) return
    const exists = settings.must_have_skills.some((s) => s.toLowerCase() === skill.toLowerCase())
    if (exists) {
      setSkillDraft('')
      return
    }
    setSettings({ ...settings, must_have_skills: [...settings.must_have_skills, skill] })
    setSkillDraft('')
  }

  const removeMustHaveSkill = (skillToRemove: string) => {
    setSettings({
      ...settings,
      must_have_skills: settings.must_have_skills.filter((s) => s.toLowerCase() !== skillToRemove.toLowerCase()),
    })
  }

  return (
    <main className="gmailShell">
      <Sidebar
        running={running}
        queueCount={queue.length}
        failedCount={failedQueue.length}
        runCount={logs.length}
        sentCount={sentQueue.length}
        activePage={activePage}
        onNavigate={setActivePage}
      />

      <section className="mainPane">
        <header className="topHeader">
          <div className="topSearch">
            <input
              className="search"
              value={settings.gmail_query}
              onChange={(e) => setSettings({ ...settings, gmail_query: e.target.value })}
              placeholder="Search Dashboard..."
            />
          </div>
          <div className="topActions">
            <button type="button" className="btnMuted">Batch Queue</button>
            <button
              type="button"
              className="btnPrimary"
              onClick={status?.authenticated ? runAutomation : connectGmail}
              disabled={running}
            >
              {running ? 'Running...' : status?.authenticated ? 'Sync Now' : 'Connect Gmail'}
            </button>
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
          </div>
        </header>

        <div className="pageBody">
          <div className="titleBlock">
            <h1>Run Queue Dashboard</h1>
            <p>Manage and monitor your automated recruitment email operations.</p>
          </div>

          <section className="statsGrid">
            <article className="statCard">
              <p>Needs Review</p>
              <strong>{queue.length}</strong>
            </article>
            <article className="statCard error">
              <p>Failed Mapping</p>
              <strong>{failedQueue.length}</strong>
            </article>
            <article className="statCard">
              <p>Recent Runs</p>
              <strong>{logs.length}</strong>
            </article>
          </section>
          {isCandidateRefreshing ? <p className="subtle">Refreshing filtered counts...</p> : null}
          {candidateRefreshError ? <p className="subtle">Counts refresh issue: {candidateRefreshError}</p> : null}

          <section className="actionBar">
            <input
              value={settings.gmail_query}
              onChange={(e) => setSettings({ ...settings, gmail_query: e.target.value })}
              placeholder="tx is:unread"
            />
            <button
              type="button"
              className="syncBtn topBarAction"
              onClick={status?.authenticated ? runAutomation : connectGmail}
              disabled={running}
            >
              {running ? 'Running...' : status?.authenticated ? 'Sync + Queue' : 'Connect Gmail'}
            </button>
          </section>

          {activePage === 'run_queue' ? (
            <section className="liveMonitorCard">
              <div className="liveMonitorHeader">
                <div>
                  <h2>Live Automation Monitor</h2>
                  <p>Real-time productivity trend from recorded events and workflow actions.</p>
                </div>
                <label className="monitorRange">
                  Range
                  <select value={timeRange} onChange={(e) => setTimeRange(e.target.value as TimeRangeKey)}>
                    <option value="last_1h">Last 1 hour</option>
                    <option value="current_day">Current day</option>
                    <option value="current_month">Current month</option>
                    <option value="current_year">Current year</option>
                    <option value="last_5y">Last 5 years</option>
                  </select>
                </label>
                <div className={`liveTicker ${liveDirection}`}>
                  <strong>{latestScore.toFixed(1)}</strong>
                  <span>{trendDelta >= 0 ? '+' : ''}{trendDelta.toFixed(1)}%</span>
                </div>
              </div>
              <div className="liveChartWrap">
                <div className="chartAxisLabel yAxisLabel">Approved Sent Count</div>
                <div className="hBarChart" aria-label="Productivity horizontal bar chart">
                  {visibleBars.map((bar) => {
                    const width = Math.max(0, Math.round((bar.sent_count / maxSentInBars) * 100))
                    return (
                      <div key={bar.ts} className="hBarRow" title={`${bar.sent_count} approved sent`}>
                        <div className="hBarLabel">{formatBucketLabel(bar.ts, timeRange)}</div>
                        <div className="hBarTrack">
                          <div className="hBarFill" style={{ width: `${width}%` }} />
                        </div>
                        <div className="hBarValue">{bar.sent_count}</div>
                      </div>
                    )
                  })}
                </div>
                <div className="chartAxisLabel xAxisLabel">Time Buckets</div>
                {visibleBars.length === 0 || visibleBars.every((bar) => bar.sent_count === 0) ? (
                  <p className="chartEmptyState">No approved sends in this period yet.</p>
                ) : null}
              </div>
              <div className="signalTape">
                {realtimeSignals.length === 0 ? <span>No events in selected range</span> : null}
                {realtimeSignals.map((signal, index) => (
                  // Event type + time helps users audit what drives the trend line.
                  // eslint-disable-next-line react/no-array-index-key
                  <span key={`${signal}-${index}`}>
                    {signal}
                  </span>
                ))}
              </div>
              <div className="monitorMeta">
                <span>Total approved & sent: {productivityTrend?.kpi_total_sent ?? 0}</span>
                <span>Previous period sent: {productivityTrend?.previous_period_total_sent ?? 0}</span>
                <span>Trend: {productivityTrend?.trend_direction ?? 'flat'} ({trendDelta >= 0 ? '+' : ''}{trendDelta.toFixed(1)}%)</span>
              </div>
              <h3 className="monitorSectionTitle">Activity Log</h3>
              <div className="monitorHistory">
                {productivityEvents.slice(0, 12).map((event) => (
                  <span key={event.id}>
                    {new Date(event.occurred_at).toLocaleString()} - {event.event_type}
                  </span>
                ))}
                {productivityEvents.length === 0 ? (
                  <span>No recorded history yet</span>
                ) : null}
              </div>
            </section>
          ) : null}

          {activePage === 'run_queue' ? (
            <form className="configGrid" onSubmit={saveSettings}>
              <section className="card">
                <h2>Gmail Access</h2>
                <div className="stack">
                  <div className="row"><span className="label">Status</span><span className="tag">{status?.authenticated ? 'Authenticated' : 'Not authenticated'}</span></div>
                  <div className="row"><span className="label">Configured</span><span>{status?.configured ? 'Yes' : 'No'}</span></div>
                  <div className="row"><span className="label">Account</span><span>{status?.token_path ?? '-'}</span></div>
                  <div className="row"><span className="label">Last Sync</span><span>{status?.last_sync_at ?? 'Never'}</span></div>
                  <div className="row"><span className="label">Telegram</span><span>{telegramStatus?.polling ? 'Connected' : telegramStatus?.enabled ? 'Starting' : 'Disabled'}</span></div>
                  <div className="row"><span className="label">Authorized Chats</span><span>{telegramStatus?.authorized_chats ?? 0}</span></div>
                </div>
              </section>

              <section className="card">
                <h2>AI Access</h2>
                <div className="stack">
                  <div className="row"><span className="label">Provider</span><span>{aiStatus?.provider ?? 'DeepSeek'}</span></div>
                  <div className="row"><span className="label">Model</span><span className="tag">{aiStatus?.model ?? 'deepseek-chat'}</span></div>
                  <div className="row"><span className="label">Connection</span><span className="dotOk">{aiStatus?.connected ? 'Healthy' : 'Disconnected'}</span></div>
                  {aiStatus?.last_draft_source ? <div className="row"><span className="label">Draft Source</span><span>{getDraftSourceLabel(aiStatus.last_draft_source)}</span></div> : null}
                  {aiLastDuration ? <div className="row"><span className="label">Last Duration</span><span>{aiLastDuration}</span></div> : null}
                </div>
              </section>

              <section className="card">
                <h2>Automation Filters</h2>
                <div className="stack">
                  <label className="toggleRow">
                    <span>Enable AI Features</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_ai_enabled}
                        onChange={(e) => setSettings(withAiToggle(settings, e.target.checked))}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
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
                    <div className="skillBox">
                      {settings.must_have_skills.map((skill) => (
                        <span key={skill} className="skillChip">
                          {skill}
                          <button
                            type="button"
                            className="chipRemove"
                            onClick={() => removeMustHaveSkill(skill)}
                            aria-label={`Remove ${skill}`}
                            title={`Remove ${skill}`}
                          >
                            x
                          </button>
                        </span>
                      ))}
                      <input
                        value={skillDraft}
                        className="skillInput"
                        onChange={(e) => setSkillDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ',') {
                            e.preventDefault()
                            addMustHaveSkill(skillDraft)
                          } else if (e.key === 'Backspace' && !skillDraft && settings.must_have_skills.length > 0) {
                            removeMustHaveSkill(settings.must_have_skills[settings.must_have_skills.length - 1])
                          }
                        }}
                        onBlur={() => addMustHaveSkill(skillDraft)}
                        placeholder="Add skill..."
                      />
                    </div>
                  </label>
                </div>
              </section>

              <section className="card">
                <h2>Dynamic Policy</h2>
                <div className="stack">
                  <label className="toggleRow">
                    <span>Use Dynamic Policy</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={dynamicPolicyBeta}
                        onChange={(e) => setDynamicPolicyBeta(e.target.checked)}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <label>
                    Policy Profile
                    <select
                      value={selectedProfileToApply}
                      onChange={(e) => setSelectedProfileToApply(e.target.value as PolicyProfileName)}
                    >
                      {profileNames.map((profileName) => (
                        <option key={profileName} value={profileName}>{profileName}</option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    onClick={() => {
                      const profilePolicy = policyProfiles[selectedProfileToApply]
                      setSettings({ ...settings, policy: profilePolicy })
                      setLastAppliedProfile(selectedProfileToApply)
                    }}
                  >
                    Apply Profile
                  </button>
                  <p className="subtle">Selected profile: {profileStatusLabel}</p>
                  {dynamicPolicyBeta ? (
                    <>
                      <label className="toggleRow">
                        <span>Force unread in query</span>
                        <span className="toggleSwitch">
                          <input
                            type="checkbox"
                            checked={currentPolicy.query.force_unread}
                            onChange={(e) =>
                              setSettings({
                                ...settings,
                                policy: {
                                  ...currentPolicy,
                                  query: { ...currentPolicy.query, force_unread: e.target.checked },
                                },
                              })
                            }
                          />
                          <span className="toggleTrack" />
                        </span>
                      </label>
                      <label>
                        Include labels
                        <input
                          value={currentPolicy.query.include_labels.join(',')}
                          onChange={(e) =>
                            setSettings({
                              ...settings,
                              policy: {
                                ...currentPolicy,
                                query: {
                                  ...currentPolicy.query,
                                  include_labels: e.target.value.split(',').map((v) => v.trim()).filter(Boolean),
                                },
                              },
                            })
                          }
                        />
                      </label>
                      <label>
                        Exclude labels
                        <input
                          value={currentPolicy.query.exclude_labels.join(',')}
                          onChange={(e) =>
                            setSettings({
                              ...settings,
                              policy: {
                                ...currentPolicy,
                                query: {
                                  ...currentPolicy.query,
                                  exclude_labels: e.target.value.split(',').map((v) => v.trim()).filter(Boolean),
                                },
                              },
                            })
                          }
                        />
                      </label>
                    </>
                  ) : null}
                </div>
              </section>

              <section className="card">
                <h2>Profile Settings</h2>
                <div className="stack">
                  <label>
                    Default Query
                    <input
                      value={settings.default_gmail_query}
                      onChange={(e) => setSettings({ ...settings, default_gmail_query: e.target.value })}
                      placeholder="is:unread in:inbox recruiter"
                    />
                  </label>
                  <label>
                    Default Date
                    <select
                      value={settings.default_date_mode}
                      onChange={(e) => setSettings({ ...settings, default_date_mode: e.target.value as 'today' | 'off' })}
                    >
                      <option value="today">Today (auto)</option>
                      <option value="off">Off</option>
                    </select>
                  </label>
                  <label className="toggleRow">
                    <span>Auto Run Every N Minutes</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_auto_polling}
                        onChange={(e) => setSettings({ ...settings, feature_auto_polling: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <label>
                    Auto Run Interval (minutes)
                    <input
                      type="number"
                      min={1}
                      max={1440}
                      value={settings.feature_auto_poll_interval_minutes}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          feature_auto_poll_interval_minutes: Math.max(1, Math.min(Number(e.target.value) || 10, 1440)),
                        })
                      }
                    />
                  </label>
                  <label>
                    Signature Name
                    <input
                      value={settings.signature_name}
                      onChange={(e) => setSettings({ ...settings, signature_name: e.target.value })}
                      placeholder="Your full name"
                    />
                  </label>
                  <label>
                    Signature Phone
                    <input
                      value={settings.signature_phone}
                      onChange={(e) => setSettings({ ...settings, signature_phone: e.target.value })}
                      placeholder="+1 555-555-5555"
                    />
                  </label>
                  <label>
                    Signature Email
                    <input
                      value={settings.signature_email}
                      onChange={(e) => setSettings({ ...settings, signature_email: e.target.value })}
                      placeholder="you@example.com"
                    />
                  </label>
                  <p className="subtle">These defaults are shared with Telegram and used by <code>/run</code>. Auto-run settings are also synced to Telegram.</p>
                </div>
              </section>

              <section className="card">
                <h2>Execution Control</h2>
                <div className="stack">
                  <label className="toggleRow pillRow">
                    <span>Dry Run Mode</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={currentPolicy.run.dry_run}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            policy: {
                              ...currentPolicy,
                              run: { ...currentPolicy.run, dry_run: e.target.checked },
                            },
                          })
                        }
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <label>
                    Batch Limit
                    <input
                      type="number"
                      min={1}
                      max={200}
                      value={currentPolicy.run.batch_limit}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          policy: {
                            ...currentPolicy,
                            run: { ...currentPolicy.run, batch_limit: Number(e.target.value) },
                          },
                        })
                      }
                    />
                  </label>
                  <label>
                    Date mode
                    <select
                      value={currentPolicy.query.date_mode}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          policy: {
                            ...currentPolicy,
                            query: { ...currentPolicy.query, date_mode: e.target.value as 'custom' | 'any' },
                          },
                        })
                      }
                    >
                      <option value="custom">Use selected date</option>
                      <option value="any">Ignore selected date</option>
                    </select>
                  </label>
                  <label>
                    Fallback Draft Template
                    <textarea
                      className="fallbackTemplateTextarea"
                      rows={10}
                      value={settings.fallback_draft_template}
                      onChange={(e) => setSettings({ ...settings, fallback_draft_template: e.target.value })}
                      placeholder={"Use tokens like {{greeting}}, {{role}}, {{skills_list}}, {{requested_details_block}}, {{signature_name}}"}
                    />
                  </label>
                  <p className="subtle">
                    Available tokens: {'{{greeting}}'}, {'{{role}}'}, {'{{sender}}'}, {'{{location}}'}, {'{{salary_text}}'}, {'{{skills_list}}'}, {'{{skills_inline}}'}, {'{{resume_file_name}}'}, {'{{signature_name}}'}, {'{{signature_phone}}'}, {'{{signature_email}}'}, {'{{requested_details_block}}'}.
                  </p>
                  <button type="submit" disabled={saving}>{saving ? 'Saving...' : 'Save Filters'}</button>
                  <p className="subtle">
                    {activeResume ? `Active resume: ${activeResume.file_name} (v${activeResume.version})` : 'No active resume uploaded yet.'}
                  </p>
                  <input type="file" accept=".pdf,.doc,.docx" onChange={(e) => setResumeFile(e.target.files?.[0] ?? null)} />
                  <button type="button" onClick={uploadResume} disabled={!resumeFile}>
                    {activeResume ? 'Replace Resume' : 'Upload Resume'}
                  </button>
                </div>
              </section>
            </form>
          ) : null}

          {error ? <p className="errorMessage">{error}</p> : null}

          {activePage === 'needs_review' ? (
            <section className="card pageSection">
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
                <p>
                  <strong>Draft source:</strong> {getDraftSourceLabel(item.draft_source)}
                  {item.draft_model ? ` (${item.draft_model})` : ''}
                </p>
                {item.draft_ai_error ? <p className="subtle"><strong>AI fallback:</strong> {item.draft_ai_error}</p> : null}
                <p><strong>Draft:</strong></p>
                <div className="draftUnified">
                  <label className="draftPaneLabel">Editable Draft</label>
                  <textarea
                    value={editedDraft}
                    rows={10}
                    onChange={(e) => setDraftEdits((prev) => ({ ...prev, [item.id]: e.target.value }))}
                  />
                  <label className="draftPaneLabel">Live Preview</label>
                  <div
                    className="draftPreview"
                    dangerouslySetInnerHTML={{ __html: draftToPreviewHtml(editedDraft) }}
                  />
                </div>
                {item.last_error ? <p className="errorMessage"><strong>Last Error:</strong> {item.last_error}</p> : null}
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
            <section className="card pageSection">
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
            <section className="card pageSection">
          <h2>Recent Runs</h2>
          {logs.length === 0 ? <p className="subtle">No runs yet.</p> : null}
          {logs.map((item, index) => (
            <article key={`${item.email_id ?? 'none'}-${index}`} className="emailItem">
              <p><strong>Status:</strong> {item.status}</p>
              <p><strong>Detail:</strong> {item.detail}</p>
              <p><strong>Email ID:</strong> {item.email_id ?? '-'}</p>
              {item.gmail_message_url ? (
                <p>
                  <strong>Open:</strong>{' '}
                  <a href={item.gmail_message_url} target="_blank" rel="noreferrer">
                    Open exact email in Gmail
                  </a>
                </p>
              ) : null}
              {item.decision_reason || item.skip_reason || item.routing_reason ? (
                <p className="subtle">
                  <strong>Why:</strong>{' '}
                  {[
                    item.decision_reason ? `Decision: ${item.decision_reason}` : null,
                    item.skip_reason ? `Skip: ${item.skip_reason}` : null,
                    item.routing_reason ? `Routing: ${item.routing_reason}` : null,
                  ]
                    .filter(Boolean)
                    .join(' | ')}
                </p>
              ) : null}
              {item.effective_query || item.matched_count != null || item.queued_count != null || item.skipped_count != null || item.failed_count != null ? (
                <p className="subtle">
                  <strong>Summary:</strong>{' '}
                  {[
                    item.effective_query ? `Query: ${item.effective_query}` : null,
                    item.matched_count != null ? `Matched: ${item.matched_count}` : null,
                    item.queued_count != null ? `Queued: ${item.queued_count}` : null,
                    item.skipped_count != null ? `Skipped: ${item.skipped_count}` : null,
                    item.failed_count != null ? `Failed: ${item.failed_count}` : null,
                  ]
                    .filter(Boolean)
                    .join(' | ')}
                </p>
              ) : null}
            </article>
          ))}
            </section>
          ) : null}

          {activePage === 'sent_items' ? (
            <section className="card pageSection">
          <h2>Sent Items</h2>
          {sentQueue.length === 0 ? <p className="subtle">No approved and sent emails yet.</p> : null}
          {sentQueue.map((item) => (
            <article key={`sent-${item.id}`} className="emailItem">
              <p><strong>Email ID:</strong> {item.id}</p>
              <p><strong>From:</strong> {item.sender}</p>
              <p><strong>Subject:</strong> {item.subject}</p>
              <p><strong>Sent at:</strong> {item.sent_at ? new Date(item.sent_at).toLocaleString() : '-'}</p>
              {item.gmail_message_url ? (
                <p>
                  <strong>Open:</strong>{' '}
                  <a href={item.gmail_message_url} target="_blank" rel="noreferrer">
                    Open exact email in Gmail
                  </a>
                </p>
              ) : null}
            </article>
          ))}
            </section>
          ) : null}
        </div>
      </section>
    </main>
  )
}

export default App
