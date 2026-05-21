import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import Sidebar from './components/Sidebar'
import { withAiToggle } from './features/ai/state'
import { getDraftSourceLabel } from './features/ai/ui'
import { withSavedQueries } from './features/query_bucket/api'
import QueryBucket from './features/query_bucket/QueryBucket'
import { type CandidateState, useCandidateBuckets } from './candidateBuckets'
import { addEmployerDomain, removeEmployerDomain } from './employerDomains'

const GMAIL_OAUTH_POLL_INTERVAL_MS = 2000
const GMAIL_OAUTH_POLL_TIMEOUT_MS = 180000
const VIEW_EVENT_THROTTLE_MS = 60000
let hasBootstrappedAppOnce = false

export function shouldTrackViewEvent(
  lastTrackedAtByKey: Record<string, number>,
  throttleKey: string,
  now: number,
  throttleMs = VIEW_EVENT_THROTTLE_MS,
): boolean {
  const lastTrackedAt = lastTrackedAtByKey[throttleKey]
  if (lastTrackedAt === undefined) return true
  return now - lastTrackedAt >= throttleMs
}

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
  embedding_provider?: string
  embedding_model?: string
  embedding_connected?: boolean
  embedding_detail?: string
  embedding_configured?: boolean | null
  embedding_runtime_healthy?: boolean | null
  embedding_last_error?: string | null
  embedding_last_attempted_at?: string | null
  embedding_last_success_at?: string | null
  embedding_last_duration_ms?: number | null
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
  saved_gmail_queries: string[]
  mail_date: string | null
  default_date_mode: 'today' | 'off'
  min_salary: number | null
  accepted_locations: string[]
  visa_required_allowed: boolean
  remote_preference: string
  role_keywords: string[]
  must_have_skills: string[]
  employer_domains: string[]
  free_text_guidance: string
  qualification_threshold: number
  feature_auto_polling: boolean
  feature_auto_poll_interval_minutes: number
  feature_nvoids_enabled: boolean
  feature_nvoids_auto_sync: boolean
  feature_nvoids_poll_interval_minutes: number
  nvoids_batch_limit: number
  feature_auto_send: boolean
  feature_retry_queue: boolean
  feature_ai_enabled: boolean
  feature_semantic_enabled: boolean
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
  auto_sent_count?: number | null
  auto_send_failed_count?: number | null
  retry_promoted_count?: number | null
  retry_skipped_count?: number | null
}

type OAuthStartResponse = {
  status: string
  detail: string
  configured: boolean
  authenticated: boolean
  authorization_url?: string | null
}

type OAuthUrlResponse = {
  authorization_url?: string | null
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
  ai_score?: number | null
  draft_reply: string
  draft_source: string | null
  draft_model: string | null
  draft_ai_error: string | null
  draft_resume_context_status: string | null
  draft_quality?: DraftQuality | null
  resume_file_name: string | null
  state: string
  last_error: string | null
  source: string
  external_message_id: string | null
  external_thread_id: string | null
}

export const sourceListingUrl = (item: Candidate): string | null => {
  if (item.source !== 'nvoids') return null
  const thread = (item.external_thread_id ?? '').trim()
  if (thread.startsWith('http://') || thread.startsWith('https://')) return thread
  const message = (item.external_message_id ?? '').trim()
  const nvoidsIdMatch = message.match(/^nvoids:(?:nvoids:)?(\d+)$/i)
  if (nvoidsIdMatch) {
    return `https://nvoids.com/job_details.jsp?id=${nvoidsIdMatch[1]}`
  }
  return null
}

type PremiumNumberConfidence = 'high' | 'medium' | 'low'

type PremiumNumberCard = {
  id: number
  recruiter_email_id: number
  phone_number_display: string
  phone_number_normalized: string
  owner_name: string
  company: string
  designation: string
  purpose: string
  confidence: PremiumNumberConfidence
  contact_type: 'recruiter_direct' | 'submission_contact' | 'employer_internal' | 'unknown'
  recruiter_relevance_score: number
  is_recruiter_relevant: boolean
  relevance_reason: string
  source_fragment: string
  source_email_sender: string
  source_email_subject: string
  source_email_message_id: string | null
  created_at: string
  updated_at: string
}

type PremiumNumberListResponse = {
  items: PremiumNumberCard[]
  next_cursor: number | null
  has_next: boolean
}

type NumberReviewCard = {
  id: number
  source_email_id: number
  normalized_phone_number: string
  display_phone_number: string
  owner_name: string
  company: string
  designation: string
  confidence: PremiumNumberConfidence
  purpose: string
  evidence_snippet: string
  email_subject: string
  email_sender: string
  gmail_open_url: string
  state: string
}

type RecruiterNumberCard = {
  id: number
  normalized_phone_number: string
  display_phone_number: string
  recruiter_name: string
  company: string
  designation: string
  recruiter_email: string
  total_opportunity_count: number
  last_email_received_at: string | null
}

type EmployerNumberCard = {
  id: number
  normalized_phone_number: string
  display_phone_number: string
  owner_name: string
  company: string
  source_email_id: number | null
}

type OpportunityStatus = 'New' | 'Called' | 'Applied' | 'Follow Up' | 'Closed' | 'Not Interested'

type RecruiterOpportunityCard = {
  id: number
  recruiter_number_id: number
  source_email_id: number | null
  gmail_message_id: string
  source_type: 'gmail' | 'nvoids'
  source_url: string | null
  external_opportunity_id: number | null
  email_subject: string
  email_sender: string
  gmail_open_url: string
  received_at: string | null
  job_title: string
  client: string
  location: string
  work_mode: string
  visa_restrictions: string
  extracted_skills: string
  evidence: string
  recruiter_name: string
  recruiter_email: string
  recruiter_phone_display: string
  recruiter_phone_normalized: string
  status: OpportunityStatus
  notes: string
  cold_call_script: string | null
  cold_call_script_updated_at: string | null
}

type RecruiterOpportunityDeleteResponse = {
  id: number
  deleted: boolean
  recruiter_number_deleted: boolean
}

type RoutingEvidence = {
  role: string
  email: string
  source: string
  detail: string
}

type DraftQuality = {
  content_valid: boolean
  greeting_compliance: 'compliant' | 'missing' | 'multiple' | string
  resume_context_status: string
  confidence: number
  score: number
  label: VerdictLabel
  issues: string[]
}

type TimeRangeKey = 'last_1h' | 'current_day' | 'current_week' | 'current_month' | 'current_year' | 'last_5y'

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

type VerdictLabel = 'Excellent' | 'Strong' | 'Good' | 'Review' | 'Risky'
type VerdictTone = 'excellent' | 'strong' | 'good' | 'review' | 'risky'

type VerdictCandidateInput = Pick<
  Candidate,
  | 'ai_score'
  | 'routing_confidence'
  | 'draft_resume_context_status'
  | 'recipient_email'
  | 'cc_email'
  | 'draft_ai_error'
  | 'draft_quality'
>

export function clamp01(value: number | null | undefined): number {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0
  return Math.max(0, Math.min(value, 1))
}

export function clamp100(value: number): number {
  if (Number.isNaN(value)) return 0
  return Math.max(0, Math.min(Math.round(value), 100))
}

export function getOverallVerdict(
  candidate: VerdictCandidateInput,
  effectiveDraft: string,
  routingTrusted: boolean,
): { score: number; label: VerdictLabel; tone: VerdictTone } {
  if (candidate.draft_quality) {
    const score = clamp100(candidate.draft_quality.score)
    const label = candidate.draft_quality.label
    const toneMap: Record<VerdictLabel, VerdictTone> = {
      Excellent: 'excellent',
      Strong: 'strong',
      Good: 'good',
      Review: 'review',
      Risky: 'risky',
    }
    return { score, label, tone: toneMap[label] }
  }

  let total = 50
  total += clamp01(candidate.ai_score) * 30
  total += clamp01(candidate.routing_confidence) * 20

  const contextStatus = candidate.draft_resume_context_status ?? 'unknown'
  if (contextStatus === 'injected') total += 8
  else if (contextStatus === 'limited') total += 2
  else if (contextStatus === 'missing_resume' || contextStatus === 'extract_failed') total -= 12
  else total -= 4

  total += routingTrusted ? 6 : -10
  if (!candidate.recipient_email || !candidate.cc_email) total -= 6
  if (!effectiveDraft.trim()) total -= 10
  if (candidate.draft_ai_error) total -= 6

  const score = clamp100(total)
  if (score >= 95) return { score, label: 'Excellent', tone: 'excellent' }
  if (score >= 90) return { score, label: 'Strong', tone: 'strong' }
  if (score >= 80) return { score, label: 'Good', tone: 'good' }
  if (score >= 70) return { score, label: 'Review', tone: 'review' }
  return { score, label: 'Risky', tone: 'risky' }
}

function getResumeContextLabel(value: string | null | undefined): string {
  if (value === 'injected') return 'Injected'
  if (value === 'limited') return 'Limited'
  if (value === 'missing_resume') return 'Missing Resume'
  if (value === 'extract_failed') return 'Extract Failed'
  if (value === 'rules_only') return 'Rules Only'
  return 'Unknown'
}

function App() {
  const INITIAL_BUCKET_LIMIT = 25
  const PAGE_BUCKET_LIMIT = 25
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
    saved_gmail_queries: [],
    mail_date: null,
    default_date_mode: 'today',
    min_salary: null,
    accepted_locations: [],
    visa_required_allowed: false,
    remote_preference: 'any',
    role_keywords: [],
    must_have_skills: [],
    employer_domains: [],
    free_text_guidance: '',
    qualification_threshold: 0.6,
    feature_auto_polling: false,
    feature_auto_poll_interval_minutes: 10,
    feature_nvoids_enabled: true,
    feature_nvoids_auto_sync: false,
    feature_nvoids_poll_interval_minutes: 30,
    nvoids_batch_limit: 10,
    feature_auto_send: false,
    feature_retry_queue: false,
    feature_ai_enabled: false,
    feature_semantic_enabled: false,
    fallback_draft_template: '',
    signature_name: '',
    signature_phone: '',
    signature_email: '',
    policy: defaultPolicy,
  })
  const [resumeFile, setResumeFile] = useState<File | null>(null)
  const [activeResume, setActiveResume] = useState<ResumeAsset | null>(null)
  const [running, setRunning] = useState(false)
  const [nvoidsRunning, setNvoidsRunning] = useState(false)
  const [oauthInProgress, setOauthInProgress] = useState(false)
  const [oauthAuthorizationUrl, setOauthAuthorizationUrl] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [logs, setLogs] = useState<AutomationRunResponse[]>([])
  const [sendingId, setSendingId] = useState<number | null>(null)
  const [rejectingId, setRejectingId] = useState<number | null>(null)
  const [movingToFailedId, setMovingToFailedId] = useState<number | null>(null)
  const [draftEdits, setDraftEdits] = useState<Record<number, string>>({})
  const [routingFixes, setRoutingFixes] = useState<Record<number, { to: string; cc: string }>>({})
  const [fixingId, setFixingId] = useState<number | null>(null)
  const [activePage, setActivePage] = useState<'run_queue' | 'needs_review' | 'failed_mapping' | 'recent_runs' | 'sent_items' | 'premium_numbers'>('run_queue')
  const [dynamicPolicyBeta, setDynamicPolicyBeta] = useState(false)
  const [selectedProfileToApply, setSelectedProfileToApply] = useState<PolicyProfileName>('Balanced')
  const [lastAppliedProfile, setLastAppliedProfile] = useState<PolicyProfileName | null>(null)
  const [skillDraft, setSkillDraft] = useState('')
  const [employerDomainDraft, setEmployerDomainDraft] = useState('')
  const [employerDomainError, setEmployerDomainError] = useState('')
  const [numberReviewCards, setNumberReviewCards] = useState<NumberReviewCard[]>([])
  const [recruiterNumberCards, setRecruiterNumberCards] = useState<RecruiterNumberCard[]>([])
  const [employerNumberCards, setEmployerNumberCards] = useState<EmployerNumberCard[]>([])
  const [opportunityCards, setOpportunityCards] = useState<RecruiterOpportunityCard[]>([])
  const [premiumNextCursor, setPremiumNextCursor] = useState<number | null>(null)
  const [premiumHasNext, setPremiumHasNext] = useState(false)
  const [premiumLoading, setPremiumLoading] = useState(false)
  const [premiumError, setPremiumError] = useState('')
  const premiumConfidenceFilter: 'all' | PremiumNumberConfidence = 'all'
  const [premiumScopeFilter, setPremiumScopeFilter] = useState<'all_review' | 'recruiter_numbers' | 'employer_numbers' | 'recruiter_opportunities'>('all_review')
  const [opportunityStatusFilter, setOpportunityStatusFilter] = useState<'all' | OpportunityStatus>('all')
  const [opportunitySourceFilter, setOpportunitySourceFilter] = useState<'all' | 'gmail' | 'nvoids'>('all')
  const [premiumSearch, setPremiumSearch] = useState('')
  const [updatingOpportunityId, setUpdatingOpportunityId] = useState<number | null>(null)
  const [deletingOpportunityId, setDeletingOpportunityId] = useState<number | null>(null)
  const [generatingColdCallId, setGeneratingColdCallId] = useState<number | null>(null)
  const [classifyingReviewId, setClassifyingReviewId] = useState<number | null>(null)
  const [timeRange, setTimeRange] = useState<TimeRangeKey>('current_day')
  const [productivityEvents, setProductivityEvents] = useState<ProductivityEvent[]>([])
  const [productivityTrend, setProductivityTrend] = useState<ProductivityTrendResponse | null>(null)
  const datePickerRef = useRef<HTMLInputElement | null>(null)
  const lastTrackedViewRef = useRef<Record<string, number>>({})
  const hasBootstrappedCandidatesRef = useRef(false)
  const oauthPollingStartedAtRef = useRef<number | null>(null)
  const refreshTimerRef = useRef<number | null>(null)

  const {
    queue,
    failedQueue,
    sentQueue,
    isCandidateRefreshing,
    candidateRefreshError,
    loadingMoreKey,
    bucketMeta,
    refreshCandidates,
    loadCandidateBucket,
    loadMoreCandidates,
  } = useCandidateBuckets<Candidate>({
    apiBase,
    pageBucketLimit: PAGE_BUCKET_LIMIT,
    initialBucketLimit: INITIAL_BUCKET_LIMIT,
    onNeedsReviewItems: (items) => {
      setDraftEdits((prev) => {
        let next: typeof prev | null = null
        for (const c of items) {
          if (!(c.id in prev)) {
            next ??= { ...prev }
            next[c.id] = c.draft_reply ?? ''
          }
        }
        return next ?? prev
      })
    },
    onFailedItems: (items) => {
      setRoutingFixes((prev) => {
        let next: typeof prev | null = null
        for (const c of items) {
          if (!(c.id in prev)) {
            next ??= { ...prev }
            next[c.id] = { to: c.recipient_email ?? '', cc: c.cc_email ?? '' }
          }
        }
        return next ?? prev
      })
    },
  })

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

  const loadStatus = async (): Promise<GmailStatus> => {
    const res = await fetch(`${apiBase}/gmail/status`)
    if (!res.ok) throw new Error('Failed to load Gmail status')
    const payload = (await res.json()) as GmailStatus
    setStatus(payload)
    return payload
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

  const loadOauthAuthorizationUrl = async () => {
    const res = await fetch(`${apiBase}/gmail/oauth/url`)
    if (!res.ok) return null
    const payload = (await res.json()) as OAuthUrlResponse
    return payload.authorization_url ?? null
  }

  const fetchAndOpenOauthUrl = async () => {
    const url = await loadOauthAuthorizationUrl()
    if (!url) {
      setError('OAuth URL is not ready yet. Wait 1-2 seconds and click Get OAuth URL again.')
      return
    }
    setOauthAuthorizationUrl(url)
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  const loadSettings = async (): Promise<SettingsPayload> => {
    const res = await fetch(`${apiBase}/settings`)
    if (!res.ok) throw new Error('Failed to load settings')
    const payload = (await res.json()) as SettingsPayload
    const normalized: SettingsPayload = {
      ...payload,
      feature_semantic_enabled: Boolean(payload.feature_semantic_enabled),
      default_gmail_query: payload.default_gmail_query || payload.gmail_query || 'is:unread',
      saved_gmail_queries: payload.saved_gmail_queries ?? [],
      default_date_mode: payload.default_date_mode === 'off' ? 'off' : 'today',
      feature_auto_poll_interval_minutes: Math.max(1, Math.min(payload.feature_auto_poll_interval_minutes || 10, 1440)),
      feature_nvoids_enabled: Boolean(payload.feature_nvoids_enabled ?? true),
      feature_nvoids_auto_sync: Boolean(payload.feature_nvoids_auto_sync ?? false),
      feature_nvoids_poll_interval_minutes: Math.max(1, Math.min(payload.feature_nvoids_poll_interval_minutes || 30, 1440)),
      nvoids_batch_limit: Math.max(1, Math.min(payload.nvoids_batch_limit || 10, 50)),
      employer_domains: payload.employer_domains ?? [],
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

  const loadPremiumNumbers = async (opts?: { append?: boolean; cursor?: number | null }) => {
    const cursor = opts?.cursor ?? 0
    setPremiumLoading(true)
    setPremiumError('')
    try {
      if (premiumScopeFilter === 'all_review') {
        const res = await fetch(`${apiBase}/number-review`)
        if (!res.ok) throw new Error('Failed to load number review queue')
        setNumberReviewCards((await res.json()) as NumberReviewCard[])
        setPremiumHasNext(false)
        setPremiumNextCursor(null)
      } else if (premiumScopeFilter === 'recruiter_numbers') {
        const res = await fetch(`${apiBase}/recruiter-numbers`)
        if (!res.ok) throw new Error('Failed to load recruiter numbers')
        setRecruiterNumberCards((await res.json()) as RecruiterNumberCard[])
        setPremiumHasNext(false)
        setPremiumNextCursor(null)
      } else if (premiumScopeFilter === 'employer_numbers') {
        const res = await fetch(`${apiBase}/employer-numbers`)
        if (!res.ok) throw new Error('Failed to load employer numbers')
        setEmployerNumberCards((await res.json()) as EmployerNumberCard[])
        setPremiumHasNext(false)
        setPremiumNextCursor(null)
      } else if (premiumScopeFilter === 'recruiter_opportunities') {
        const params = new URLSearchParams()
        if (opportunityStatusFilter !== 'all') params.set('status', opportunityStatusFilter)
        if (opportunitySourceFilter !== 'all') params.set('source_type', opportunitySourceFilter)
        if (premiumSearch.trim()) params.set('q', premiumSearch.trim())
        if (settings.mail_date) params.set('mail_date', settings.mail_date)
        const res = await fetch(`${apiBase}/recruiter-opportunities?${params.toString()}`)
        if (!res.ok) throw new Error('Failed to load recruiter opportunities')
        setOpportunityCards((await res.json()) as RecruiterOpportunityCard[])
        setPremiumHasNext(false)
        setPremiumNextCursor(null)
      } else {
        const params = new URLSearchParams({
          cursor: String(cursor),
          limit: '25',
        })
        if (premiumConfidenceFilter !== 'all') params.set('confidence', premiumConfidenceFilter)
        if (premiumSearch.trim()) params.set('q', premiumSearch.trim())
        if (settings.mail_date) params.set('mail_date', settings.mail_date)
        const res = await fetch(`${apiBase}/premium-numbers?${params.toString()}`)
        if (!res.ok) throw new Error('Failed to load premium numbers')
        const payload = (await res.json()) as PremiumNumberListResponse
        setPremiumNextCursor(payload.next_cursor)
        setPremiumHasNext(payload.has_next)
      }
    } catch (e) {
      setPremiumError((e as Error).message)
    } finally {
      setPremiumLoading(false)
    }
  }

  const markReviewCard = async (reviewId: number, mode: 'recruiter' | 'employer') => {
    setClassifyingReviewId(reviewId)
    try {
      const res = await fetch(
        `${apiBase}/number-review/${reviewId}/${mode === 'recruiter' ? 'mark-recruiter' : 'mark-employer'}`,
        { method: 'POST' },
      )
      if (!res.ok) throw new Error(`Failed to mark as ${mode}`)
      await loadPremiumNumbers({ append: false, cursor: 0 })
    } catch (e) {
      setPremiumError((e as Error).message)
    } finally {
      setClassifyingReviewId(null)
    }
  }

  const deleteReviewCard = async (reviewId: number) => {
    setClassifyingReviewId(reviewId)
    try {
      const res = await fetch(`${apiBase}/number-review/${reviewId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete review card')
      await loadPremiumNumbers({ append: false, cursor: 0 })
    } catch (e) {
      setPremiumError((e as Error).message)
    } finally {
      setClassifyingReviewId(null)
    }
  }

  const updateOpportunity = async (id: number, patch: Partial<Pick<RecruiterOpportunityCard, 'status' | 'notes'>>) => {
    setUpdatingOpportunityId(id)
    try {
      const res = await fetch(`${apiBase}/recruiter-opportunities/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!res.ok) throw new Error('Failed to update opportunity')
      const updated = (await res.json()) as RecruiterOpportunityCard
      setOpportunityCards((prev) => prev.map((item) => (item.id === id ? updated : item)))
    } catch (e) {
      setPremiumError((e as Error).message)
    } finally {
      setUpdatingOpportunityId(null)
    }
  }

  const generateColdCallScript = async (id: number) => {
    setGeneratingColdCallId(id)
    try {
      const res = await fetch(`${apiBase}/recruiter-opportunities/${id}/generate-cold-call-script`, {
        method: 'POST',
      })
      if (!res.ok) throw new Error('Failed to generate cold call script')
      const updated = (await res.json()) as RecruiterOpportunityCard
      setOpportunityCards((prev) => prev.map((item) => (item.id === id ? updated : item)))
    } catch (e) {
      setPremiumError((e as Error).message)
    } finally {
      setGeneratingColdCallId(null)
    }
  }

  const deleteOpportunity = async (id: number) => {
    setDeletingOpportunityId(id)
    try {
      const res = await fetch(`${apiBase}/recruiter-opportunities/${id}`, {
        method: 'DELETE',
      })
      if (!res.ok) throw new Error('Failed to delete opportunity')
      await res.json() as RecruiterOpportunityDeleteResponse
      setOpportunityCards((prev) => prev.filter((item) => item.id !== id))
      schedulePostMutationRefresh()
    } catch (e) {
      setPremiumError((e as Error).message)
    } finally {
      setDeletingOpportunityId(null)
    }
  }

  const swapNumberBucket = async (id: number, from: 'recruiter' | 'employer') => {
    setClassifyingReviewId(id)
    try {
      const endpoint =
        from === 'recruiter'
          ? `${apiBase}/recruiter-numbers/${id}/swap-to-employer`
          : `${apiBase}/employer-numbers/${id}/swap-to-recruiter`
      const res = await fetch(endpoint, { method: 'POST' })
      if (!res.ok) throw new Error('Failed to swap number bucket')
      await loadPremiumNumbers({ append: false, cursor: 0 })
    } catch (e) {
      setPremiumError((e as Error).message)
    } finally {
      setClassifyingReviewId(null)
    }
  }

  const bucketForPage = (page: typeof activePage): CandidateState | null => {
    if (page === 'run_queue' || page === 'needs_review') return 'needs_review'
    if (page === 'failed_mapping') return 'failed'
    if (page === 'sent_items') return 'approved_sent'
    return null
  }

  const refreshVisibleCandidates = async (
    mailDate: string | null,
    options?: { activeOnly?: boolean; includeLoaded?: boolean; initialLoad?: boolean },
  ) => {
    const activeBucket = bucketForPage(activePage) ?? 'needs_review'
    await refreshCandidates(mailDate, activeBucket, options)
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
      premium_numbers: 'view_premium_numbers',
    }
    const eventType = eventMap[page]
    const throttleKey = `${page}:${timeRange}`
    const now = Date.now()
    if (!shouldTrackViewEvent(lastTrackedViewRef.current, throttleKey, now)) return
    lastTrackedViewRef.current[throttleKey] = now
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
  }

  const schedulePostMutationRefresh = () => {
    if (refreshTimerRef.current !== null) {
      window.clearTimeout(refreshTimerRef.current)
    }
    refreshTimerRef.current = window.setTimeout(() => {
      refreshVisibleCandidates(settings.mail_date ?? null, { activeOnly: true, includeLoaded: true }).catch(() => {
        // Keep UI responsive if one refresh call fails; error surfaces on next action.
      })
      loadProductivityAnalytics(timeRange).catch(() => {
        // Keep UI responsive if analytics refresh fails transiently.
      })
      loadPremiumNumbers({ append: false, cursor: 0 }).catch(() => {
        // Keep UI responsive if premium numbers refresh fails transiently.
      })
    }, 200)
  }

  useEffect(() => {
    if (hasBootstrappedAppOnce) return
    hasBootstrappedAppOnce = true

    const bootstrap = async () => {
      try {
        await Promise.all([
          loadStatus(),
          loadActiveResume(),
          loadAiStatus(),
          loadTelegramStatus(),
        ])
        const normalizedSettings = await loadSettings()
        await refreshVisibleCandidates(normalizedSettings.mail_date ?? null, { activeOnly: true, initialLoad: true })
        await loadPremiumNumbers({ append: false, cursor: 0 })
        hasBootstrappedCandidatesRef.current = true
      } catch (e) {
        setError((e as Error).message)
      }
    }
    bootstrap().catch((e) => setError((e as Error).message))
  }, [])

  useEffect(() => {
    if (!hasBootstrappedCandidatesRef.current) return
    refreshVisibleCandidates(settings.mail_date ?? null, { activeOnly: true, includeLoaded: true }).catch((e) => setError((e as Error).message))
  }, [settings.mail_date])

  useEffect(() => {
    if (!hasBootstrappedCandidatesRef.current) return
    const key = bucketForPage(activePage)
    if (!key) return
    if (bucketMeta[key].loaded) return
    loadCandidateBucket(key, settings.mail_date ?? null, {
      append: false,
      cursor: null,
      limit: INITIAL_BUCKET_LIMIT,
      markRefreshing: true,
    }).catch((e) => setError((e as Error).message))
  }, [activePage, settings.mail_date, bucketMeta.failed.loaded, bucketMeta.needs_review.loaded, bucketMeta.approved_sent.loaded])

  useEffect(() => {
    if (!hasBootstrappedCandidatesRef.current) return
    if (activePage !== 'premium_numbers') return
    loadPremiumNumbers({ append: false, cursor: 0 }).catch((e) => setPremiumError((e as Error).message))
  }, [activePage, premiumConfidenceFilter, premiumScopeFilter, premiumSearch, settings.mail_date, opportunityStatusFilter, opportunitySourceFilter])

  useEffect(() => {
    loadProductivityAnalytics(timeRange).catch((e) => setError((e as Error).message))
  }, [timeRange])

  useEffect(() => {
    trackViewEvent(activePage)
      .catch((e) => setError((e as Error).message))
  }, [activePage])

  useEffect(() => {
    if (!running) return
    let timerId: number | null = null
    const startTime = Date.now()

    const poll = () => {
      if (document.visibilityState === 'visible') {
        loadAiStatus().catch(() => {
          // Keep the run UI stable; the main request will surface actionable errors.
        })
      }
      const elapsedMs = Date.now() - startTime
      const delayMs = elapsedMs <= 10000 ? 1000 : 2500
      timerId = window.setTimeout(poll, delayMs)
    }

    timerId = window.setTimeout(poll, 1000)
    return () => {
      if (timerId !== null) {
        window.clearTimeout(timerId)
      }
    }
  }, [running])

  useEffect(() => {
    return () => {
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (!oauthInProgress) return
    if (oauthPollingStartedAtRef.current === null) {
      oauthPollingStartedAtRef.current = Date.now()
    }
    const intervalId = window.setInterval(() => {
      const pollStartedAt = oauthPollingStartedAtRef.current ?? Date.now()
      if (Date.now() - pollStartedAt >= GMAIL_OAUTH_POLL_TIMEOUT_MS) {
        setOauthInProgress(false)
        oauthPollingStartedAtRef.current = null
        setOauthAuthorizationUrl(null)
        setError('OAuth timed out. Complete Google sign-in and click Connect Gmail again.')
        return
      }
      loadStatus()
        .then((latestStatus) => {
          // Stop polling once Gmail reports authenticated.
          if (latestStatus.authenticated) {
            setOauthInProgress(false)
            oauthPollingStartedAtRef.current = null
            setOauthAuthorizationUrl(null)
            return
          }
          if (!oauthAuthorizationUrl) {
            loadOauthAuthorizationUrl()
              .then((url) => {
                if (url) setOauthAuthorizationUrl(url)
              })
              .catch(() => {
                // Keep polling; URL may not be ready yet.
              })
          }
        })
        .catch(() => {
          // Keep trying quietly while OAuth is in progress.
        })
    }, GMAIL_OAUTH_POLL_INTERVAL_MS)
    return () => window.clearInterval(intervalId)
  }, [oauthAuthorizationUrl, oauthInProgress])

  useEffect(() => {
    if (!oauthInProgress) {
      oauthPollingStartedAtRef.current = null
    }
  }, [oauthInProgress])

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
      await refreshVisibleCandidates(settings.mail_date ?? null, { activeOnly: false })
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

  const runNvoidsSync = async () => {
    setNvoidsRunning(true)
    setError('')
    try {
      const params = new URLSearchParams()
      params.set('batch_limit', String(Math.max(1, Math.min(settings.nvoids_batch_limit || 10, 50))))
      const res = await fetch(`${apiBase}/external-feeds/nvoids/sync?${params.toString()}`, {
        method: 'POST',
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Nvoids sync failed')
      }
      const data = (await res.json()) as { source_type: string; fetched_count: number; created_count: number; deduped_count: number; failed_count: number }
      setLogs((prev) => [
        {
          status: 'ok',
          detail: `nvoids sync complete: fetched=${data.fetched_count} created=${data.created_count} deduped=${data.deduped_count} failed=${data.failed_count}`,
          email_id: null,
        },
        ...prev,
      ].slice(0, RECENT_RUNS_LIMIT))
      await refreshVisibleCandidates(settings.mail_date ?? null, { activeOnly: false })
      await loadPremiumNumbers()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setNvoidsRunning(false)
    }
  }

  const connectGmail = async () => {
    if (oauthInProgress || running) return
    setRunning(true)
    setError('')
    setOauthAuthorizationUrl(null)
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
      if (data.status === 'oauth_in_progress') {
        oauthPollingStartedAtRef.current = Date.now()
        setOauthInProgress(true)
        const initialUrl = data.authorization_url ?? null
        setOauthAuthorizationUrl(initialUrl)
        if (!initialUrl) {
          loadOauthAuthorizationUrl()
            .then((url) => {
              if (url) setOauthAuthorizationUrl(url)
            })
            .catch(() => {
              // Polling flow will retry URL lookup.
            })
        }
      } else if (data.status === 'ready') {
        oauthPollingStartedAtRef.current = null
        setOauthInProgress(false)
        setOauthAuthorizationUrl(null)
      }
      if (data.status !== 'ready') {
        setError(data.detail)
      }
      await Promise.all([loadStatus(), loadAiStatus(), loadTelegramStatus()])
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
      schedulePostMutationRefresh()
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
      schedulePostMutationRefresh()
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
      schedulePostMutationRefresh()
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
  const embeddingLastDuration = aiStatus?.embedding_last_duration_ms
    ? `${(aiStatus.embedding_last_duration_ms / 1000).toFixed(1)}s`
    : null
  const trendBars = useMemo<ProductivityBarPoint[]>(
    () => (productivityTrend?.bars ?? []),
    [productivityTrend?.bars],
  )
  const latestScore = productivityTrend?.kpi_total_sent ?? trendBars.reduce((sum, bar) => sum + bar.sent_count, 0)
  const trendDelta = productivityTrend?.trend_delta_pct ?? 0
  const liveDirection = productivityTrend?.trend_direction === 'down' ? 'down' : (productivityTrend?.trend_direction ?? 'flat')
  const realtimeSignals = useMemo(
    () =>
      productivityEvents.slice(0, 8).map((event) => {
        const when = new Date(event.occurred_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        return `${when} ${event.event_type.replaceAll('_', ' ')}`
      }),
    [productivityEvents],
  )
  const visibleBars = useMemo(() => [...trendBars].reverse(), [trendBars])
  const maxSentInBars = useMemo(() => Math.max(1, ...visibleBars.map((bar) => bar.sent_count)), [visibleBars])

  const formatBucketLabel = (timestamp: string, range: TimeRangeKey) => {
    const dt = new Date(timestamp)
    if (range === 'last_1h' || range === 'current_day') {
      return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    }
    if (range === 'current_week') {
      return dt.toLocaleDateString([], { weekday: 'short', day: 'numeric' })
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

  const moveToFailedMapping = async (candidateId: number) => {
    setMovingToFailedId(candidateId)
    setError('')
    try {
      const res = await fetch(`${apiBase}/candidates/${candidateId}/send-to-failed-mapping`, {
        method: 'POST',
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Failed to move candidate to failed mapping')
      }
      schedulePostMutationRefresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setMovingToFailedId(null)
    }
  }

  const updateSavedQueries = async (nextSavedQueries: string[]) => {
    const nextSettings = withSavedQueries(settings, nextSavedQueries)
    setSettings(nextSettings)
    const res = await fetch(`${apiBase}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(nextSettings),
    })
    if (!res.ok) {
      setError('Failed to save query bucket')
      return
    }
    await loadSettings()
  }

  const addEmployerDomainChip = (raw: string) => {
    const result = addEmployerDomain(settings.employer_domains, raw)
    if (result.error) {
      setEmployerDomainError(result.error)
      return
    }
    setEmployerDomainError('')
    setSettings({ ...settings, employer_domains: result.next })
    setEmployerDomainDraft('')
  }

  const removeEmployerDomainChip = (domainToRemove: string) => {
    setEmployerDomainError('')
    setSettings({
      ...settings,
      employer_domains: removeEmployerDomain(settings.employer_domains, domainToRemove),
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
        premiumCount={numberReviewCards.length}
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
              className="btnMuted"
              onClick={runNvoidsSync}
              disabled={nvoidsRunning || running || !settings.feature_nvoids_enabled}
            >
              {nvoidsRunning ? 'Nvoids Syncing...' : 'Sync Nvoids'}
            </button>
            <button
              type="button"
              className="btnPrimary"
              onClick={status?.authenticated ? runAutomation : connectGmail}
              disabled={running || oauthInProgress}
            >
              {running ? 'Running...' : status?.authenticated ? 'Sync Now' : oauthInProgress ? 'OAuth In Progress...' : 'Connect Gmail'}
            </button>
            {oauthInProgress && oauthAuthorizationUrl ? (
              <a href={oauthAuthorizationUrl} target="_blank" rel="noreferrer" className="btnMuted">
                Open OAuth URL
              </a>
            ) : null}
            {oauthInProgress ? (
              <button type="button" className="btnMuted" onClick={() => fetchAndOpenOauthUrl().catch(() => {
                setError('Could not fetch OAuth URL. Please try again.')
              })}>
                {oauthAuthorizationUrl ? 'Refresh OAuth URL' : 'Get OAuth URL'}
              </button>
            ) : null}
            {oauthInProgress && oauthAuthorizationUrl ? (
              <button
                type="button"
                className="btnMuted"
                onClick={() => {
                  navigator.clipboard.writeText(oauthAuthorizationUrl).catch(() => {
                    setError('Could not copy OAuth URL. Please open it directly.')
                  })
                }}
              >
                Copy OAuth URL
              </button>
            ) : null}
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
            <QueryBucket
              queryValue={settings.gmail_query}
              savedQueries={settings.saved_gmail_queries}
              onQueryChange={(value) => setSettings({ ...settings, gmail_query: value })}
              onQuerySelect={(value) => setSettings({ ...settings, gmail_query: value })}
              onSavedQueriesChange={updateSavedQueries}
            />
            <button
              type="button"
              className="syncBtn topBarAction"
              onClick={status?.authenticated ? runAutomation : connectGmail}
              disabled={running || oauthInProgress}
            >
              {running ? 'Running...' : status?.authenticated ? 'Sync + Queue' : oauthInProgress ? 'OAuth In Progress...' : 'Connect Gmail'}
            </button>
            <button
              type="button"
              className="syncBtn topBarAction"
              onClick={runNvoidsSync}
              disabled={nvoidsRunning || running || !settings.feature_nvoids_enabled}
            >
              {nvoidsRunning ? 'Syncing Nvoids...' : 'Sync + Queue Nvoids'}
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
                    <option value="current_week">Current week</option>
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
                  <div className="row"><span className="label">Embedding</span><span className="dotOk">{typeof aiStatus?.embedding_runtime_healthy === 'boolean' ? (aiStatus.embedding_runtime_healthy ? 'Healthy' : 'Disconnected') : typeof aiStatus?.embedding_connected === 'boolean' ? (aiStatus.embedding_connected ? 'Healthy' : 'Unknown') : 'Unknown'}{aiStatus?.embedding_provider ? ` (${aiStatus.embedding_provider}${aiStatus.embedding_model ? ` / ${aiStatus.embedding_model}` : ''})` : ''}</span></div>
                  <div className="row"><span className="label">Embedding Config</span><span>{typeof aiStatus?.embedding_configured === 'boolean' ? (aiStatus.embedding_configured ? 'Configured' : 'Missing setup') : 'Unknown'}</span></div>
                  {aiStatus?.embedding_last_error ? <div className="row"><span className="label">Embedding Error</span><span>{aiStatus.embedding_last_error}</span></div> : null}
                  {aiStatus?.embedding_last_success_at ? <div className="row"><span className="label">Embedding Last Success</span><span>{aiStatus.embedding_last_success_at}</span></div> : null}
                  {embeddingLastDuration ? <div className="row"><span className="label">Embedding Duration</span><span>{embeddingLastDuration}</span></div> : null}
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
                  <label className="toggleRow">
                    <span>Enable Semantic Matching</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_semantic_enabled}
                        onChange={(e) => setSettings({ ...settings, feature_semantic_enabled: e.target.checked })}
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
                <h2>Employer Domains</h2>
                <div className="stack">
                  <label>
                    Employer Domains (exact domain match)
                    <div className="skillBox">
                      {settings.employer_domains.map((domain) => (
                        <span key={domain} className="skillChip">
                          {domain}
                          <button
                            type="button"
                            className="chipRemove"
                            onClick={() => removeEmployerDomainChip(domain)}
                            aria-label={`Remove ${domain}`}
                            title={`Remove ${domain}`}
                          >
                            x
                          </button>
                        </span>
                      ))}
                      <input
                        value={employerDomainDraft}
                        className="skillInput"
                        onChange={(e) => {
                          setEmployerDomainDraft(e.target.value)
                          if (employerDomainError) setEmployerDomainError('')
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ',') {
                            e.preventDefault()
                            addEmployerDomainChip(employerDomainDraft)
                          } else if (e.key === 'Backspace' && !employerDomainDraft && settings.employer_domains.length > 0) {
                            removeEmployerDomainChip(settings.employer_domains[settings.employer_domains.length - 1])
                          }
                        }}
                        onBlur={() => addEmployerDomainChip(employerDomainDraft)}
                        placeholder="Add domain..."
                      />
                    </div>
                  </label>
                  {employerDomainError ? <p className="subtle">{employerDomainError}</p> : null}
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
                  <label className="toggleRow pillRow">
                    <span>Auto Send Current Run Queue</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_auto_send}
                        onChange={(e) => setSettings({ ...settings, feature_auto_send: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <p className="subtle">Auto-send only candidates queued in the current run.</p>
                  <label className="toggleRow pillRow">
                    <span>Retry Failed Queue</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_retry_queue}
                        onChange={(e) => setSettings({ ...settings, feature_retry_queue: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <p className="subtle">Retry failed candidates and promote sendable ones to Needs Review.</p>
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

              <section className="card">
                <h2>Nvoids Control</h2>
                <div className="stack">
                  <label className="toggleRow pillRow">
                    <span>Enable Nvoids Pipeline</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_nvoids_enabled}
                        onChange={(e) => setSettings({ ...settings, feature_nvoids_enabled: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <label className="toggleRow pillRow">
                    <span>Auto Sync Nvoids</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_nvoids_auto_sync}
                        onChange={(e) => setSettings({ ...settings, feature_nvoids_auto_sync: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <label>
                    Nvoids Batch Limit (per run)
                    <input
                      type="number"
                      min={1}
                      max={50}
                      value={settings.nvoids_batch_limit}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          nvoids_batch_limit: Math.max(1, Math.min(Number(e.target.value) || 10, 50)),
                        })
                      }
                    />
                  </label>
                  <label>
                    Nvoids Auto Sync Interval (minutes)
                    <input
                      type="number"
                      min={1}
                      max={1440}
                      value={settings.feature_nvoids_poll_interval_minutes}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          feature_nvoids_poll_interval_minutes: Math.max(1, Math.min(Number(e.target.value) || 30, 1440)),
                        })
                      }
                    />
                  </label>
                  <button
                    type="button"
                    onClick={runNvoidsSync}
                    disabled={nvoidsRunning || running || !settings.feature_nvoids_enabled}
                  >
                    {nvoidsRunning ? 'Running Nvoids Sync...' : 'Run Nvoids Sync Now'}
                  </button>
                  <p className="subtle">
                    Nvoids sync is isolated from Gmail run queue and serialized to avoid concurrent DB load.
                  </p>
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
            const effectiveDraft = draftEdits[item.id] ?? item.draft_reply
            const routingTrusted = canTrustRouting(item)
            const verdict = getOverallVerdict(item, effectiveDraft, routingTrusted)
            const requiresResumeForApproval = item.source === 'gmail'
            const canApprove =
              Boolean(item.recipient_email) &&
              Boolean(item.cc_email) &&
              Boolean(effectiveDraft?.trim()) &&
              (!requiresResumeForApproval || Boolean(item.resume_file_name)) &&
              routingTrusted
            return (
              <article key={item.id} className="emailItem">
                <p><strong>Email ID:</strong> {item.id}</p>
                <p><strong>From:</strong> {item.sender}</p>
                <p><strong>Subject:</strong> {item.subject}</p>
                {sourceListingUrl(item) ? (
                  <p>
                    <strong>Source Listing:</strong>{' '}
                    <a href={sourceListingUrl(item)!} target="_blank" rel="noreferrer">
                      Open source listing
                    </a>
                  </p>
                ) : null}
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
                <p><strong>Resume Context:</strong> {getResumeContextLabel(item.draft_resume_context_status)}</p>
                {item.draft_ai_error ? <p className="subtle"><strong>AI fallback:</strong> {item.draft_ai_error}</p> : null}
                <p><strong>Draft:</strong></p>
                <div className="draftUnified">
                  <label className="draftPaneLabel">Editable Draft</label>
                  <textarea
                    value={effectiveDraft}
                    rows={10}
                    onChange={(e) => setDraftEdits((prev) => ({ ...prev, [item.id]: e.target.value }))}
                  />
                  <label className="draftPaneLabel">Live Preview</label>
                  <div
                    className="draftPreview"
                    dangerouslySetInnerHTML={{ __html: draftToPreviewHtml(effectiveDraft) }}
                  />
                </div>
                {item.last_error ? <p className="errorMessage"><strong>Last Error:</strong> {item.last_error}</p> : null}
                <div className="rowBtns">
                  <button
                    type="button"
                    onClick={() => approveSend(item)}
                    disabled={!canApprove || sendingId === item.id}
                    title={
                      !canApprove
                        ? requiresResumeForApproval
                          ? 'Safe routing, To, CC, body, and resume are required before send'
                          : 'Safe routing, To, CC, and body are required before approval'
                        : requiresResumeForApproval
                          ? 'Approve and send'
                          : 'Approve candidate'
                    }
                  >
                    {sendingId === item.id ? 'Sending...' : requiresResumeForApproval ? 'Approve & Send' : 'Approve'}
                  </button>
                  <button
                    type="button"
                    onClick={() => rejectSend(item.id)}
                    disabled={rejectingId === item.id}
                  >
                    {rejectingId === item.id ? 'Rejecting...' : 'Reject'}
                  </button>
                  <button
                    type="button"
                    onClick={() => moveToFailedMapping(item.id)}
                    disabled={movingToFailedId === item.id}
                    title="Move to Failed Mapping so recipients can be remapped"
                  >
                    {movingToFailedId === item.id ? 'Moving...' : 'Send to Failed Mapping'}
                  </button>
                  <span className={`verdictBadge verdict-${verdict.tone}`} title="Overall Verdict">
                    {verdict.label} • {verdict.score}
                  </span>
                </div>
              </article>
            )
          })}
          {bucketMeta.needs_review.hasNext ? (
            <button
              type="button"
              onClick={() => loadMoreCandidates('needs_review', settings.mail_date ?? null)}
              disabled={loadingMoreKey === 'needs_review'}
            >
              {loadingMoreKey === 'needs_review' ? 'Loading...' : 'Load More'}
            </button>
          ) : null}
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
          {bucketMeta.failed.hasNext ? (
            <button
              type="button"
              onClick={() => loadMoreCandidates('failed', settings.mail_date ?? null)}
              disabled={loadingMoreKey === 'failed'}
            >
              {loadingMoreKey === 'failed' ? 'Loading...' : 'Load More'}
            </button>
          ) : null}
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
              {item.auto_sent_count != null || item.auto_send_failed_count != null || item.retry_promoted_count != null || item.retry_skipped_count != null ? (
                <div className="automationMetrics">
                  <strong>Automation:</strong>
                  {item.auto_sent_count != null ? <span className="tag">Auto Sent: {item.auto_sent_count}</span> : null}
                  {item.auto_send_failed_count != null ? <span className="tag">Auto Send Failed: {item.auto_send_failed_count}</span> : null}
                  {item.retry_promoted_count != null ? <span className="tag">Retry Promoted: {item.retry_promoted_count}</span> : null}
                  {item.retry_skipped_count != null ? <span className="tag">Retry Skipped: {item.retry_skipped_count}</span> : null}
                </div>
              ) : null}
            </article>
          ))}
            </section>
          ) : null}

          {activePage === 'premium_numbers' ? (
            <section className="card pageSection">
              <h2>Premium Numbers</h2>
              <div className="actionBar">
                <select
                  value={premiumScopeFilter}
                  onChange={(e) =>
                    setPremiumScopeFilter(
                      e.target.value as 'all_review' | 'recruiter_numbers' | 'employer_numbers' | 'recruiter_opportunities',
                    )
                  }
                >
                  <option value="all_review">All</option>
                  <option value="recruiter_numbers">Recruiter Numbers</option>
                  <option value="employer_numbers">Employer Numbers</option>
                  <option value="recruiter_opportunities">Recruiter Opportunities</option>
                </select>
                {premiumScopeFilter === 'recruiter_opportunities' ? (
                  <>
                    <select
                      value={opportunityStatusFilter}
                      onChange={(e) => setOpportunityStatusFilter(e.target.value as 'all' | OpportunityStatus)}
                    >
                      <option value="all">All statuses</option>
                      <option value="New">New</option>
                      <option value="Called">Called</option>
                      <option value="Applied">Applied</option>
                      <option value="Follow Up">Follow Up</option>
                      <option value="Closed">Closed</option>
                      <option value="Not Interested">Not Interested</option>
                    </select>
                    <select
                      value={opportunitySourceFilter}
                      onChange={(e) => setOpportunitySourceFilter(e.target.value as 'all' | 'gmail' | 'nvoids')}
                    >
                      <option value="all">All sources</option>
                      <option value="gmail">Gmail</option>
                      <option value="nvoids">Nvoids</option>
                    </select>
                  </>
                ) : null}
                <input
                  value={premiumSearch}
                  onChange={(e) => setPremiumSearch(e.target.value)}
                  placeholder="Search number, owner, company..."
                />
              </div>
              {premiumLoading ? <p className="subtle">Loading premium numbers...</p> : null}
              {premiumError ? <p className="subtle">Premium numbers error: {premiumError}</p> : null}
              {premiumScopeFilter === 'all_review' && !premiumLoading && numberReviewCards.length === 0 ? (
                <p className="subtle">No unknown numbers pending review.</p>
              ) : null}
              {premiumScopeFilter === 'all_review'
                ? numberReviewCards.map((item) => (
                    <article key={`review-${item.id}`} className="emailItem">
                      <p><strong>Phone:</strong> {item.display_phone_number}</p>
                      <p><strong>Owner:</strong> {item.owner_name}</p>
                      <p><strong>Company:</strong> {item.company}</p>
                      <p><strong>Designation:</strong> {item.designation}</p>
                      <p><strong>Confidence:</strong> {item.confidence.toUpperCase()}</p>
                      <p><strong>Purpose:</strong> {item.purpose}</p>
                      <p><strong>Email Sender:</strong> {item.email_sender}</p>
                      <p><strong>Email Subject:</strong> {item.email_subject}</p>
                      {item.gmail_open_url ? (
                        <p><strong>Open:</strong> <a href={item.gmail_open_url} target="_blank" rel="noreferrer">Open exact email in Gmail</a></p>
                      ) : null}
                      <p className="subtle"><strong>Evidence:</strong> {item.evidence_snippet}</p>
                      <div className="rowBtns">
                        <button
                          type="button"
                          onClick={() => markReviewCard(item.id, 'recruiter')}
                          disabled={classifyingReviewId === item.id}
                        >
                          Mark as Recruiter
                        </button>
                        <button
                          type="button"
                          onClick={() => markReviewCard(item.id, 'employer')}
                          disabled={classifyingReviewId === item.id}
                        >
                          Mark as Employer
                        </button>
                        <button
                          type="button"
                          onClick={() => deleteReviewCard(item.id)}
                          disabled={classifyingReviewId === item.id}
                        >
                          Delete
                        </button>
                      </div>
                    </article>
                  ))
                : null}

              {premiumScopeFilter === 'recruiter_numbers' && !premiumLoading && recruiterNumberCards.length === 0 ? (
                <p className="subtle">No recruiter numbers found.</p>
              ) : null}
              {premiumScopeFilter === 'recruiter_numbers'
                ? recruiterNumberCards.map((item) => (
                    <article key={`recruiter-number-${item.id}`} className="emailItem">
                      <p><strong>Recruiter:</strong> {item.recruiter_name}</p>
                      <p><strong>Phone:</strong> {item.display_phone_number}</p>
                      <p><strong>Company:</strong> {item.company}</p>
                      <p><strong>Designation:</strong> {item.designation}</p>
                      <p><strong>Recruiter Email:</strong> {item.recruiter_email || '-'}</p>
                      <p><strong>Total Opportunities:</strong> {item.total_opportunity_count}</p>
                      <p><strong>Last Email:</strong> {item.last_email_received_at ? new Date(item.last_email_received_at).toLocaleString() : '-'}</p>
                      <div className="rowBtns">
                        <button
                          type="button"
                          onClick={() => swapNumberBucket(item.id, 'recruiter')}
                          disabled={classifyingReviewId === item.id}
                        >
                          Swap to Employer
                        </button>
                      </div>
                    </article>
                  ))
                : null}

              {premiumScopeFilter === 'employer_numbers' && !premiumLoading && employerNumberCards.length === 0 ? (
                <p className="subtle">No employer numbers found.</p>
              ) : null}
              {premiumScopeFilter === 'employer_numbers'
                ? employerNumberCards.map((item) => (
                    <article key={`employer-number-${item.id}`} className="emailItem">
                      <p><strong>Phone:</strong> {item.display_phone_number}</p>
                      <p><strong>Owner:</strong> {item.owner_name}</p>
                      <p><strong>Company:</strong> {item.company}</p>
                      <p><strong>Source Email ID:</strong> {item.source_email_id ?? '-'}</p>
                      <div className="rowBtns">
                        <button
                          type="button"
                          onClick={() => swapNumberBucket(item.id, 'employer')}
                          disabled={classifyingReviewId === item.id}
                        >
                          Swap to Recruiter
                        </button>
                      </div>
                    </article>
                  ))
                : null}

              {premiumScopeFilter === 'recruiter_opportunities' && !premiumLoading && opportunityCards.length === 0 ? (
                <p className="subtle">No recruiter opportunities found.</p>
              ) : null}
              {premiumScopeFilter === 'recruiter_opportunities'
                ? opportunityCards.map((item) => (
                    <article key={`opportunity-${item.id}`} className="emailItem">
                      <p><strong>Subject:</strong> {item.email_subject}</p>
                      <p><strong>Source:</strong> {(item.source_type || 'gmail').toUpperCase()}</p>
                      <p><strong>Recruiter Name:</strong> {item.recruiter_name || '-'}</p>
                      <p><strong>Recruiter Email:</strong> {item.recruiter_email || '-'}</p>
                      <p><strong>Recruiter Phone:</strong> {item.recruiter_phone_display || '-'}</p>
                      <p><strong>Email Sender:</strong> {item.email_sender || '-'}</p>
                      <p><strong>Job Title:</strong> {item.job_title || '-'}</p>
                      <p><strong>Client:</strong> {item.client || '-'}</p>
                      <p><strong>Location:</strong> {item.location || '-'}</p>
                      <p><strong>Work Mode:</strong> {item.work_mode || '-'}</p>
                      <p><strong>Visa:</strong> {item.visa_restrictions || '-'}</p>
                      <p><strong>Skills:</strong> {item.extracted_skills || '-'}</p>
                      {item.source_url || item.gmail_open_url ? (
                        <p>
                          <strong>Open:</strong>{' '}
                          <a href={item.source_url || item.gmail_open_url} target="_blank" rel="noreferrer">
                            {item.source_type === 'nvoids' ? 'Open Original Post' : 'Open exact email in Gmail'}
                          </a>
                        </p>
                      ) : null}
                      <label>
                        Status
                        <select
                          value={item.status}
                          onChange={(e) => updateOpportunity(item.id, { status: e.target.value as OpportunityStatus })}
                          disabled={updatingOpportunityId === item.id}
                        >
                          <option value="New">New</option>
                          <option value="Called">Called</option>
                          <option value="Applied">Applied</option>
                          <option value="Follow Up">Follow Up</option>
                          <option value="Closed">Closed</option>
                          <option value="Not Interested">Not Interested</option>
                        </select>
                      </label>
                      <label>
                        Notes
                        <textarea
                          value={item.notes || ''}
                          rows={3}
                          onChange={(e) =>
                            setOpportunityCards((prev) =>
                              prev.map((entry) => (entry.id === item.id ? { ...entry, notes: e.target.value } : entry)),
                            )
                          }
                          onBlur={(e) => updateOpportunity(item.id, { notes: e.target.value })}
                          disabled={updatingOpportunityId === item.id}
                        />
                      </label>
                      <div className="rowBtns">
                        <button
                          type="button"
                          onClick={() => generateColdCallScript(item.id)}
                          disabled={generatingColdCallId === item.id || deletingOpportunityId === item.id}
                        >
                          {generatingColdCallId === item.id ? 'Generating...' : 'Generate Cold Call Script'}
                        </button>
                        <button
                          type="button"
                          onClick={() => deleteOpportunity(item.id)}
                          disabled={deletingOpportunityId === item.id}
                        >
                          {deletingOpportunityId === item.id ? 'Deleting...' : 'Delete'}
                        </button>
                        {item.cold_call_script ? (
                          <button
                            type="button"
                            onClick={() => {
                              navigator.clipboard.writeText(item.cold_call_script || '').catch(() => {
                                setPremiumError('Failed to copy cold call script')
                              })
                            }}
                          >
                            Copy Script
                          </button>
                        ) : null}
                      </div>
                      {item.cold_call_script ? (
                        <label>
                          Cold Call Script
                          <textarea
                            value={item.cold_call_script}
                            rows={4}
                            readOnly
                          />
                        </label>
                      ) : null}
                    </article>
                  ))
                : null}
              {premiumHasNext ? (
                <button
                  type="button"
                  onClick={() => {
                    if (premiumNextCursor == null) return
                    loadPremiumNumbers({ append: true, cursor: premiumNextCursor }).catch((e) =>
                      setPremiumError((e as Error).message),
                    )
                  }}
                  disabled={premiumLoading || premiumNextCursor == null}
                >
                  {premiumLoading ? 'Loading...' : 'Load More'}
                </button>
              ) : null}
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
          {bucketMeta.approved_sent.hasNext ? (
            <button
              type="button"
              onClick={() => loadMoreCandidates('approved_sent', settings.mail_date ?? null)}
              disabled={loadingMoreKey === 'approved_sent'}
            >
              {loadingMoreKey === 'approved_sent' ? 'Loading...' : 'Load More'}
            </button>
          ) : null}
            </section>
          ) : null}
        </div>
      </section>
    </main>
  )
}

export default App
