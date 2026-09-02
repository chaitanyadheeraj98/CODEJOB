import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import './App.css'
import Sidebar, { type SidebarProps } from './components/Sidebar'
import CandidateCard from './components/CandidateCard'
import ResumeTrackingPage from './features/resume_tracking/ResumeTrackingPage'
import TrustedGmailGroupsPanel, { type TrustedGmailGroup } from './features/gmail_groups/TrustedGmailGroupsPanel'
import { getDraftSourceLabel } from './features/ai/ui'
import QueryBucket from './features/query_bucket/QueryBucket'
import EmailSearch from './features/email_search/EmailSearch'
import AssistantPage from './features/chat/AssistantPage'
import ChatProvider from './features/chat/ChatProvider'
import { useChat } from './features/chat/chatContext'
import ChatWidget from './features/chat/ChatWidget'
import { getChatStatus } from './features/chat/api'
import type { ChatStatus } from './features/chat/types'
import PremiumNumbersPage from './features/premium_numbers/PremiumNumbersPage'
import AppTSPage from './features/application_tracking/AppTSPage'
import VerificationBadge from './features/premium_numbers/VerificationBadge'
import { type CandidateState, useCandidateBuckets } from './candidateBuckets'
import type { CandidateQueryOptions } from './candidateBuckets'
import FilterSortBar, { type FilterValues } from './components/FilterSortBar'
import FilterVisibilitySettings from './components/FilterVisibilitySettings'
import SelectionActionBar from './components/SelectionActionBar'
import { filterSortRegistry, resolveRegistryEntry } from './filterSortRegistry'
import { hasActiveTextSearch, narrowValuesToVisible, visibleFieldsFor } from './filterVisibility'
import { focusedCandidateMissing } from './recordFocus'
import { buildUrlSearch, parseFilterValuesFromParams } from './useUrlSync'
import { addCcEmail, removeCcEmail } from './ccEmails'
import { addEmployerDomain, removeEmployerDomain } from './employerDomains'
import { formatRelativeInboxTime, getInitials } from './inboxFormat'
import type { EmailSearchHit } from './emailSearch'

const GMAIL_OAUTH_POLL_INTERVAL_MS = 2000
const GMAIL_OAUTH_POLL_TIMEOUT_MS = 180000
const VIEW_EVENT_THROTTLE_MS = 60000
const SETTINGS_REVIEW_BATCH_SIZE = 50

function emailSearchRelatedId(hit: EmailSearchHit): string | null {
  if (hit.section === 'inbox') {
    return hit.detail.conversation_id == null ? null : String(hit.detail.conversation_id)
  }
  if (hit.section === 'recent_runs') {
    const related = hit.detail.run_key ?? hit.detail.recent_run_skipped_item_id
    return related == null ? null : String(related)
  }
  if (hit.section === 'premium_numbers') {
    const related =
      hit.detail.number_review_id ??
      hit.detail.recruiter_number_id ??
      hit.detail.employer_number_id ??
      hit.detail.recruiter_opportunity_id ??
      hit.detail.contact_id
    return related == null ? null : String(related)
  }
  return hit.recruiter_email_id == null ? null : String(hit.recruiter_email_id)
}

export const DRAFT_TEXT_SIZE_OPTIONS = ['small', 'normal', 'large', 'huge'] as const
export type DraftTextSize = (typeof DRAFT_TEXT_SIZE_OPTIONS)[number]
type NvoidsDetailTitleMode = 'job_details' | 'hotlist_details' | 'all'
const DRAFT_TEXT_SIZE_STYLES: Record<DraftTextSize, { fontSize: string; lineHeight: string }> = {
  small: { fontSize: '12px', lineHeight: '1.5' },
  normal: { fontSize: '16px', lineHeight: '1.5' },
  large: { fontSize: '20px', lineHeight: '1.5' },
  huge: { fontSize: '28px', lineHeight: '1.4' },
}

type ActivePage = 'assistant' | 'run_queue' | 'needs_review' | 'failed_mapping' | 'recent_runs' | 'sent_items' | 'inbox' | 'premium_numbers' | 'resume_tracking' | 'application_tracking' | 'settings'
const ACTIVE_PAGES = new Set<ActivePage>(['assistant', 'run_queue', 'needs_review', 'failed_mapping', 'recent_runs', 'sent_items', 'inbox', 'premium_numbers', 'resume_tracking', 'application_tracking', 'settings'])
const initialActivePage = (): ActivePage => {
  const page = new URLSearchParams(window.location.search).get('page') as ActivePage | null
  return page && ACTIVE_PAGES.has(page) ? page : 'run_queue'
}

const PAGE_TITLES: Record<ActivePage, string> = {
  assistant: 'CodeJob Assistant',
  run_queue: 'Run Queue Dashboard',
  needs_review: 'Needs Review',
  failed_mapping: 'Failed Mapping',
  recent_runs: 'Recent Runs',
  sent_items: 'Sent Items',
  inbox: 'Reply Inbox',
  premium_numbers: 'Premium Numbers',
  resume_tracking: 'Resume Tracking',
  application_tracking: 'Application Tracking',
  settings: 'Settings',
}

const PAGE_SUBTITLES: Record<ActivePage, string> = {
  assistant: 'Ask about your pipeline, analyse it, and hand off the work.',
  run_queue: 'Manage and monitor your automated recruitment email operations.',
  needs_review: 'Approve, edit, or reject AI-drafted replies before they send.',
  failed_mapping: 'Fix recipient routing for emails the parser could not map.',
  recent_runs: 'See automation run history and outcomes.',
  sent_items: 'Review emails that have already been sent.',
  inbox: 'Review recruiter replies and continue Gmail conversations.',
  premium_numbers: 'Manage inventory, assignments, and rescoring operations.',
  resume_tracking: 'See which resume variants move through the funnel and why others stall.',
  application_tracking: 'Review bookmarked requirements and explicitly tracked applications.',
  settings: 'Manage learning queues, trusted Gmail groups, and resume assets.',
}

// App renders ChatProvider inside its own tree, so App cannot call useChat().
// This consumer sits below the provider and is the only thing that needs to.
function SidebarWithAssistantBadge(props: Omit<SidebarProps, 'assistantUnseenCount'>) {
  const { unseenCount } = useChat()
  return <Sidebar {...props} assistantUnseenCount={unseenCount} />
}

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

const isValidEmailAddress = (value: string): boolean => Boolean(value.trim()) && addCcEmail([], value).error === null

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

export function normalizeDraftTextSize(value: string | null | undefined): DraftTextSize {
  const normalized = (value ?? '').trim().toLowerCase()
  return (DRAFT_TEXT_SIZE_OPTIONS as readonly string[]).includes(normalized) ? (normalized as DraftTextSize) : 'normal'
}

function normalizeNvoidsDetailTitleMode(value: string | null | undefined): NvoidsDetailTitleMode {
  const normalized = (value ?? '').trim().toLowerCase()
  return normalized === 'hotlist_details' || normalized === 'all' ? normalized : 'job_details'
}

export function draftTextSizeToPreviewStyle(draftTextSize: string | null | undefined): { fontSize: string; lineHeight: string } {
  return DRAFT_TEXT_SIZE_STYLES[normalizeDraftTextSize(draftTextSize)]
}

export function draftToPreviewHtml(draftText: string): string {
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
  groq_configured?: boolean
  groq_enabled_in_settings?: boolean
  groq_model?: string
  groq_base_url_present?: boolean
  groq_runtime_healthy?: boolean | null
  groq_last_error?: string | null
  groq_detail?: string
  groq_request_mode?: string
  groq_last_attempted_at?: string | null
  groq_last_success_at?: string | null
  groq_last_duration_ms?: number | null
  intent_gate_provider?: string
  intent_gate_model?: string
  intent_gate_configured?: boolean
  intent_gate_enabled_in_settings?: boolean
  intent_gate_runtime_healthy?: boolean | null
  intent_gate_last_error?: string | null
  intent_gate_detail?: string
  intent_gate_effort_ladder?: string
  intent_gate_last_rung?: string
  intent_gate_min_taxonomy_confidence?: number
  intent_gate_last_attempted_at?: string | null
  intent_gate_last_success_at?: string | null
  intent_gate_last_duration_ms?: number | null
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
  nvoids_detail_title_mode: NvoidsDetailTitleMode
  nvoids_locations: string[]
  nvoids_job_role: string
  nvoids_search_location: string
  nvoids_custom_query: string
  feature_auto_send: boolean
  feature_retry_queue: boolean
  feature_ai_enabled: boolean
  feature_ai_extractor_enabled: boolean
  feature_semantic_enabled: boolean
  feature_groq_job_parser_enabled: boolean
  feature_gmail_requirement_groups_enabled: boolean
  feature_role_manifest_enabled: boolean
  feature_strict_candidate_screening_enabled: boolean
  feature_email_tracking_enabled: boolean
  feature_reply_inbox_enabled: boolean
  feature_applications_enabled: boolean
  feature_application_automation_enabled: boolean
  feature_application_outreach_drafts_enabled: boolean
  feature_reminder_sweep_interval_minutes: number
  feature_resume_tracking_enabled: boolean
  feature_resume_tracking_sweep_interval_minutes: number
  candidate_work_authorizations: string[]
  preferred_employment_types: Array<'C2C' | 'W2' | '1099' | 'FT'>
  visible_filters: Record<string, string[]>
  preferred_minimum_rate: number | null
  candidate_total_experience_years: number | null
  candidate_us_experience_years: number | null
  candidate_current_location: string
  draft_text_size: DraftTextSize
  fallback_draft_template: string
  signature_name: string
  signature_phone: string
  signature_email: string
  preferred_employer_cc_emails: string[]
  default_employer_cc_emails: string[]
  preferred_employer_cc_email?: string
  resume_display_name: string
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
    draft_rules: DraftRules
  }
}

export type RuleMode = 'ignore' | 'warn' | 'block'

export type DraftRule = {
  mode: RuleMode
}

export type AcceptedLocationRule = DraftRule & {
  locations?: string[]
}

export type MinimumSalaryRule = DraftRule & {
  value?: number | null
}

export type MustHaveSkillsRule = DraftRule & {
  skills?: string[]
}

export type ScoreThresholdRule = DraftRule & {
  value?: number | null
}

export type DraftRules = {
  recruiter_like_gmail: DraftRule
  accepted_location: AcceptedLocationRule
  minimum_salary: MinimumSalaryRule
  must_have_skills: MustHaveSkillsRule
  score_threshold: ScoreThresholdRule
  f2f_non_texas: DraftRule
  unknown_location: DraftRule
  recipient_mapping: DraftRule
}

type PolicyProfileName = 'Flexible Drafting' | 'Balanced' | 'Strict'

export const defaultDraftRules = (): DraftRules => ({
  recruiter_like_gmail: { mode: 'block' },
  accepted_location: { mode: 'block', locations: [] },
  minimum_salary: { mode: 'block', value: null },
  must_have_skills: { mode: 'block', skills: [] },
  score_threshold: { mode: 'block', value: null },
  f2f_non_texas: { mode: 'block' },
  unknown_location: { mode: 'block' },
  recipient_mapping: { mode: 'block' },
})

export const buildDefaultPolicy = (): DynamicPolicy => ({
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
    draft_rules: defaultDraftRules(),
  },
})

type PolicySeed = Pick<SettingsPayload, 'accepted_locations' | 'min_salary' | 'must_have_skills' | 'qualification_threshold'> | undefined

export function normalizeDynamicPolicy(policy?: DynamicPolicy | null, seed?: PolicySeed): DynamicPolicy {
  const defaultPolicy = buildDefaultPolicy()
  const qualification = policy?.qualification
  const ruleSeed = seed ?? {
    accepted_locations: [],
    min_salary: null,
    must_have_skills: [],
    qualification_threshold: 0.6,
  }
  const rawQualification = (qualification ?? {}) as Record<string, unknown>
  const legacyDraftFilters = (rawQualification.draft_filters ?? {}) as Record<string, boolean>
  const rawDraftRules = (rawQualification.draft_rules ?? {}) as Record<string, unknown>
  const legacyMode = (key: string): RuleMode => (legacyDraftFilters[key] === false ? 'warn' : 'block')
  const normalizedDraftRules: DraftRules = {
    recruiter_like_gmail: {
      mode: ((rawDraftRules.recruiter_like_gmail as DraftRule | undefined)?.mode ?? legacyMode('recruiter_like_filter_enabled')) as RuleMode,
    },
    accepted_location: {
      mode: ((rawDraftRules.accepted_location as AcceptedLocationRule | undefined)?.mode ?? legacyMode('accepted_location_filter_enabled')) as RuleMode,
      locations: (rawDraftRules.accepted_location as AcceptedLocationRule | undefined)?.locations ?? ruleSeed.accepted_locations ?? [],
    },
    minimum_salary: {
      mode: ((rawDraftRules.minimum_salary as MinimumSalaryRule | undefined)?.mode ?? legacyMode('minimum_salary_filter_enabled')) as RuleMode,
      value: (rawDraftRules.minimum_salary as MinimumSalaryRule | undefined)?.value ?? ruleSeed.min_salary ?? null,
    },
    must_have_skills: {
      mode: ((rawDraftRules.must_have_skills as MustHaveSkillsRule | undefined)?.mode ?? legacyMode('must_have_skills_filter_enabled')) as RuleMode,
      skills: (rawDraftRules.must_have_skills as MustHaveSkillsRule | undefined)?.skills ?? ruleSeed.must_have_skills ?? [],
    },
    score_threshold: {
      mode: ((rawDraftRules.score_threshold as ScoreThresholdRule | undefined)?.mode ?? legacyMode('score_threshold_filter_enabled')) as RuleMode,
      value: (rawDraftRules.score_threshold as ScoreThresholdRule | undefined)?.value ?? ruleSeed.qualification_threshold ?? 0.6,
    },
    f2f_non_texas: {
      mode: ((rawDraftRules.f2f_non_texas as DraftRule | undefined)?.mode ?? legacyMode('f2f_non_texas_filter_enabled')) as RuleMode,
    },
    unknown_location: {
      mode: ((rawDraftRules.unknown_location as DraftRule | undefined)?.mode ?? legacyMode('strict_unknown_location_filter_enabled')) as RuleMode,
    },
    recipient_mapping: {
      mode: ((rawDraftRules.recipient_mapping as DraftRule | undefined)?.mode ?? legacyMode('require_to_and_cc_before_draft_enabled')) as RuleMode,
    },
  }
  return {
    ...defaultPolicy,
    ...(policy ?? {}),
    query: {
      ...defaultPolicy.query,
      ...(policy?.query ?? {}),
    },
    run: {
      ...defaultPolicy.run,
      ...(policy?.run ?? {}),
    },
    qualification: {
      ...defaultPolicy.qualification,
      ...(qualification ?? {}),
      draft_rules: normalizedDraftRules,
    },
  }
}

type AutomationRunResponse = {
  status: string
  detail: string
  run_key?: string | null
  run_source?: string | null
  skipped_item_count?: number | null
  created_at?: string | null
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
  source_count?: number | null
  requirement_count?: number | null
  multi_role_source_count?: number | null
  manifest_review_count?: number | null
}

type RecentRunItem = {
  id: number
  run_key: string
  run_source: string
  source_type: string
  outcome: string
  reason_code: string
  reason_detail: string
  external_message_id?: string | null
  external_thread_id?: string | null
  candidate_email_id?: number | null
  external_opportunity_id?: number | null
  title_or_subject: string
  sender: string
  location?: string | null
  source_url?: string | null
  gmail_message_url?: string | null
  intent_type?: string | null
  intent_confidence?: number | null
  intent_reason?: string | null
  intent_evidence: string[]
  intent_negative_evidence: string[]
  gate_action?: string | null
  gate_provider?: string | null
  source_group_name?: string | null
  source_group_email?: string | null
  source_group_match_method?: string | null
  source_group_trusted?: boolean | null
  qualification_result?: string | null
  blocking_rule?: string | null
  qualification_detail?: string | null
  qualification_context?: Record<string, unknown> | null
  created_at: string
}

type RecentRunCard = AutomationRunResponse & {
  run_key?: string | null
  run_source?: string | null
  skipped_item_count?: number | null
  created_at?: string | null
  skipped_items?: RecentRunItem[]
  skipped_items_loaded?: boolean
  skipped_items_loading?: boolean
  skipped_items_error?: string | null
  selected_skipped_ids?: number[]
  retrying_skipped?: boolean
  retry_error?: string | null
}

type BackgroundJob = {
  run_key: string
  job_id: string | null
  status: string
  detail: string
  processed_items: number
  total_items: number | null
  progress_pct: number | null
  queue_name: string | null
  skipped_item_count?: number | null
  failed_count?: number | null
}

type JobEnqueueResponse = {
  run_key: string
  job_id: string
  status: string
}

type RecentRunListResponse = {
  items: RecentRunCard[]
  next_cursor?: number | null
  has_next?: boolean
}

type RecentRunItemListResponse = {
  items: RecentRunItem[]
  next_cursor: number | null
  has_next: boolean
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
  skills_text: string
  primary_role: string
  structured_skills: string[]
  variant_label: string
  is_enabled: boolean
  is_current: boolean
  created_at: string
  updated_at: string
}

type AttachmentAsset = {
  id: number
  file_name: string
  mime_type: string
  sha256: string
  file_size: number
  is_enabled: boolean
  created_at: string
  updated_at: string
}

type PendingSkill = {
  skill_name: string
  normalized_name: string
  occurrence_count: number
  candidate_ids: number[]
  suspicious: boolean
  recoverable_skills: string[]
  source_tags: string[]
}

type PendingEntity = {
  entity_type: 'company' | 'location' | 'role'
  display_name: string
  normalized_name: string
  occurrence_count: number
  candidate_ids: number[]
}

type EmbedPendingSkillsResponse = {
  embedded_count: number
  remaining_count: number
  duration_ms: number
}

type JobIntentLearningSignal = {
  id: number
  owner_id: string
  phrase: string
  normalized_phrase: string
  polarity: string
  source_examples_count: number
  sample_evidence: string[]
  confidence_aggregate: number
  last_intent_type?: string | null
  status: string
  created_at: string
  updated_at: string
}

type EmbeddedJobIntentSignal = {
  id: number
  phrase: string
  polarity: string
  confidence: number
  embedded: boolean
}

type SettingsBootstrapPayload = {
  settings: SettingsPayload
  role_manifest_child_creation_enabled: boolean
  gmail_requirement_groups: TrustedGmailGroup[]
  resumes: ResumeAsset[]
  attachments: AttachmentAsset[]
  pending_skills: PendingSkill[]
  pending_job_intent_signals: JobIntentLearningSignal[]
  approved_job_intent_signals: JobIntentLearningSignal[]
  loaded_at: string
  owner_id: string
}

type BootstrapStatus = 'idle' | 'loading' | 'ready' | 'error'

type ResumeDatabaseSectionProps = {
  activeResume: ResumeAsset | null
  resumeFile: File | null
  resumeSkillsInput: string
  resumePrimaryRoleInput: string
  resumeStructuredSkillsInput: string
  resumeVariantLabelInput: string
  resumeSkillEdits: Record<number, string>
  resumeMetadataEdits: Record<number, { primary_role: string; structured_skills: string; variant_label: string }>
  resumeAssets: ResumeAsset[]
  resumeUploading: boolean
  focusResumeId: number | null
  setResumeFile: (file: File | null) => void
  setResumeSkillsInput: (value: string) => void
  setResumePrimaryRoleInput: (value: string) => void
  setResumeStructuredSkillsInput: (value: string) => void
  setResumeVariantLabelInput: (value: string) => void
  setResumeSkillEdits: React.Dispatch<React.SetStateAction<Record<number, string>>>
  setResumeMetadataEdits: React.Dispatch<React.SetStateAction<Record<number, { primary_role: string; structured_skills: string; variant_label: string }>>>
  uploadResume: () => void
  saveResumeSkills: (resumeId: number) => void
  toggleResumeAsset: (resumeId: number, isEnabled: boolean) => void
  deleteResumeAsset: (resumeId: number) => void
}

export function ResumeDatabaseSection({
  activeResume,
  resumeFile,
  resumeSkillsInput,
  resumePrimaryRoleInput,
  resumeStructuredSkillsInput,
  resumeVariantLabelInput,
  resumeSkillEdits,
  resumeMetadataEdits,
  resumeAssets,
  resumeUploading,
  focusResumeId,
  setResumeFile,
  setResumeSkillsInput,
  setResumePrimaryRoleInput,
  setResumeStructuredSkillsInput,
  setResumeVariantLabelInput,
  setResumeSkillEdits,
  setResumeMetadataEdits,
  uploadResume,
  saveResumeSkills,
  toggleResumeAsset,
  deleteResumeAsset,
}: ResumeDatabaseSectionProps) {
  const [expandedResumeIds, setExpandedResumeIds] = useState<Record<number, boolean>>({})
  const [handledFocusResumeId, setHandledFocusResumeId] = useState<number | null>(null)

  if (focusResumeId !== null && focusResumeId !== handledFocusResumeId && resumeAssets.some((resume) => resume.id === focusResumeId)) {
    setHandledFocusResumeId(focusResumeId)
    setExpandedResumeIds((prev) => ({ ...prev, [focusResumeId]: true }))
  }

  useEffect(() => {
    if (focusResumeId === null) return
    document.getElementById(`resume-row-${focusResumeId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [focusResumeId])

  const toggleResumeExpanded = (resumeId: number) => {
    setExpandedResumeIds((prev) => ({
      ...prev,
      [resumeId]: !prev[resumeId],
    }))
  }

  const previewSkills = (skillsText: string) => {
    const normalized = (skillsText || '').trim()
    if (!normalized) return 'No manual skills saved yet. File extraction will be used as fallback.'
    const items = normalized.split(',').map((item) => item.trim()).filter(Boolean)
    if (items.length <= 5) return items.join(', ')
    return `${items.slice(0, 5).join(', ')} +${items.length - 5} more`
  }

  return (
    <section className="card resumeDatabaseCard">
      <h2>Resume Database</h2>
      <div className="stack resumeDatabaseStack">
        <p className="subtle resumeDatabaseFallback">
          {activeResume
            ? `Legacy current fallback: ${activeResume.file_name} (v${activeResume.version})`
            : 'No legacy current fallback resume is available yet.'}
        </p>
        <div className="stack resumeDatabaseUpload">
          <input
            type="file"
            accept=".pdf,.doc,.docx"
            aria-label="Upload resume file"
            disabled={resumeUploading}
            onChange={(e) => setResumeFile(e.target.files?.[0] ?? null)}
          />
          <label className="resumeDatabaseField">
            <span>Resume Skills</span>
            <textarea
              className="resumeDatabaseTextarea"
              rows={3}
              value={resumeSkillsInput}
              onChange={(e) => setResumeSkillsInput(e.target.value)}
              placeholder="java, spring boot, microservices, aws"
            />
          </label>
          <label className="resumeDatabaseField"><span>Primary role</span><input value={resumePrimaryRoleInput} onChange={(event) => setResumePrimaryRoleInput(event.target.value)} placeholder="Senior Java Developer" /></label>
          <label className="resumeDatabaseField"><span>Structured skills</span><input value={resumeStructuredSkillsInput} onChange={(event) => setResumeStructuredSkillsInput(event.target.value)} placeholder="Java, Spring Boot, AWS" /></label>
          <label className="resumeDatabaseField"><span>Variant label</span><input value={resumeVariantLabelInput} onChange={(event) => setResumeVariantLabelInput(event.target.value)} placeholder="Java / Banking" /></label>
          <p className="subtle resumeDatabaseHelp">Use clean comma-separated skills for faster and more accurate resume matching.</p>
          <button type="button" onClick={uploadResume} disabled={!resumeFile || resumeUploading}>
            {resumeUploading ? 'Processing Resume...' : 'Upload Resume To Database'}
          </button>
          {resumeUploading ? (
            <p className="subtle" aria-live="polite">Processing resume - extracting content and preparing ATS profile...</p>
          ) : null}
        </div>
        {resumeAssets.length === 0 ? (
          <p className="subtle">No resumes stored yet.</p>
        ) : (
          <div className="resumeDatabaseList">
            {resumeAssets.map((resume) => {
              const isExpanded = !!expandedResumeIds[resume.id]
              return (
                <article key={resume.id} id={`resume-row-${resume.id}`} className={`resumeDatabaseItem pillRow ${resume.id === focusResumeId ? 'focused' : ''}`}>
                  <div className="resumeDatabaseHeader">
                    <div className="resumeDatabaseTitleBlock">
                      <strong className="resumeDatabaseFileName">{resume.file_name}</strong>
                      <div className="resumeDatabaseBadges">
                        <span className="resumeDatabaseVersion">{`v${resume.version}`}</span>
                        {resume.is_current ? <span className="resumeDatabaseBadge">Legacy current fallback</span> : null}
                        <span className="resumeDatabaseBadge">{resume.is_enabled ? 'Enabled' : 'Disabled'}</span>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="resumeDatabaseExpandButton"
                      onClick={() => toggleResumeExpanded(resume.id)}
                      aria-expanded={isExpanded}
                    >
                      {isExpanded ? 'Collapse' : 'Expand'}
                    </button>
                  </div>
                  <p className="subtle resumeDatabaseMeta">Added: {formatSettingsDate(resume.created_at)}</p>
                  <p className="subtle resumeDatabaseSummary">
                    Matching skills preview: {previewSkills(resume.skills_text)}
                  </p>
                  {isExpanded ? (
                    <div className="resumeDatabaseBody">
                      <label className="resumeDatabaseField">
                        <span>Stored Skills</span>
                        <textarea
                          className="resumeDatabaseTextarea"
                          rows={3}
                          value={resumeSkillEdits[resume.id] ?? ''}
                          onChange={(e) =>
                            setResumeSkillEdits((prev) => ({
                              ...prev,
                              [resume.id]: e.target.value,
                            }))
                          }
                          placeholder="java, spring boot, microservices, aws"
                        />
                      </label>
                      <p className="subtle resumeDatabaseMatch">
                        {resume.skills_text
                          ? `Matching skills: ${resume.skills_text}`
                          : 'No manual skills saved yet. File extraction will be used as fallback.'}
                      </p>
                      <label className="resumeDatabaseField"><span>Primary role</span><input value={resumeMetadataEdits[resume.id]?.primary_role ?? ''} onChange={(event) => setResumeMetadataEdits((current) => ({ ...current, [resume.id]: { ...(current[resume.id] ?? { structured_skills: '', variant_label: '' }), primary_role: event.target.value } }))} /></label>
                      <label className="resumeDatabaseField"><span>Structured skills</span><input value={resumeMetadataEdits[resume.id]?.structured_skills ?? ''} onChange={(event) => setResumeMetadataEdits((current) => ({ ...current, [resume.id]: { ...(current[resume.id] ?? { primary_role: '', variant_label: '' }), structured_skills: event.target.value } }))} /></label>
                      <label className="resumeDatabaseField"><span>Variant label</span><input value={resumeMetadataEdits[resume.id]?.variant_label ?? ''} onChange={(event) => setResumeMetadataEdits((current) => ({ ...current, [resume.id]: { ...(current[resume.id] ?? { primary_role: '', structured_skills: '' }), variant_label: event.target.value } }))} /></label>
                      <div className="resumeDatabaseActions">
                        <label className="toggleRow pillRow resumeDatabaseToggle">
                          <span>{resume.is_enabled ? 'Enabled' : 'Disabled'}</span>
                          <span className="toggleSwitch">
                            <input
                              type="checkbox"
                              checked={resume.is_enabled}
                              onChange={(e) => toggleResumeAsset(resume.id, e.target.checked)}
                            />
                            <span className="toggleTrack" />
                          </span>
                        </label>
                        <div className="resumeDatabaseButtons">
                          <button type="button" onClick={() => saveResumeSkills(resume.id)}>
                            Save resume details
                          </button>
                          <button type="button" onClick={() => deleteResumeAsset(resume.id)}>
                            Delete
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : null}
                </article>
              )
            })}
          </div>
        )}
      </div>
    </section>
  )
}

type SkillUpgradeSectionProps = {
  pendingSkills: PendingSkill[]
  loading: boolean
  busySkillKey: string | null
  approveAllSkills: () => void
  approveSkill: (skill: PendingSkill) => void
  dismissSkill: (skill: PendingSkill) => void
  embeddingPendingCount?: number
  embeddingSummary?: string
  embedSkills?: () => void
}

export function SkillUpgradeSection({
  pendingSkills,
  loading,
  busySkillKey,
  approveAllSkills,
  approveSkill,
  dismissSkill,
  embeddingPendingCount = 0,
  embeddingSummary = '',
  embedSkills = () => {},
}: SkillUpgradeSectionProps) {
  const [visibleSkillCount, setVisibleSkillCount] = useState(SETTINGS_REVIEW_BATCH_SIZE)
  const actionablePendingSkills = useMemo(
    () => pendingSkills.filter(
      (skill) => skill.occurrence_count >= 2 && !skill.suspicious,
    ),
    [pendingSkills],
  )
  const visibleSkills = pendingSkills.slice(0, visibleSkillCount)
  const remainingSkills = pendingSkills.length - visibleSkills.length
  return (
    <section className="card skillUpgradeCard">
      <h2>Upgrade Skills</h2>
      <div className="stack skillUpgradeStack">
        <p className="subtle skillUpgradeIntro">
          Review parser-extracted unknown skills here. Approve adds them to your
          custom taxonomy; dismiss removes them from this queue. Approve all only
          includes skills seen at least twice.
        </p>

        <section className="skillUpgradeColumn">
          <div className="skillUpgradeColumnHeader">
            <h3>Pending Unknown Skills</h3>
            <div className="rowBtns">
              {!loading && actionablePendingSkills.length > 0 ? (
                <button
                  type="button"
                  className="primary"
                  onClick={approveAllSkills}
                  disabled={busySkillKey !== null}
                >
                  {busySkillKey === 'approve-all-skills' ? 'Approving all...' : 'Approve all'}
                </button>
              ) : null}
              <span className="skillUpgradeCount">{pendingSkills.length}</span>
            </div>
          </div>
          {loading ? (
            <p className="subtle">Loading skills...</p>
          ) : pendingSkills.length === 0 ? (
            <p className="subtle">No pending unknown skills right now.</p>
          ) : (
            <div className="skillUpgradeList">
              {visibleSkills.map((skill) => {
                const approveKey = `approve:${skill.normalized_name}`
                const dismissKey = `dismiss:${skill.normalized_name}`
                const isSuspicious = skill.suspicious
                return (
                  <article key={skill.normalized_name} className="skillUpgradeItem">
                    <div className="skillUpgradeItemHeader">
                      <strong className="skillUpgradeName">{skill.skill_name}</strong>
                      <span className="skillUpgradeBadge">{skill.occurrence_count} hit{skill.occurrence_count === 1 ? '' : 's'}</span>
                    </div>
                    <p className="subtle skillUpgradeMeta">
                      Normalized key: {skill.normalized_name}
                    </p>
                    <p className="subtle skillUpgradeMeta">
                      Candidate IDs: {skill.candidate_ids.length > 0 ? skill.candidate_ids.join(', ') : '-'}
                    </p>
                    <p className="subtle skillUpgradeMeta">
                      Source: {skill.source_tags.length > 0 ? skill.source_tags.join(', ') : 'legacy'}
                    </p>
                    {isSuspicious ? (
                      <p className="skillUpgradeWarning">
                        This looks malformed or contains known skills
                        {skill.recoverable_skills.length > 0 ? ` (${skill.recoverable_skills.join(', ')})` : ''}.
                        {' '}Approve is disabled; use Dismiss to remove it.
                      </p>
                    ) : null}
                    <div className="skillUpgradeActions">
                      <button
                        type="button"
                        className="primary"
                        onClick={() => approveSkill(skill)}
                        disabled={busySkillKey !== null || isSuspicious}
                      >
                        {busySkillKey === approveKey ? 'Approving...' : 'Approve'}
                      </button>
                      <button
                        type="button"
                        onClick={() => dismissSkill(skill)}
                        disabled={busySkillKey !== null}
                      >
                        {busySkillKey === dismissKey ? 'Dismissing...' : 'Dismiss'}
                      </button>
                    </div>
                  </article>
                )
              })}
              {remainingSkills > 0 ? (
                <button
                  type="button"
                  onClick={() => setVisibleSkillCount((count) => count + SETTINGS_REVIEW_BATCH_SIZE)}
                >
                  Show {Math.min(SETTINGS_REVIEW_BATCH_SIZE, remainingSkills)} more
                </button>
              ) : null}
            </div>
          )}
        </section>

        <section className="skillUpgradeColumn" aria-label="Approved skills embedding">
          <div className="skillUpgradeColumnHeader">
            <h3>Approved Skills — Embedding</h3>
            <div className="rowBtns">
              <button
                type="button"
                className="primary"
                onClick={embedSkills}
                disabled={busySkillKey !== null || embeddingPendingCount === 0}
              >
                {busySkillKey === 'embed-skills' ? 'Embedding...' : 'Embed Skills'}
              </button>
              <span className="skillUpgradeCount">{embeddingPendingCount}</span>
            </div>
          </div>
          <p className="subtle skillUpgradeMeta">
            {embeddingPendingCount} approved skill{embeddingPendingCount === 1 ? '' : 's'} pending embedding.
          </p>
          {embeddingPendingCount >= 150 ? (
            <p className="subtle skillUpgradeMeta">A large batch is ready. Run it when mail sync is idle.</p>
          ) : null}
          {embeddingSummary ? <p className="subtle skillUpgradeMeta">{embeddingSummary}</p> : null}
        </section>
      </div>
    </section>
  )
}

type EntityUpgradeSectionProps = {
  title: string
  pendingEntities: PendingEntity[]
  loading: boolean
  busyKey: string | null
  approveAll: () => void
  approve: (entity: PendingEntity) => void
  dismiss: (entity: PendingEntity) => void
}

export function EntityUpgradeSection({
  title,
  pendingEntities,
  loading,
  busyKey,
  approveAll,
  approve,
  dismiss,
}: EntityUpgradeSectionProps) {
  const [visibleCount, setVisibleCount] = useState(SETTINGS_REVIEW_BATCH_SIZE)
  const visibleEntities = pendingEntities.slice(0, visibleCount)
  const remainingCount = pendingEntities.length - visibleEntities.length
  const actionableEntities = useMemo(
    () => pendingEntities.filter((entity) => entity.occurrence_count >= 2),
    [pendingEntities],
  )
  return (
    <section className="card skillUpgradeCard">
      <h2>{title}</h2>
      <div className="skillUpgradeColumn">
        <div className="skillUpgradeColumnHeader">
          <p className="subtle skillUpgradeIntro">
            Review AI-extracted canonical-name candidates. Approve all only includes values seen at least twice.
          </p>
          <div className="rowBtns">
            {!loading && actionableEntities.length > 0 ? (
              <button type="button" className="primary" onClick={approveAll} disabled={busyKey !== null}>
                {busyKey === 'approve-all' ? 'Approving all...' : `Approve all (${actionableEntities.length})`}
              </button>
            ) : null}
            <span className="skillUpgradeCount">{pendingEntities.length}</span>
          </div>
        </div>
        {loading ? <p className="subtle">Loading candidates...</p> : null}
        {!loading && pendingEntities.length === 0 ? <p className="subtle">No pending candidates.</p> : null}
        {!loading && pendingEntities.length > 0 ? (
          <div className="skillUpgradeList">
            {visibleEntities.map((entity) => (
              <article key={entity.normalized_name} className="skillUpgradeItem">
                <div className="skillUpgradeItemHeader">
                  <strong className="skillUpgradeName">{entity.display_name}</strong>
                  <span className="skillUpgradeBadge">{entity.occurrence_count} hit{entity.occurrence_count === 1 ? '' : 's'}</span>
                </div>
                <p className="subtle skillUpgradeMeta">Candidate IDs: {entity.candidate_ids.join(', ') || '-'}</p>
                <div className="skillUpgradeActions">
                  <button type="button" className="primary" onClick={() => approve(entity)} disabled={busyKey !== null}>Approve</button>
                  <button type="button" onClick={() => dismiss(entity)} disabled={busyKey !== null}>Dismiss</button>
                </div>
              </article>
            ))}
            {remainingCount > 0 ? (
              <button type="button" onClick={() => setVisibleCount((count) => count + SETTINGS_REVIEW_BATCH_SIZE)}>
                Show {Math.min(SETTINGS_REVIEW_BATCH_SIZE, remainingCount)} more
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  )
}

const isPositiveIntentPolarity = (polarity: string) => polarity === 'positive_recruiter_jd'

type JobIntentLearningSectionProps = {
  pendingSignals: JobIntentLearningSignal[]
  approvedSignals: JobIntentLearningSignal[]
  embeddedSignals?: EmbeddedJobIntentSignal[]
  loading: boolean
  busySignalKey: string | null
  approveAllSignals: () => void
  approveSignal: (signal: JobIntentLearningSignal) => void
  dismissSignal: (signal: JobIntentLearningSignal) => void
  togglePolarity: (signal: { id: number }) => void
}

export function JobIntentLearningSection({
  pendingSignals,
  approvedSignals,
  embeddedSignals = [],
  loading,
  busySignalKey,
  approveAllSignals,
  approveSignal,
  dismissSignal,
  togglePolarity,
}: JobIntentLearningSectionProps) {
  const [visiblePendingCount, setVisiblePendingCount] = useState(SETTINGS_REVIEW_BATCH_SIZE)
  const [visiblePositiveApprovedCount, setVisiblePositiveApprovedCount] = useState(SETTINGS_REVIEW_BATCH_SIZE)
  const [visibleNegativeApprovedCount, setVisibleNegativeApprovedCount] = useState(SETTINGS_REVIEW_BATCH_SIZE)
  const visiblePendingSignals = pendingSignals.slice(0, visiblePendingCount)
  const positiveApprovedSignals = useMemo(
    () => approvedSignals.filter((signal) => isPositiveIntentPolarity(signal.polarity)),
    [approvedSignals],
  )
  const negativeApprovedSignals = useMemo(
    () => approvedSignals.filter((signal) => !isPositiveIntentPolarity(signal.polarity)),
    [approvedSignals],
  )
  const visiblePositiveApprovedSignals = positiveApprovedSignals.slice(0, visiblePositiveApprovedCount)
  const visibleNegativeApprovedSignals = negativeApprovedSignals.slice(0, visibleNegativeApprovedCount)
  const remainingPendingSignals = pendingSignals.length - visiblePendingSignals.length
  const remainingPositiveApprovedSignals = positiveApprovedSignals.length - visiblePositiveApprovedSignals.length
  const remainingNegativeApprovedSignals = negativeApprovedSignals.length - visibleNegativeApprovedSignals.length
  const [activeIntentTab, setActiveIntentTab] = useState<'pending' | 'positive' | 'negative' | 'embedded'>('pending')
  const [visibleEmbeddedCount, setVisibleEmbeddedCount] = useState(SETTINGS_REVIEW_BATCH_SIZE)
  const visibleEmbeddedSignals = embeddedSignals.slice(0, visibleEmbeddedCount)
  const remainingEmbeddedSignals = embeddedSignals.length - visibleEmbeddedSignals.length
  return (
    <section className="card skillUpgradeCard">
      <h2>Job Intent Learning</h2>
      <div className="stack skillUpgradeStack">
        <p className="subtle skillUpgradeIntro">
          Groq-suggested Gmail intent phrases land here for review. Approve lets fallback mode use them later; dismiss keeps them suppressed.
        </p>

        <div className="intentTabBar" role="tablist">
          <button type="button" role="tab" aria-selected={activeIntentTab === 'pending'} className={activeIntentTab === 'pending' ? 'intentTab intentTab--active' : 'intentTab'} onClick={() => setActiveIntentTab('pending')}>
            Pending ({pendingSignals.length})
          </button>
          <button type="button" role="tab" aria-selected={activeIntentTab === 'positive'} className={activeIntentTab === 'positive' ? 'intentTab intentTab--active' : 'intentTab'} onClick={() => setActiveIntentTab('positive')}>
            Positive ({positiveApprovedSignals.length})
          </button>
          <button type="button" role="tab" aria-selected={activeIntentTab === 'negative'} className={activeIntentTab === 'negative' ? 'intentTab intentTab--active' : 'intentTab'} onClick={() => setActiveIntentTab('negative')}>
            Negative ({negativeApprovedSignals.length})
          </button>
          <button type="button" role="tab" aria-selected={activeIntentTab === 'embedded'} className={activeIntentTab === 'embedded' ? 'intentTab intentTab--active' : 'intentTab'} onClick={() => setActiveIntentTab('embedded')}>
            Embedded ({embeddedSignals.length})
          </button>
        </div>

        {activeIntentTab === 'pending' ? (
        <section className="skillUpgradeColumn">
          <div className="skillUpgradeColumnHeader">
            <h3>Pending Intent Signals</h3>
            <div className="rowBtns">
              {!loading && pendingSignals.length > 0 ? (
                <button
                  type="button"
                  className="primary"
                  onClick={approveAllSignals}
                  disabled={busySignalKey !== null}
                >
                  {busySignalKey === 'approve-all-intents' ? 'Approving all...' : 'Approve all'}
                </button>
              ) : null}
              <span className="skillUpgradeCount">{pendingSignals.length}</span>
            </div>
          </div>
          {loading ? (
            <p className="subtle">Loading intent signals...</p>
          ) : pendingSignals.length === 0 ? (
            <p className="subtle">No pending job-intent learning right now.</p>
          ) : (
            <div className="skillUpgradeList">
              {visiblePendingSignals.map((signal) => {
                const approveKey = `approve-intent:${signal.id}`
                const dismissKey = `dismiss-intent:${signal.id}`
                return (
                  <article key={`${signal.id}-${signal.normalized_phrase}-${signal.polarity}`} className="skillUpgradeItem">
                    <div className="skillUpgradeItemHeader">
                      <strong className="skillUpgradeName">{signal.phrase}</strong>
                      <span className="skillUpgradeBadge">{signal.source_examples_count} hit{signal.source_examples_count === 1 ? '' : 's'}</span>
                    </div>
                    <p className="subtle skillUpgradeMeta">Polarity: {signal.polarity}</p>
                    <p className="subtle skillUpgradeMeta">Fallback confidence: {signal.confidence_aggregate.toFixed(2)}</p>
                    {signal.last_intent_type ? <p className="subtle skillUpgradeMeta">Last intent: {signal.last_intent_type}</p> : null}
                    {signal.sample_evidence.length > 0 ? (
                      <div className="automationMetrics">
                        <strong>Samples:</strong>
                        {signal.sample_evidence.map((entry) => (
                          <span key={`${signal.id}-${entry}`} className="tag">{entry}</span>
                        ))}
                      </div>
                    ) : null}
                    <div className="skillUpgradeActions">
                      <button
                        type="button"
                        className="primary"
                        onClick={() => approveSignal(signal)}
                        disabled={busySignalKey !== null}
                      >
                        {busySignalKey === approveKey ? 'Approving...' : 'Approve'}
                      </button>
                      <button
                        type="button"
                        onClick={() => dismissSignal(signal)}
                        disabled={busySignalKey !== null}
                      >
                        {busySignalKey === dismissKey ? 'Dismissing...' : 'Dismiss'}
                      </button>
                    </div>
                  </article>
                )
              })}
              {remainingPendingSignals > 0 ? (
                <button
                  type="button"
                  onClick={() => setVisiblePendingCount((count) => count + SETTINGS_REVIEW_BATCH_SIZE)}
                >
                  Show {Math.min(SETTINGS_REVIEW_BATCH_SIZE, remainingPendingSignals)} more pending signals
                </button>
              ) : null}
            </div>
          )}
        </section>
        ) : null}

        {activeIntentTab === 'positive' ? (
        <section className="skillUpgradeColumn">
          <div className="skillUpgradeColumnHeader">
            <h4>Approved — Positive</h4>
            <span className="skillUpgradeCount">{positiveApprovedSignals.length}</span>
          </div>
          {!loading && positiveApprovedSignals.length === 0 ? (
            <p className="subtle">No approved positive signals yet.</p>
          ) : !loading ? (
            <div className="skillUpgradeList">
              {visiblePositiveApprovedSignals.map((signal) => (
                <article key={`approved-${signal.id}`} className="skillUpgradeItem">
                  <div className="skillUpgradeItemHeader">
                    <strong className="skillUpgradeName">{signal.phrase}</strong>
                    <button
                      type="button"
                      className="intentPolarityLight intentPolarityLight--positive"
                      onClick={() => togglePolarity(signal)}
                      disabled={busySignalKey !== null}
                      title="Positive signal — click to mark negative"
                    >
                      {busySignalKey === `toggle-polarity:${signal.id}` ? '...' : 'Positive'}
                    </button>
                  </div>
                  <p className="subtle skillUpgradeMeta">Examples: {signal.source_examples_count}</p>
                  <p className="subtle skillUpgradeMeta">Confidence: {signal.confidence_aggregate.toFixed(2)}</p>
                </article>
              ))}
              {remainingPositiveApprovedSignals > 0 ? (
                <button
                  type="button"
                  onClick={() => setVisiblePositiveApprovedCount((count) => count + SETTINGS_REVIEW_BATCH_SIZE)}
                >
                  Show {Math.min(SETTINGS_REVIEW_BATCH_SIZE, remainingPositiveApprovedSignals)} more positive signals
                </button>
              ) : null}
            </div>
          ) : null}
        </section>
        ) : null}

        {activeIntentTab === 'negative' ? (
        <section className="skillUpgradeColumn">
          <div className="skillUpgradeColumnHeader">
            <h4>Approved — Negative</h4>
            <span className="skillUpgradeCount">{negativeApprovedSignals.length}</span>
          </div>
          {!loading && negativeApprovedSignals.length === 0 ? (
            <p className="subtle">No approved negative signals yet.</p>
          ) : !loading ? (
            <div className="skillUpgradeList">
              {visibleNegativeApprovedSignals.map((signal) => (
                <article key={`approved-${signal.id}`} className="skillUpgradeItem">
                  <div className="skillUpgradeItemHeader">
                    <strong className="skillUpgradeName">{signal.phrase}</strong>
                    <button
                      type="button"
                      className="intentPolarityLight intentPolarityLight--negative"
                      onClick={() => togglePolarity(signal)}
                      disabled={busySignalKey !== null}
                      title="Negative signal — click to mark positive"
                    >
                      {busySignalKey === `toggle-polarity:${signal.id}` ? '...' : 'Negative'}
                    </button>
                  </div>
                  <p className="subtle skillUpgradeMeta">Examples: {signal.source_examples_count}</p>
                  <p className="subtle skillUpgradeMeta">Confidence: {signal.confidence_aggregate.toFixed(2)}</p>
                </article>
              ))}
              {remainingNegativeApprovedSignals > 0 ? (
                <button
                  type="button"
                  onClick={() => setVisibleNegativeApprovedCount((count) => count + SETTINGS_REVIEW_BATCH_SIZE)}
                >
                  Show {Math.min(SETTINGS_REVIEW_BATCH_SIZE, remainingNegativeApprovedSignals)} more negative signals
                </button>
              ) : null}
            </div>
          ) : null}
        </section>
        ) : null}

        {activeIntentTab === 'embedded' ? (
        <section className="skillUpgradeColumn">
          <div className="skillUpgradeColumnHeader">
            <h4>Currently Embedded Signals</h4>
            <span className="skillUpgradeCount">{embeddedSignals.length}</span>
          </div>
          <p className="subtle skillUpgradeIntro">
            These are the top-ranked approved signals Groq and semantic matching actually use right now (capped at 15 per polarity).
          </p>
          {!loading && embeddedSignals.length === 0 ? (
            <p className="subtle">No signals are currently embedded.</p>
          ) : !loading ? (
            <div className="skillUpgradeList">
              {visibleEmbeddedSignals.map((signal) => (
                <article key={`embedded-${signal.id}`} className="skillUpgradeItem">
                  <div className="skillUpgradeItemHeader">
                    <strong className="skillUpgradeName">{signal.phrase}</strong>
                    <button
                      type="button"
                      className={isPositiveIntentPolarity(signal.polarity) ? 'intentPolarityLight intentPolarityLight--positive' : 'intentPolarityLight intentPolarityLight--negative'}
                      onClick={() => togglePolarity(signal)}
                      disabled={busySignalKey !== null}
                      title={isPositiveIntentPolarity(signal.polarity) ? 'Positive signal — click to mark negative' : 'Negative signal — click to mark positive'}
                    >
                      {busySignalKey === `toggle-polarity:${signal.id}` ? '...' : isPositiveIntentPolarity(signal.polarity) ? 'Positive' : 'Negative'}
                    </button>
                  </div>
                  <p className="subtle skillUpgradeMeta">Confidence: {signal.confidence.toFixed(2)}</p>
                  <p className="subtle skillUpgradeMeta">{signal.embedded ? 'Embedded via SBERT' : 'Fallback (hash embedding — SBERT unavailable)'}</p>
                </article>
              ))}
              {remainingEmbeddedSignals > 0 ? (
                <button
                  type="button"
                  onClick={() => setVisibleEmbeddedCount((count) => count + SETTINGS_REVIEW_BATCH_SIZE)}
                >
                  Show {Math.min(SETTINGS_REVIEW_BATCH_SIZE, remainingEmbeddedSignals)} more embedded signals
                </button>
              ) : null}
            </div>
          ) : null}
        </section>
        ) : null}
      </div>
    </section>
  )
}

export type Candidate = {
  id: number
  record_id?: string | null
  subject: string
  sender: string
  body: string
  role: string
  // NULL on rows written before provenance existed - unverified, not 'extracted'.
  role_source?: string | null
  role_canonical?: string | null
  location: string
  salary_text: string
  skills_text: string
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
  ats_score?: number | null
  ats_score_source?: string | null
  ats_summary?: string | null
  ats_breakdown?: Record<string, unknown> | null
  resume_picker_score?: number | null
  resume_picker_reason?: string | null
  resume_picker_candidates?: Record<string, unknown> | null
  resume_picker_breakdown?: Record<string, unknown> | null
  draft_reply: string
  draft_source: string | null
  draft_model: string | null
  draft_ai_error: string | null
  draft_resume_context_status: string | null
  draft_quality?: DraftQuality | null
  resume_file_name: string | null
  parser_details: Record<string, unknown> | null
  attachment_file_names: string[]
  state: string
  last_error: string | null
  source: string
  external_message_id: string | null
  external_thread_id: string | null
  gmail_sent_id?: string | null
  source_parent_email_id?: number | null
  is_source_parent?: boolean
  is_multi_role_child?: boolean
  requirement_index?: number | null
  requirement_count?: number | null
  requirement_key?: string | null
  requirement_source_text?: string | null
  inherited_constraints?: Array<Record<string, unknown>>
  role_manifest_status?: string
  role_manifest_confidence?: number | null
  role_manifest?: Record<string, unknown> | null
  role_manifest_diagnostics?: Record<string, unknown> | null
  eligibility_status?: string | null
  eligibility_details?: Record<string, unknown> | null
  sendability_status?: string | null
  screening_mode?: 'compatibility' | 'strict' | null
  marked_for_tracking: boolean
  premium_status?: string | null
  premium_verification_level?: 'unverified' | 'verified' | 'trusted' | null
  following_badge?: 'bookmarked' | 'tracked' | 'active' | null
  following_warning?: string | null
}

export type SentItemDetails = {
  email_id: number
  source_type: string
  source_label: string
  requirement_received_link: string | null
  sent_gmail_message_link: string | null
  resume_variant_sent: string | null
  attached_files: string[]
  company: string | null
  recruiter_name: string | null
  recruiter_email: string | null
  recruiter_email_domain: string | null
  recruiter_phone: string | null
  recruiter_company: string | null
  employer_name: string | null
  employer_email: string | null
  employer_email_domain: string | null
  employer_phone: string | null
  employer_company: string | null
  end_client: string | null
  implementation_partner: string | null
  vendor: string | null
  domain_mentioned: string | null
  experience_required: string | null
  mandatory_skills: string[]
  missing_skills: string[]
  ats_score: number | null
  ats_summary: string | null
  to_email: string | null
  cc_email: string | null
  sent_at: string | null
  opened_at: string | null
  open_count: number
  reply_count: number
}

type ConversationSummary = {
  id: number
  root_recruiter_email_id: number
  recruiter: string
  recruiter_email: string | null
  subject: string
  status: string
  last_message_preview: string
  last_message_at: string
  unread_reply_count: number
  last_inbound_reply_at: string | null
  gmail_thread_link: string | null
}

type ConversationMessage = {
  id: number
  direction: 'inbound' | 'outbound'
  sender: string
  body: string
  snippet: string
  occurred_at: string
  read_at: string | null
}

type ConversationDetail = ConversationSummary & {
  to_email: string | null
  cc_email: string | null
  messages: ConversationMessage[]
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

function getSourceLabel(source: string | null | undefined): string {
  const normalized = (source ?? '').trim().toLowerCase()
  if (normalized === 'gmail') return 'Gmail'
  if (normalized === 'nvoids') return 'Nvoids'
  if (normalized === 'manual') return 'Manual'
  return normalized || 'Unknown'
}

function renderTextOrDash(value: string | null | undefined): string {
  const text = (value ?? '').trim()
  return text || '-'
}

function jobStatusMeta(status: string): { label: string; className: string; checkmark: boolean } {
  switch (status) {
    case 'running':
      return { label: 'Running', className: 'running', checkmark: false }
    case 'ok':
      return { label: 'Completed', className: 'ok', checkmark: true }
    case 'failed':
      return { label: 'Failed', className: 'failed', checkmark: false }
    case 'canceled':
      return { label: 'Canceled', className: 'canceled', checkmark: false }
    default:
      return { label: 'Queued', className: 'queued', checkmark: false }
  }
}

export function jdSummarySkills(item: Candidate): string[] {
  const breakdown = item.resume_picker_breakdown ?? {}
  const matchedPriority = Array.isArray(breakdown.matched_priority_skills)
    ? breakdown.matched_priority_skills.filter((s): s is string => typeof s === 'string' && s.trim().length > 0)
    : []
  if (matchedPriority.length > 0) return matchedPriority.slice(0, 3)
  return (item.skills_text ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 3)
}

function renderListOrDash(values: string[] | null | undefined): string {
  const items = (values ?? []).map((value) => value.trim()).filter(Boolean)
  return items.length > 0 ? items.join(', ') : '-'
}

function formatPickerScore(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '-'
  return `${Math.round(value * 100)}`
}

type ResumePickerPanelProps = {
  candidate: Candidate
}

export function ResumePickerPanel({ candidate }: ResumePickerPanelProps) {
  const breakdown = candidate.resume_picker_breakdown ?? {}
  const candidatesPayload = candidate.resume_picker_candidates ?? {}
  const matchedPriority = Array.isArray(breakdown.matched_priority_skills) ? breakdown.matched_priority_skills.filter((item): item is string => typeof item === 'string' && item.trim().length > 0) : []
  const missingPriority = Array.isArray(breakdown.missing_priority_skills) ? breakdown.missing_priority_skills.filter((item): item is string => typeof item === 'string' && item.trim().length > 0) : []
  const rankingsRaw = Array.isArray(candidatesPayload.rankings) ? candidatesPayload.rankings : []
  const alternatives = rankingsRaw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .filter((item) => (item.resume_file_name as string | undefined) !== candidate.resume_file_name)
    .slice(0, 2)

  if (
    candidate.resume_picker_score == null &&
    !candidate.resume_picker_reason &&
    matchedPriority.length === 0 &&
    missingPriority.length === 0 &&
    alternatives.length === 0
  ) {
    return null
  }

  return (
    <div className="parserDetailsPanel">
      <p><strong>Resume Picker:</strong> {candidate.resume_file_name ?? '-'}</p>
      <p><strong>Final Score:</strong> {formatPickerScore(candidate.resume_picker_score)}</p>
      <p><strong>Why:</strong> {candidate.resume_picker_reason ?? '-'}</p>
      <p><strong>Matched Priority Skills:</strong> {matchedPriority.join(', ') || '-'}</p>
      <p><strong>Missing Priority Skills:</strong> {missingPriority.join(', ') || '-'}</p>
      <p><strong>Top Alternatives:</strong></p>
      {alternatives.length === 0 ? <p className="subtle">No alternatives logged.</p> : null}
      {alternatives.length > 0 ? (
        <ul>
          {alternatives.map((item, index) => (
            <li key={`${String(item.resume_file_name ?? index)}-${index}`}>
              {String(item.resume_file_name ?? '-')} ({formatPickerScore(typeof item.final_resume_score === 'number' ? item.final_resume_score : null)}): {String(item.selection_reason ?? '-')}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

type CandidateDeleteResponse = {
  id: number
  deleted: boolean
  state: string
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

type JobQueueSummary = {
  queued: number
  processing: number
  succeeded: number
  failed: number
}

type LiveReplyStatus = {
  count: number
  checked_at: string | null
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

export function formatAtsScore(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-'
  return String(Math.round(value))
}

export function getAtsStrengthLabel(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return 'Unknown'
  if (value >= 80) return 'Strong'
  if (value >= 60) return 'Moderate'
  return 'Weak'
}

type ParserDetailsPayload = {
  parser_version?: string
  source?: string
  parser_mode?: string
  base_parser_result?: Record<string, unknown>
  ai_extractor_result?: Record<string, unknown> | null
  skills_audit?: Record<string, unknown>
  approved_skills_text?: string
  unknown_skills?: unknown[]
  merged_result?: Record<string, unknown>
  parser_warning?: string | null
  fallback_used?: boolean
  source_hints?: Record<string, unknown>
  structured_requirements?: StructuredRequirementsPayload | Record<string, unknown> | null
  requirements_schema_version?: number
}

type StructuredRequirementSkill = {
  canonical_name: string
  versions: string[]
}

type StructuredRequirementGroup = {
  group_id: string
  level: string
  mode: 'all' | 'any'
  skills: StructuredRequirementSkill[]
}

type StructuredRequirementsPayload = {
  schema_version?: number
  required_groups: StructuredRequirementGroup[]
  preferred_groups: StructuredRequirementGroup[]
  informational_groups: StructuredRequirementGroup[]
  experience_years_min?: number | null
  local_required?: boolean
  work_mode?: string | null
  locations: string[]
  preferred_domains: string[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function normalizeParserDetails(value: unknown): ParserDetailsPayload | null {
  if (!isRecord(value)) return null
  return value as ParserDetailsPayload
}

function readStructuredRequirementSkill(value: unknown): StructuredRequirementSkill | null {
  if (!isRecord(value)) return null
  const canonicalName = recordStringValue(value, 'canonical_name')
  if (!canonicalName) return null
  return {
    canonical_name: canonicalName,
    versions: recordStringArray(value, 'versions'),
  }
}

function readStructuredRequirementGroup(value: unknown): StructuredRequirementGroup | null {
  if (!isRecord(value)) return null
  const mode = recordStringValue(value, 'mode').toLowerCase() === 'any' ? 'any' : 'all'
  const skills = Array.isArray(value.skills)
    ? value.skills.map(readStructuredRequirementSkill).filter((item): item is StructuredRequirementSkill => item != null)
    : []
  if (skills.length === 0) return null
  return {
    group_id: recordStringValue(value, 'group_id'),
    level: recordStringValue(value, 'level'),
    mode,
    skills,
  }
}

function normalizeStructuredRequirements(value: unknown): StructuredRequirementsPayload | null {
  if (!isRecord(value)) return null
  const readGroups = (key: string) => {
    const raw = value[key]
    if (!Array.isArray(raw)) return []
    return raw.map(readStructuredRequirementGroup).filter((item): item is StructuredRequirementGroup => item != null)
  }
  return {
    schema_version: typeof value.schema_version === 'number' ? value.schema_version : undefined,
    required_groups: readGroups('required_groups'),
    preferred_groups: readGroups('preferred_groups'),
    informational_groups: readGroups('informational_groups'),
    experience_years_min: typeof value.experience_years_min === 'number' ? value.experience_years_min : null,
    local_required: typeof value.local_required === 'boolean' ? value.local_required : undefined,
    work_mode: recordStringValue(value, 'work_mode') || null,
    locations: recordStringArray(value, 'locations'),
    preferred_domains: recordStringArray(value, 'preferred_domains'),
  }
}

function renderParserValue(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') return value || '-'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    return value.length === 0 ? '-' : value.map((item) => renderParserValue(item)).join(', ')
  }
  if (isRecord(value)) {
    const pairs = Object.entries(value)
    if (pairs.length === 0) return '-'
    return pairs.map(([key, item]) => `${key}: ${renderParserValue(item)}`).join('\n')
  }
  return String(value)
}

function recordStringValue(record: Record<string, unknown> | null | undefined, key: string): string {
  if (!record) return ''
  const value = record[key]
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

function recordStringArray(record: Record<string, unknown> | null | undefined, key: string): string[] {
  if (!record) return []
  const value = record[key]
  if (!Array.isArray(value)) return []
  return value.map((item) => renderParserValue(item).trim()).filter(Boolean).filter((item) => item !== '-')
}

function parserTitleCase(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .map((part) => {
      const lowered = part.toLowerCase()
      if (lowered === 'ai') return 'AI'
      if (lowered === 'ats') return 'ATS'
      if (lowered === 'api') return 'API'
      if (lowered === 'id') return 'ID'
      if (lowered === 'jd') return 'JD'
      return part.charAt(0).toUpperCase() + part.slice(1)
    })
    .join(' ')
}

function parserTokensFromValue(value: unknown): string[] {
  const seen = new Set<string>()
  const items: string[] = []

  const push = (raw: string) => {
    const text = raw.trim()
    if (!text || text === '-') return
    const key = text.toLowerCase()
    if (seen.has(key)) return
    seen.add(key)
    items.push(text)
  }

  const visit = (current: unknown) => {
    if (current == null) return
    if (Array.isArray(current)) {
      current.forEach(visit)
      return
    }
    if (typeof current === 'string') {
      current
        .split(/[,;\n]+/)
        .map((item) => item.trim())
        .filter(Boolean)
        .forEach(push)
      return
    }
    if (typeof current === 'number' || typeof current === 'boolean') {
      push(String(current))
      return
    }
    if (isRecord(current)) {
      Object.values(current).forEach(visit)
    }
  }

  visit(value)
  return items
}

function formatStructuredSkill(skill: StructuredRequirementSkill): string {
  if (skill.versions.length === 0) return skill.canonical_name
  return `${skill.canonical_name} ${skill.versions.join('/')}`
}

function formatRequirementGroupSkills(group: StructuredRequirementGroup): string {
  const items = group.skills.map(formatStructuredSkill)
  return group.mode === 'any' ? items.join(' or ') : items.join(', ')
}

function requirementModeLabel(group: StructuredRequirementGroup, preferred = false): string {
  if (preferred) return group.mode === 'any' ? 'Preferred' : 'Preferred'
  return group.mode === 'any' ? 'One required' : 'All required'
}

function normalizeLocationValue(value: string): string {
  return value.replace(/\s+/g, ' ').trim()
}

function isCleanLocationValue(value: string): boolean {
  const text = normalizeLocationValue(value)
  if (!text || text.includes('\n')) return false
  if (/send resume|education|spring boot|kafka|docker|merchant|permanent resident/i.test(text)) return false
  return /remote|onsite|hybrid|[A-Za-z][A-Za-z .'-]+,\s*[A-Za-z0-9]{2,}/i.test(text)
}

function pickDisplayLocation(locations: string[]): string {
  const cleanLocations = Array.from(new Set(locations.map(normalizeLocationValue).filter(isCleanLocationValue)))
  return cleanLocations[0] ?? '-'
}

function readRequirementGroupLabels(value: unknown, key: string): string[] {
  if (!isRecord(value)) return []
  return recordStringArray(value, key)
}

function readMatchedAlternatives(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {}
  return Object.fromEntries(
    Object.entries(value)
      .map(([key, raw]) => [key.trim(), renderParserValue(raw).trim()] as const)
      .filter(([key, raw]) => key && raw && raw !== '-'),
  )
}

function summarizePickerGroupResult(
  label: string,
  matchedAlternatives: Record<string, string>,
): string {
  const matchedAlternative = matchedAlternatives[label]
  if (matchedAlternative) return `${label} through ${matchedAlternative}`
  return label
}

function rawRequirementGroupLines(groups: StructuredRequirementGroup[], preferred = false): string {
  if (groups.length === 0) return '-'
  return groups
    .map((group) => `${requirementModeLabel(group, preferred)}: ${formatRequirementGroupSkills(group)}`)
    .join('\n')
}

function rawConstraintsSummary(structuredRequirements: StructuredRequirementsPayload | null): string {
  if (!structuredRequirements) return '-'
  const lines = [
    `Experience: ${structuredRequirements.experience_years_min != null ? `${structuredRequirements.experience_years_min}+ years` : '-'}`,
    `Location: ${pickDisplayLocation(structuredRequirements.locations)}`,
    `Local candidate: ${
      structuredRequirements.local_required == null
        ? '-'
        : structuredRequirements.local_required
          ? 'Required'
          : 'Not required'
    }`,
    `Work mode: ${structuredRequirements.work_mode || '-'}`,
    `Preferred experience/domain: ${structuredRequirements.preferred_domains.length > 0 ? structuredRequirements.preferred_domains.join(', ') : '-'}`,
  ]
  return lines.join('\n')
}

function rawResumePickerSummary(args: {
  mandatoryStatus: string
  mandatoryCoverage: string
  satisfiedRequiredGroups: string[]
  unmetRequiredGroups: string[]
  matchedAlternatives: Record<string, string>
  versionUnverified: string[]
}): string {
  const {
    mandatoryStatus,
    mandatoryCoverage,
    satisfiedRequiredGroups,
    unmetRequiredGroups,
    matchedAlternatives,
    versionUnverified,
  } = args
  return [
    `Mandatory gate status: ${mandatoryStatus || '-'}`,
    `Mandatory coverage: ${mandatoryCoverage}`,
    `Satisfied: ${satisfiedRequiredGroups.length > 0 ? satisfiedRequiredGroups.join('; ') : '-'}`,
    `Unmet: ${unmetRequiredGroups.length > 0 ? unmetRequiredGroups.join('; ') : '-'}`,
    `Matched alternatives: ${Object.keys(matchedAlternatives).length > 0 ? Object.values(matchedAlternatives).join('; ') : '-'}`,
    `Version not verified: ${versionUnverified.length > 0 ? versionUnverified.join('; ') : '-'}`,
  ].join('\n')
}

function formatParserMetricValue(value: unknown, options?: { percent?: boolean }): string {
  const percent = options?.percent ?? false
  if (value == null) return '-'
  if (typeof value === 'number') {
    if (Number.isNaN(value)) return '-'
    if (percent) return `${Math.round(value * 100)}%`
    if (Number.isInteger(value)) return String(value)
    return value.toFixed(2)
  }
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  const text = renderParserValue(value).trim()
  return text || '-'
}

function parserMetricRowsFromAts(
  atsScore: number | null | undefined,
  atsSource: string | null | undefined,
  atsSummary: string | null | undefined,
  atsBreakdown: Record<string, unknown> | null | undefined,
): Array<{ label: string; value: string }> {
  return [
    { label: 'Score', value: atsScore == null ? '-' : `${formatAtsScore(atsScore)} (${getAtsStrengthLabel(atsScore)})` },
    { label: 'Source', value: renderTextOrDash(atsSource) },
    { label: 'Raw Overlap', value: formatParserMetricValue(atsBreakdown?.raw_overlap, { percent: true }) },
    { label: 'Intent Match', value: formatParserMetricValue(atsBreakdown?.intent_match, { percent: true }) },
    { label: 'Role Alignment', value: formatParserMetricValue(atsBreakdown?.role_alignment, { percent: true }) },
    { label: 'Semantic Similarity', value: formatParserMetricValue(atsBreakdown?.semantic_similarity, { percent: true }) },
    { label: 'Foundation Coverage', value: formatParserMetricValue(atsBreakdown?.foundation_coverage, { percent: true }) },
    { label: 'Summary', value: renderTextOrDash(atsSummary) },
  ]
}

function parserDisplayValue(value: unknown): string {
  if (value == null) return '-'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return renderParserValue(value)
}

function ParserChipList({ items, empty = '-' }: { items: string[]; empty?: string }) {
  if (items.length === 0) return <p className="subtle">{empty}</p>
  return (
    <div className="parserChipList">
      {items.map((item) => (
        <span key={item} className="parserChip">{item}</span>
      ))}
    </div>
  )
}

function ParserMetricGrid({ rows }: { rows: Array<{ label: string; value: string }> }) {
  const filtered = rows.filter((row) => row.value.trim() && row.value.trim() !== '-')
  const effective = filtered.length > 0 ? filtered : rows
  return (
    <div className="parserMetricGrid">
      {effective.map((row) => (
        <div key={row.label} className="parserMetricRow">
          <span className="parserLabel">{row.label}</span>
          <span className="parserValue">{row.value}</span>
        </div>
      ))}
    </div>
  )
}

function ParserFieldValue({ value }: { value: unknown }) {
  if (value == null || value === '') return <span className="parserValue">-</span>

  if (Array.isArray(value)) {
    const items = parserTokensFromValue(value)
    if (items.length > 0) return <ParserChipList items={items} />
    return <span className="parserValue">{renderParserValue(value)}</span>
  }

  if (isRecord(value)) {
    const entries = Object.entries(value)
    if (entries.length === 0) return <span className="parserValue">-</span>
    return (
      <div className="parserNestedBlock">
        <ParserKeyValueList record={value} />
      </div>
    )
  }

  if (typeof value === 'string') {
    const text = value.trim()
    if (!text) return <span className="parserValue">-</span>
    if (text.includes('\n') || text.length > 120) {
      return <div className="parserTextBlock">{text}</div>
    }
    return <span className="parserValue">{text}</span>
  }

  return <span className="parserValue">{parserDisplayValue(value)}</span>
}

function ParserKeyValueList({ record }: { record: Record<string, unknown> }) {
  const entries = Object.entries(record)
  if (entries.length === 0) return <p className="subtle">-</p>
  return (
    <div className="parserKeyValueList">
      {entries.map(([key, value]) => (
        <div key={key} className="parserKeyValueRow">
          <span className="parserLabel">{parserTitleCase(key)}</span>
          <ParserFieldValue value={value} />
        </div>
      ))}
    </div>
  )
}

function ParserRawDebug({ value }: { value: unknown }) {
  return (
    <details className="parserRawDebug">
      <summary>Raw Debug</summary>
      <pre className="parserRawBlock">{renderParserValue(value)}</pre>
    </details>
  )
}

function ParserDetailsCard({
  title,
  className,
  bodyClassName,
  children,
}: {
  title: string
  className?: string
  bodyClassName?: string
  children: React.ReactNode
}) {
  const classes = ['parserDetailsBlock', className].filter(Boolean).join(' ')
  const bodyClasses = ['parserDetailsCardBody', bodyClassName].filter(Boolean).join(' ')
  return (
    <section className={classes}>
      <h3>{title}</h3>
      <div className={bodyClasses}>{children}</div>
    </section>
  )
}

type ContactDetailsSourceItem = {
  id: number
  role?: string | null
  location?: string | null
  salary_text?: string | null
  skills_text?: string | null
  resume_file_name?: string | null
  recipient_email?: string | null
  cc_email?: string | null
  ats_score?: number | null
  ats_summary?: string | null
}

export function renderContactDetailsGrid(details: SentItemDetails, item: ContactDetailsSourceItem) {
  return (
    <div className="parserDetailsSummaryGrid">
      <ParserDetailsCard title="Source" className="parserDetailsSummaryBlock">
        <div className="sentItemLinkList">
          <p><strong>Source:</strong> {renderTextOrDash(details.source_label)}</p>
          <p>
            <strong>Requirement Link:</strong>{' '}
            {details.requirement_received_link ? (
              <a href={details.requirement_received_link} target="_blank" rel="noreferrer">
                Open requirement
              </a>
            ) : '-'}
          </p>
          <p>
            <strong>Sent Gmail Link:</strong>{' '}
            {details.sent_gmail_message_link ? (
              <a href={details.sent_gmail_message_link} target="_blank" rel="noreferrer">
                Open sent message
              </a>
            ) : '-'}
          </p>
        </div>
      </ParserDetailsCard>
      <ParserDetailsCard title="Requirement" className="parserDetailsSummaryBlock">
        <pre className="parserCardPre">{[
          `Role: ${renderTextOrDash(item.role)}`,
          `Location: ${renderTextOrDash(item.location)}`,
          `Salary: ${renderTextOrDash(item.salary_text)}`,
          `Skills: ${renderTextOrDash(item.skills_text)}`,
          `Company: ${renderTextOrDash(details.company)}`,
          `End Client: ${renderTextOrDash(details.end_client)}`,
          `Implementation Partner: ${renderTextOrDash(details.implementation_partner)}`,
          `Vendor: ${renderTextOrDash(details.vendor)}`,
          `Domain Mentioned: ${renderTextOrDash(details.domain_mentioned)}`,
          `Experience Required: ${renderTextOrDash(details.experience_required)}`,
          `Mandatory Skills: ${renderListOrDash(details.mandatory_skills)}`,
          `Missing Skills: ${renderListOrDash(details.missing_skills)}`,
        ].join('\n')}</pre>
      </ParserDetailsCard>
      <ParserDetailsCard title="Resume / Send Audit" className="parserDetailsSummaryBlock">
        <pre className="parserCardPre">{[
          `Resume Variant Sent: ${renderTextOrDash(details.resume_variant_sent ?? item.resume_file_name)}`,
          `Attached Files: ${renderListOrDash(details.attached_files)}`,
          `To: ${renderTextOrDash(details.to_email ?? item.recipient_email)}`,
          `CC: ${renderTextOrDash(details.cc_email ?? item.cc_email)}`,
          `ATS Score: ${formatAtsScore(details.ats_score ?? item.ats_score)}${(details.ats_score ?? item.ats_score) != null ? ` (${getAtsStrengthLabel(details.ats_score ?? item.ats_score)})` : ''}`,
          `ATS Summary: ${renderTextOrDash(details.ats_summary ?? item.ats_summary)}`,
        ].join('\n')}</pre>
      </ParserDetailsCard>
      <ParserDetailsCard title="Recruiter" className="parserDetailsSummaryBlock">
        <pre className="parserCardPre">{[
          `Recruiter Name: ${renderTextOrDash(details.recruiter_name)}`,
          `Recruiter Email: ${renderTextOrDash(details.recruiter_email)}`,
          `Recruiter Email Domain: ${renderTextOrDash(details.recruiter_email_domain)}`,
          `Recruiter Phone: ${renderTextOrDash(details.recruiter_phone)}`,
          `Recruiter Company: ${renderTextOrDash(details.recruiter_company)}`,
        ].join('\n')}</pre>
      </ParserDetailsCard>
      <ParserDetailsCard title="Employer" className="parserDetailsSummaryBlock">
        <pre className="parserCardPre">{[
          `Employer Name: ${renderTextOrDash(details.employer_name)}`,
          `Employer Email: ${renderTextOrDash(details.employer_email)}`,
          `Employer Email Domain: ${renderTextOrDash(details.employer_email_domain)}`,
          `Employer Phone: ${renderTextOrDash(details.employer_phone)}`,
          `Employer Company: ${renderTextOrDash(details.employer_company)}`,
        ].join('\n')}</pre>
      </ParserDetailsCard>
    </div>
  )
}

function ParserStructuredSection({
  title,
  data,
  rawData,
}: {
  title: string
  data: Record<string, unknown> | null | undefined
  rawData?: unknown
}) {
  const effectiveData = data ?? {}
  return (
    <ParserDetailsCard title={title}>
      <ParserKeyValueList record={effectiveData} />
      <ParserRawDebug value={rawData ?? effectiveData} />
    </ParserDetailsCard>
  )
}

type ParserDetailsPanelProps = {
  candidateId: number
  source: string
  parserDetails: Record<string, unknown> | null
  atsScore?: number | null
  atsSource?: string | null
  atsSummary?: string | null
  atsBreakdown?: Record<string, unknown> | null
  resumePickerBreakdown?: Record<string, unknown> | null
  expanded: boolean
  onToggle: (candidateId: number) => void
}

export function ParserDetailsPanel({
  candidateId,
  source,
  parserDetails,
  atsScore,
  atsSource,
  atsSummary,
  atsBreakdown,
  resumePickerBreakdown,
  expanded,
  onToggle,
}: ParserDetailsPanelProps) {
  const [viewMode, setViewMode] = useState<'v3' | 'v1'>('v3')
  const normalized = normalizeParserDetails(parserDetails)
  if (!normalized) return null
  const legacyFinalResult = normalized.merged_result ?? {}
  const legacyBaseResult = normalized.base_parser_result ?? {}
  const finalResult = isRecord(legacyFinalResult) ? legacyFinalResult : {}
  const baseResult = isRecord(legacyBaseResult) ? legacyBaseResult : {}
  const skillsAudit = isRecord(normalized.skills_audit) ? normalized.skills_audit : null
  const legacyApprovedSkillsText = (() => {
    const audited = skillsAudit ? recordStringArray(skillsAudit, 'known').join(', ') : ''
    if (audited) return audited
    return (normalized.approved_skills_text || recordStringValue(finalResult, 'skills_text')).trim()
  })()
  const approvedSkills = (() => {
    const audited = skillsAudit ? recordStringArray(skillsAudit, 'known') : []
    if (audited.length > 0) return audited
    return parserTokensFromValue(normalized.approved_skills_text || recordStringValue(finalResult, 'skills_text'))
  })()
  const unknownSkills = (() => {
    if (skillsAudit) {
      const audited = recordStringArray(skillsAudit, 'unknown')
      if (audited.length > 0) return audited
    }
    return Array.isArray(normalized.unknown_skills)
      ? normalized.unknown_skills.map((item) => renderParserValue(item).trim()).filter(Boolean).filter((item) => item !== '-')
      : []
  })()
  const aiExtractor = isRecord(normalized.ai_extractor_result) ? normalized.ai_extractor_result : null
  const structuredRequirements = normalizeStructuredRequirements(normalized.structured_requirements)
  const sourceHints = isRecord(normalized.source_hints) ? normalized.source_hints : null
  const parserMode = normalized.parser_mode || (aiExtractor ? 'ai_primary' : 'base_only')
  const parserWarning = renderParserValue(normalized.parser_warning)
  const fallbackUsed = Boolean(normalized.fallback_used)
  const parserStatus = {
    mode: parserMode,
    fallback_used: fallbackUsed,
    warning: parserWarning === '-' ? null : parserWarning,
  }
  const legacyAtsSummary = atsSummary || `ATS Score: ${formatAtsScore(atsScore)} (${getAtsStrengthLabel(atsScore)})`
  const finalSkills = parserTokensFromValue(recordStringValue(finalResult, 'skills_text'))
  const atsMetricRows = parserMetricRowsFromAts(atsScore, atsSource, atsSummary, atsBreakdown)
  const atsBreakdownRecord = {
    score: atsScore == null ? '-' : `${formatAtsScore(atsScore)} (${getAtsStrengthLabel(atsScore)})`,
    source: atsSource ?? '-',
    ...(atsBreakdown ?? {}),
  }
  const aiEvidenceRecord = aiExtractor
    ? {
        confidence: aiExtractor.confidence,
        evidence: aiExtractor.evidence,
        error: aiExtractor.error,
      }
    : {}
  const matchedRawSkills = isRecord(atsBreakdown) ? recordStringArray(atsBreakdown, 'matched_raw_skills') : []
  const missingRawSkills = isRecord(atsBreakdown) ? recordStringArray(atsBreakdown, 'missing_raw_skills') : []
  const pickerBreakdown = isRecord(resumePickerBreakdown) ? resumePickerBreakdown : null
  const mandatoryStatus = pickerBreakdown ? recordStringValue(pickerBreakdown, 'mandatory_gate_status') : ''
  const mandatoryCoverageRaw = pickerBreakdown?.mandatory_coverage
  const mandatoryCoverage =
    typeof mandatoryCoverageRaw === 'number' && Number.isFinite(mandatoryCoverageRaw)
      ? `${Math.round(mandatoryCoverageRaw * 100)}%`
      : '-'
  const matchedAlternatives = readMatchedAlternatives(pickerBreakdown?.matched_alternatives)
  const satisfiedRequiredGroups = readRequirementGroupLabels(pickerBreakdown, 'satisfied_required_groups').map((label) =>
    summarizePickerGroupResult(label, matchedAlternatives),
  )
  const unmetRequiredGroups = readRequirementGroupLabels(pickerBreakdown, 'unmet_required_groups')
  const versionUnverified = readRequirementGroupLabels(pickerBreakdown, 'version_unverified')
  const displayLocation = pickDisplayLocation(structuredRequirements?.locations ?? [])
  const isRawView = viewMode === 'v1'
  return (
    <div className="parserDetailsSection">
      <button type="button" className="parserDetailsToggle" onClick={() => onToggle(candidateId)}>
        {expanded ? 'Hide Details' : 'View Details'}
      </button>
      {expanded ? (
        <div className="parserDetailsPanel">
          <div className="parserDetailsHeader">
            <div className="parserDetailsMeta">
              <span><strong>Parser Version:</strong> {normalized.parser_version ?? '-'}</span>
              <span><strong>Source:</strong> {normalized.source ?? source}</span>
              <span><strong>Mode:</strong> {parserMode}</span>
              <span><strong>Fallback Used:</strong> {fallbackUsed ? 'Yes' : 'No'}</span>
            </div>
            <div className="parserViewToggle" role="tablist" aria-label="Details view mode">
              <button
                type="button"
                className={!isRawView ? 'parserViewToggleButton parserViewToggleButtonActive' : 'parserViewToggleButton'}
                aria-pressed={!isRawView}
                onClick={() => setViewMode('v3')}
              >
                Readable v3
              </button>
              <button
                type="button"
                className={isRawView ? 'parserViewToggleButton parserViewToggleButtonActive' : 'parserViewToggleButton'}
                aria-pressed={isRawView}
                onClick={() => setViewMode('v1')}
              >
                Raw v1
              </button>
            </div>
          </div>
          {isRawView ? (
            <>
              <div className="parserDetailsSummaryGrid">
                <ParserDetailsCard title="Parser Status" className="parserDetailsSummaryBlock">
                  <pre className="parserLegacyPre">{renderParserValue(parserStatus)}</pre>
                </ParserDetailsCard>
                <ParserDetailsCard title="Final Skills Text" className="parserDetailsSummaryBlock">
                  <pre className="parserLegacyPre">{recordStringValue(isRecord(legacyFinalResult) ? legacyFinalResult : null, 'skills_text') || '-'}</pre>
                </ParserDetailsCard>
                <ParserDetailsCard title="Approved Skills" className="parserDetailsSummaryBlock">
                  <pre className="parserLegacyPre">{legacyApprovedSkillsText || '-'}</pre>
                </ParserDetailsCard>
                <ParserDetailsCard title="Unknown Skills" className="parserDetailsSummaryBlock">
                  <pre className="parserLegacyPre">{unknownSkills.length > 0 ? unknownSkills.join(', ') : '-'}</pre>
                </ParserDetailsCard>
                <ParserDetailsCard title="ATS Summary" className="parserDetailsSummaryBlock">
                  <pre className="parserLegacyPre">{legacyAtsSummary}</pre>
                </ParserDetailsCard>
              </div>
              <div className="parserDetailsGrid">
                <ParserDetailsCard title="Final Extracted Result">
                  <pre className="parserLegacyPre">{renderParserValue(legacyFinalResult)}</pre>
                </ParserDetailsCard>
                <ParserDetailsCard title={fallbackUsed ? 'Base Fallback Result' : 'Base Parser Result'}>
                  <pre className="parserLegacyPre">{renderParserValue(legacyBaseResult)}</pre>
                </ParserDetailsCard>
                <ParserDetailsCard title="AI Extractor Result">
                  <pre className="parserLegacyPre">{renderParserValue(aiExtractor ?? {})}</pre>
                </ParserDetailsCard>
                <ParserDetailsCard title="Skills Audit">
                  <pre className="parserLegacyPre">{renderParserValue(skillsAudit ?? {})}</pre>
                </ParserDetailsCard>
                <ParserDetailsCard title="ATS Breakdown">
                  <pre className="parserLegacyPre">{renderParserValue(atsBreakdownRecord)}</pre>
                </ParserDetailsCard>
                <ParserDetailsCard title="Source Hints">
                  <pre className="parserLegacyPre">{renderParserValue(sourceHints ?? {})}</pre>
                </ParserDetailsCard>
                <ParserDetailsCard title="AI Evidence" className="parserDetailsBlockWide">
                  <pre className="parserLegacyPre">{renderParserValue(aiEvidenceRecord)}</pre>
                </ParserDetailsCard>
                <ParserDetailsCard title="Required Requirements">
                  <pre className="parserLegacyPre">{rawRequirementGroupLines(structuredRequirements?.required_groups ?? [])}</pre>
                  <ParserRawDebug value={structuredRequirements?.required_groups ?? []} />
                </ParserDetailsCard>
                <ParserDetailsCard title="Preferred Requirements">
                  <pre className="parserLegacyPre">{rawRequirementGroupLines(structuredRequirements?.preferred_groups ?? [], true)}</pre>
                  <ParserRawDebug value={structuredRequirements?.preferred_groups ?? []} />
                </ParserDetailsCard>
                <ParserDetailsCard title="Constraints">
                  <pre className="parserLegacyPre">{rawConstraintsSummary(structuredRequirements)}</pre>
                  <ParserRawDebug
                    value={{
                      experience_years_min: structuredRequirements?.experience_years_min,
                      locations: structuredRequirements?.locations ?? [],
                      local_required: structuredRequirements?.local_required,
                      work_mode: structuredRequirements?.work_mode,
                      preferred_domains: structuredRequirements?.preferred_domains ?? [],
                    }}
                  />
                </ParserDetailsCard>
                <ParserDetailsCard title="Resume-Picker Result">
                  <pre className="parserLegacyPre">
                    {rawResumePickerSummary({
                      mandatoryStatus,
                      mandatoryCoverage,
                      satisfiedRequiredGroups,
                      unmetRequiredGroups,
                      matchedAlternatives,
                      versionUnverified,
                    })}
                  </pre>
                  <ParserRawDebug value={pickerBreakdown ?? {}} />
                </ParserDetailsCard>
              </div>
            </>
          ) : (
            <>
              <div className="parserDetailsSummaryGrid">
                <ParserDetailsCard title="Parser Status" className="parserDetailsSummaryBlock">
                  <div className="parserStatusBadges">
                    <span className="parserStatusBadge">{`Mode: ${parserMode}`}</span>
                    <span className="parserStatusBadge">{`Fallback: ${fallbackUsed ? 'Yes' : 'No'}`}</span>
                    {parserStatus.warning ? <span className="parserStatusBadge parserStatusBadgeWarning">{`Warning: ${parserStatus.warning}`}</span> : null}
                  </div>
                  <ParserRawDebug value={parserStatus} />
                </ParserDetailsCard>
                <ParserDetailsCard title="Final Skills Text" className="parserDetailsSummaryBlock">
                  <ParserChipList items={finalSkills} />
                </ParserDetailsCard>
                <ParserDetailsCard title="Approved Skills" className="parserDetailsSummaryBlock">
                  <ParserChipList items={approvedSkills} />
                </ParserDetailsCard>
                <ParserDetailsCard title="Unknown Skills" className="parserDetailsSummaryBlock">
                  <ParserChipList items={unknownSkills} />
                </ParserDetailsCard>
                <ParserDetailsCard title="ATS Summary" className="parserDetailsSummaryBlock">
                  <ParserMetricGrid rows={atsMetricRows} />
                </ParserDetailsCard>
              </div>
              <div className="parserDetailsGrid">
                <ParserStructuredSection title="Final Extracted Result" data={finalResult} />
                <ParserStructuredSection title={fallbackUsed ? 'Base Fallback Result' : 'Base Parser Result'} data={baseResult} />
                <ParserStructuredSection title="AI Extractor Result" data={aiExtractor ?? {}} />
                <ParserStructuredSection title="Skills Audit" data={skillsAudit ?? {}} />
                <ParserDetailsCard title="Required Requirements">
                  {structuredRequirements && structuredRequirements.required_groups.length > 0 ? (
                    <div className="parserKeyValueList">
                      {structuredRequirements.required_groups.map((group, index) => (
                        <div key={group.group_id || `required-${index}`} className="parserKeyValueRow">
                          <span className="parserLabel">{requirementModeLabel(group)}</span>
                          <span className="parserValue">{formatRequirementGroupSkills(group)}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="subtle">-</p>
                  )}
                  <ParserRawDebug value={structuredRequirements?.required_groups ?? []} />
                </ParserDetailsCard>
                <ParserDetailsCard title="Preferred Requirements">
                  {structuredRequirements && structuredRequirements.preferred_groups.length > 0 ? (
                    <div className="parserKeyValueList">
                      {structuredRequirements.preferred_groups.map((group, index) => (
                        <div key={group.group_id || `preferred-${index}`} className="parserKeyValueRow">
                          <span className="parserLabel">{requirementModeLabel(group, true)}</span>
                          <span className="parserValue">{formatRequirementGroupSkills(group)}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="subtle">-</p>
                  )}
                  <ParserRawDebug value={structuredRequirements?.preferred_groups ?? []} />
                </ParserDetailsCard>
                <ParserDetailsCard title="Constraints">
                  <ParserKeyValueList
                    record={{
                      experience:
                        structuredRequirements?.experience_years_min != null ? `${structuredRequirements.experience_years_min}+ years` : '-',
                      location: displayLocation,
                      local_candidate:
                        structuredRequirements?.local_required == null
                          ? '-'
                          : structuredRequirements.local_required
                            ? 'Required'
                            : 'Not required',
                      work_mode: structuredRequirements?.work_mode || '-',
                      preferred_experience_domain:
                        structuredRequirements && structuredRequirements.preferred_domains.length > 0
                          ? structuredRequirements.preferred_domains.join(', ')
                          : '-',
                    }}
                  />
                  <ParserRawDebug
                    value={{
                      experience_years_min: structuredRequirements?.experience_years_min,
                      locations: structuredRequirements?.locations ?? [],
                      local_required: structuredRequirements?.local_required,
                      work_mode: structuredRequirements?.work_mode,
                      preferred_domains: structuredRequirements?.preferred_domains ?? [],
                    }}
                  />
                </ParserDetailsCard>
                <ParserDetailsCard title="Resume-Picker Result">
                  <ParserKeyValueList
                    record={{
                      mandatory_gate_status: mandatoryStatus || '-',
                      mandatory_coverage: mandatoryCoverage,
                    }}
                  />
                  <div className="parserSectionGroup">
                    <div>
                      <p className="parserSectionLabel">Satisfied</p>
                      <ParserChipList items={satisfiedRequiredGroups} />
                    </div>
                    <div>
                      <p className="parserSectionLabel">Unmet</p>
                      <ParserChipList items={unmetRequiredGroups} />
                    </div>
                  </div>
                  <div className="parserSectionGroup">
                    <div>
                      <p className="parserSectionLabel">Matched Alternatives</p>
                      <ParserChipList items={Object.values(matchedAlternatives)} />
                    </div>
                    <div>
                      <p className="parserSectionLabel">Version Not Verified</p>
                      <ParserChipList items={versionUnverified} />
                    </div>
                  </div>
                  <ParserRawDebug value={pickerBreakdown ?? {}} />
                </ParserDetailsCard>
                <ParserDetailsCard title="ATS Breakdown">
                  <ParserMetricGrid rows={atsMetricRows.slice(0, 7)} />
                  <div className="parserSectionGroup">
                    <div>
                      <p className="parserSectionLabel">Matched Raw Skills</p>
                      <ParserChipList items={matchedRawSkills} />
                    </div>
                    <div>
                      <p className="parserSectionLabel">Missing Raw Skills</p>
                      <ParserChipList items={missingRawSkills} />
                    </div>
                  </div>
                  <ParserKeyValueList
                    record={Object.fromEntries(
                      Object.entries(atsBreakdownRecord).filter(([key]) => !['matched_raw_skills', 'missing_raw_skills', 'raw_overlap', 'intent_match', 'role_alignment', 'semantic_similarity', 'foundation_coverage'].includes(key)),
                    )}
                  />
                  <ParserRawDebug value={atsBreakdownRecord} />
                </ParserDetailsCard>
                <ParserStructuredSection title="Source Hints" data={sourceHints ?? {}} />
                <ParserDetailsCard title="AI Evidence" className="parserDetailsBlockWide">
                  <ParserKeyValueList record={aiEvidenceRecord} />
                  <ParserRawDebug value={aiEvidenceRecord} />
                </ParserDetailsCard>
              </div>
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}

export function formatAttachmentSize(size: number | null | undefined): string {
  const value = typeof size === 'number' && Number.isFinite(size) ? size : 0
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  if (value >= 1024) return `${Math.round(value / 1024)} KB`
  return `${value} B`
}

export function formatSettingsDate(value: string | null | undefined): string {
  if (!value) return 'Unknown'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString()
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

export const canTrustRouting = (candidate: Candidate) =>
  candidate.routing_confirmed ||
  (['safe', 'confirmed'].includes(candidate.routing_status) && candidate.routing_confidence >= 0.8)

export const sourceLabel = (source: string) =>
  source
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')

export const renderRoutingPanel = (item: Candidate) => (
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

export const renderCandidateEmails = (item: Candidate) => {
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

export function getResumeContextLabel(value: string | null | undefined): string {
  if (value === 'injected') return 'Injected'
  if (value === 'limited') return 'Limited'
  if (value === 'missing_resume') return 'Missing Resume'
  if (value === 'extract_failed') return 'Extract Failed'
  if (value === 'rules_only') return 'Rules Only'
  return 'Unknown'
}

function CcEmailList({
  label,
  emails,
  onChange,
  placeholder,
}: {
  label: string
  emails: string[]
  onChange: (emails: string[]) => void
  placeholder: string
}) {
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')

  const commit = () => {
    const result = addCcEmail(emails, draft)
    setError(result.error ?? '')
    if (result.added) {
      onChange(result.next)
      setDraft('')
    }
  }

  return (
    <label>
      {label}
      <div className="skillBox">
        {emails.map((email) => (
          <span key={email} className="skillChip">
            {email}
            <button
              type="button"
              className="chipRemove"
              onClick={() => onChange(removeCcEmail(emails, email))}
              aria-label={`Remove ${email}`}
              title={`Remove ${email}`}
            >
              x
            </button>
          </span>
        ))}
        <input
          type="email"
          className="skillInput"
          value={draft}
          aria-label={`Add ${label}`}
          onChange={(event) => {
            setDraft(event.target.value)
            if (error) setError('')
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ',') {
              event.preventDefault()
              commit()
            } else if (event.key === 'Backspace' && !draft && emails.length > 0) {
              onChange(removeCcEmail(emails, emails[emails.length - 1]))
            }
          }}
          onBlur={commit}
          placeholder={placeholder}
        />
      </div>
      {error ? <span className="subtle">{error}</span> : null}
    </label>
  )
}

// Endpoints that apply the implicit one-day mail_date scope; only these widen on text search.
const MAIL_DATE_SCOPED_BUCKETS = new Set(['needs_review', 'failed', 'approved_sent', 'recruiter_opportunities'])

function App() {
  const INITIAL_BUCKET_LIMIT = 25
  const PAGE_BUCKET_LIMIT = 25
  const RECENT_RUNS_LIMIT = 100
  const apiBase = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
  const defaultPolicy: DynamicPolicy = buildDefaultPolicy()
  const policyProfiles: Record<PolicyProfileName, DynamicPolicy> = {
    'Flexible Drafting': {
      version: 2,
      query: { force_unread: true, include_labels: [], exclude_labels: [], date_mode: 'any' },
      run: { run_mode: 'all', batch_limit: 100, dry_run: false },
      qualification: {
        location_strictness: 'lenient',
        score_threshold_override_enabled: true,
        score_threshold_override_value: 0.5,
        draft_rules: {
          recruiter_like_gmail: { mode: 'warn' },
          accepted_location: { mode: 'warn', locations: [] },
          minimum_salary: { mode: 'ignore', value: null },
          must_have_skills: { mode: 'warn', skills: [] },
          score_threshold: { mode: 'warn', value: 0.5 },
          f2f_non_texas: { mode: 'warn' },
          unknown_location: { mode: 'warn' },
          recipient_mapping: { mode: 'warn' },
        },
      },
    },
    Balanced: {
      version: 2,
      query: { force_unread: true, include_labels: [], exclude_labels: [], date_mode: 'custom' },
      run: { run_mode: 'all', batch_limit: 20, dry_run: false },
      qualification: {
        location_strictness: 'balanced',
        score_threshold_override_enabled: false,
        score_threshold_override_value: 0.6,
        draft_rules: {
          recruiter_like_gmail: { mode: 'block' },
          accepted_location: { mode: 'warn', locations: [] },
          minimum_salary: { mode: 'warn', value: null },
          must_have_skills: { mode: 'warn', skills: [] },
          score_threshold: { mode: 'warn', value: 0.6 },
          f2f_non_texas: { mode: 'block' },
          unknown_location: { mode: 'warn' },
          recipient_mapping: { mode: 'block' },
        },
      },
    },
    Strict: {
      version: 2,
      query: { force_unread: true, include_labels: [], exclude_labels: [], date_mode: 'custom' },
      run: { run_mode: 'all', batch_limit: 10, dry_run: false },
      qualification: {
        location_strictness: 'strict',
        score_threshold_override_enabled: true,
        score_threshold_override_value: 0.75,
        draft_rules: {
          recruiter_like_gmail: { mode: 'block' },
          accepted_location: { mode: 'block', locations: [] },
          minimum_salary: { mode: 'block', value: null },
          must_have_skills: { mode: 'block', skills: [] },
          score_threshold: { mode: 'block', value: 0.75 },
          f2f_non_texas: { mode: 'block' },
          unknown_location: { mode: 'block' },
          recipient_mapping: { mode: 'block' },
        },
      },
    },
  }
  const profileNames: PolicyProfileName[] = ['Flexible Drafting', 'Balanced', 'Strict']
  const [status, setStatus] = useState<GmailStatus | null>(null)
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null)
  const [chatStatus, setChatStatus] = useState<ChatStatus | null>(null)
  const [telegramStatus, setTelegramStatus] = useState<TelegramStatus | null>(null)
  const [settings, setSettingsState] = useState<SettingsPayload>({
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
    nvoids_detail_title_mode: 'job_details',
    nvoids_locations: [],
    nvoids_job_role: '',
    nvoids_search_location: '',
    nvoids_custom_query: '',
    feature_auto_send: false,
    feature_retry_queue: false,
    feature_ai_enabled: false,
    feature_ai_extractor_enabled: false,
    feature_semantic_enabled: false,
    feature_groq_job_parser_enabled: false,
    feature_gmail_requirement_groups_enabled: false,
    feature_role_manifest_enabled: false,
    feature_strict_candidate_screening_enabled: false,
    feature_email_tracking_enabled: false,
    feature_reply_inbox_enabled: false,
    feature_applications_enabled: false,
    feature_application_automation_enabled: false,
    feature_application_outreach_drafts_enabled: false,
    feature_reminder_sweep_interval_minutes: 240,
    feature_resume_tracking_enabled: false,
    feature_resume_tracking_sweep_interval_minutes: 240,
    candidate_work_authorizations: [],
    preferred_employment_types: [],
    visible_filters: {},
    preferred_minimum_rate: null,
    candidate_total_experience_years: null,
    candidate_us_experience_years: null,
    candidate_current_location: '',
    draft_text_size: 'normal',
    fallback_draft_template: '',
    signature_name: '',
    signature_phone: '',
    signature_email: '',
    preferred_employer_cc_emails: [],
    default_employer_cc_emails: [],
    preferred_employer_cc_email: '',
    resume_display_name: '',
    policy: defaultPolicy,
  })
  const settingsRef = useRef(settings)
  const setSettings = (value: SettingsPayload) => {
    settingsRef.current = value
    setSettingsState(value)
  }
  const [filterVisibilityStatus, setFilterVisibilityStatus] = useState('')
  const filterVisibilitySaveTimerRef = useRef<number | null>(null)
  const filterVisibilityStatusTimerRef = useRef<number | null>(null)
  const filterVisibilitySaveVersionRef = useRef(0)
  const [resumeFile, setResumeFile] = useState<File | null>(null)
  const [resumeSkillsInput, setResumeSkillsInput] = useState('')
  const [resumePrimaryRoleInput, setResumePrimaryRoleInput] = useState('')
  const [resumeStructuredSkillsInput, setResumeStructuredSkillsInput] = useState('')
  const [resumeVariantLabelInput, setResumeVariantLabelInput] = useState('')
  const [resumeSkillEdits, setResumeSkillEdits] = useState<Record<number, string>>({})
  const [resumeMetadataEdits, setResumeMetadataEdits] = useState<Record<number, { primary_role: string; structured_skills: string; variant_label: string }>>({})
  const [resumeUploading, setResumeUploading] = useState(false)
  const [focusResumeId, setFocusResumeId] = useState<number | null>(null)
  const [attachmentUploadFiles, setAttachmentUploadFiles] = useState<File[]>([])
  const [gmailRequirementGroups, setGmailRequirementGroups] = useState<TrustedGmailGroup[]>([])
  const [gmailGroupsBusy, setGmailGroupsBusy] = useState(false)
  const [resumeAssets, setResumeAssets] = useState<ResumeAsset[]>([])
  const resumeAssetsRef = useRef(resumeAssets)
  useEffect(() => { resumeAssetsRef.current = resumeAssets }, [resumeAssets])
  const [attachmentFiles, setAttachmentFiles] = useState<AttachmentAsset[]>([])
  const [pendingSkills, setPendingSkills] = useState<PendingSkill[]>([])
  const [pendingCompanies, setPendingCompanies] = useState<PendingEntity[]>([])
  const [pendingLocations, setPendingLocations] = useState<PendingEntity[]>([])
  const [pendingRoles, setPendingRoles] = useState<PendingEntity[]>([])
  const [embeddingPendingCount, setEmbeddingPendingCount] = useState(0)
  const [embeddingSummary, setEmbeddingSummary] = useState('')
  const [skillsLoading, setSkillsLoading] = useState(false)
  const [skillActionKey, setSkillActionKey] = useState<string | null>(null)
  const [entityActionKey, setEntityActionKey] = useState<string | null>(null)
  const [pendingJobIntentSignals, setPendingJobIntentSignals] = useState<JobIntentLearningSignal[]>([])
  const [approvedJobIntentSignals, setApprovedJobIntentSignals] = useState<JobIntentLearningSignal[]>([])
  const [embeddedJobIntentSignals, setEmbeddedJobIntentSignals] = useState<EmbeddedJobIntentSignal[]>([])
  const [jobIntentLoading, setJobIntentLoading] = useState(false)
  const [jobIntentActionKey, setJobIntentActionKey] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [nvoidsRunning, setNvoidsRunning] = useState(false)
  const [automationJob, setAutomationJob] = useState<BackgroundJob | null>(null)
  const [nvoidsJob, setNvoidsJob] = useState<BackgroundJob | null>(null)
  const [automationLiveSkipped, setAutomationLiveSkipped] = useState<RecentRunItem[]>([])
  const [nvoidsLiveSkipped, setNvoidsLiveSkipped] = useState<RecentRunItem[]>([])
  const [oauthInProgress, setOauthInProgress] = useState(false)
  const [oauthAuthorizationUrl, setOauthAuthorizationUrl] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [logs, setLogs] = useState<RecentRunCard[]>([])
  const [sendingId, setSendingId] = useState<number | null>(null)
  const [rejectingId, setRejectingId] = useState<number | null>(null)
  const [regeneratingId, setRegeneratingId] = useState<number | null>(null)
  const [movingToFailedId, setMovingToFailedId] = useState<number | null>(null)
  const [draftEdits, setDraftEdits] = useState<Record<number, string>>({})
  const [expandedParserDetailIds, setExpandedParserDetailIds] = useState<Record<number, boolean>>({})
  const [expandedSentDetailIds, setExpandedSentDetailIds] = useState<Record<number, boolean>>({})
  const [sentDetailsById, setSentDetailsById] = useState<Record<number, SentItemDetails | undefined>>({})
  const [sentDetailLoadingIds, setSentDetailLoadingIds] = useState<Record<number, boolean>>({})
  const [sentDetailErrors, setSentDetailErrors] = useState<Record<number, string | undefined>>({})
  const [routingFixes, setRoutingFixes] = useState<Record<number, { to: string; cc: string }>>({})
  const [fixingId, setFixingId] = useState<number | null>(null)
  const [deletingFailedId, setDeletingFailedId] = useState<number | null>(null)
  const [activePage, setActivePage] = useState<ActivePage>(initialActivePage)
  const [premiumTab, setPremiumTab] = useState<'inventory' | 'opportunities' | 'recycle_bin'>('inventory')
  const [applicationTrackingTab, setApplicationTrackingTab] = useState<'bookmarked' | 'tracked'>('bookmarked')
  const [resumeTrackingTab, setResumeTrackingTab] = useState<'resumes' | 'submissions'>('resumes')
  const [pageFilterValues, setPageFilterValues] = useState<Partial<Record<string, FilterValues>>>({})
  const [pageSortValues, setPageSortValues] = useState<Partial<Record<string, string>>>({})
  const [needsReviewSelected, setNeedsReviewSelected] = useState<Set<number>>(new Set())
  const [needsReviewBulkAction, setNeedsReviewBulkAction] = useState<string | null>(null)
  const [failedMappingSelected, setFailedMappingSelected] = useState<Set<number>>(new Set())
  const [failedMappingBulkAction, setFailedMappingBulkAction] = useState<string | null>(null)
  const [emailSearchTarget, setEmailSearchTarget] = useState<EmailSearchHit | null>(null)
  const [inboxConversations, setInboxConversations] = useState<ConversationSummary[]>([])
  const [selectedConversationId, setSelectedConversationId] = useState<number | null>(null)
  const [selectedConversation, setSelectedConversation] = useState<ConversationDetail | null>(null)
  const [inboxLoading, setInboxLoading] = useState(false)
  const [inboxError, setInboxError] = useState('')
  const [inboxReplyDraft, setInboxReplyDraft] = useState('')
  const [inboxSending, setInboxSending] = useState(false)
  const [lastSavedSettings, setLastSavedSettings] = useState<SettingsPayload | null>(null)
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null)
  const [dynamicPolicyBeta, setDynamicPolicyBeta] = useState(false)
  const [selectedProfileToApply, setSelectedProfileToApply] = useState<PolicyProfileName>('Balanced')
  const [lastAppliedProfile, setLastAppliedProfile] = useState<PolicyProfileName | null>(null)
  const [settingsBootstrapStatus, setSettingsBootstrapStatus] = useState<BootstrapStatus>('idle')
  const [settingsBootstrapError, setSettingsBootstrapError] = useState('')
  const [hasLoadedSettingsBootstrap, setHasLoadedSettingsBootstrap] = useState(false)
  const [hasLoadedLearningData, setHasLoadedLearningData] = useState(false)
  const [roleManifestChildCreationEnabled, setRoleManifestChildCreationEnabled] = useState(false)
  const [skillDraft, setSkillDraft] = useState('')
  const [nvoidsLocationDraft, setNvoidsLocationDraft] = useState('')
  const [acceptedLocationDraft, setAcceptedLocationDraft] = useState('')
  const [employerDomainDraft, setEmployerDomainDraft] = useState('')
  const [employerDomainError, setEmployerDomainError] = useState('')
  const [premiumPendingCount, setPremiumPendingCount] = useState(0)
  const [premiumRefreshToken, setPremiumRefreshToken] = useState(0)
  const [timeRange, setTimeRange] = useState<TimeRangeKey>('current_day')
  const [productivityEvents, setProductivityEvents] = useState<ProductivityEvent[]>([])
  const [productivityTrend, setProductivityTrend] = useState<ProductivityTrendResponse | null>(null)
  const [jobSummary, setJobSummary] = useState<JobQueueSummary | null>(null)
  const [liveReplyStatus, setLiveReplyStatus] = useState<LiveReplyStatus | null>(null)
  const datePickerRef = useRef<HTMLInputElement | null>(null)
  const lastTrackedViewRef = useRef<Record<string, number>>({})
  const hasBootstrappedCandidatesRef = useRef(false)
  const oauthPollingStartedAtRef = useRef<number | null>(null)
  const refreshTimerRef = useRef<number | null>(null)
  const filterRequestControllerRef = useRef<AbortController | null>(null)

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

  const currentPolicy: DynamicPolicy = normalizeDynamicPolicy(settings.policy ?? defaultPolicy, settings)
  const draftRules = currentPolicy.qualification.draft_rules
  const applyPolicyProfile = (profileName: PolicyProfileName) => {
    const profilePolicy = normalizeDynamicPolicy(policyProfiles[profileName], settings)
    setSettings({
      ...settings,
      accepted_locations: profilePolicy.qualification.draft_rules.accepted_location.locations ?? [],
      min_salary: profilePolicy.qualification.draft_rules.minimum_salary.value ?? null,
      must_have_skills: profilePolicy.qualification.draft_rules.must_have_skills.skills ?? [],
      qualification_threshold: profilePolicy.qualification.draft_rules.score_threshold.value ?? 0.6,
      policy: profilePolicy,
    })
    setLastAppliedProfile(profileName)
  }
  const updateRuleMode = (key: keyof DraftRules, mode: RuleMode) => {
    setSettings({
      ...settings,
      policy: {
        ...currentPolicy,
        qualification: {
          ...currentPolicy.qualification,
          draft_rules: {
            ...draftRules,
            [key]: {
              ...draftRules[key],
              mode,
            },
          },
        },
      },
    })
  }
  const updateRuleValue = (key: 'accepted_location' | 'minimum_salary' | 'must_have_skills' | 'score_threshold', value: string) => {
    if (key === 'accepted_location') {
      const locations = value.split(',').map((part) => part.trim()).filter(Boolean)
      setSettings({
        ...settings,
        accepted_locations: locations,
        policy: {
          ...currentPolicy,
          qualification: {
            ...currentPolicy.qualification,
            draft_rules: {
              ...draftRules,
              accepted_location: {
                ...draftRules.accepted_location,
                locations,
              },
            },
          },
        },
      })
      return
    }
    if (key === 'minimum_salary') {
      const nextValue = value.trim() === '' ? null : Number(value)
      setSettings({
        ...settings,
        min_salary: Number.isNaN(nextValue as number) ? null : nextValue,
        policy: {
          ...currentPolicy,
          qualification: {
            ...currentPolicy.qualification,
            draft_rules: {
              ...draftRules,
              minimum_salary: {
                ...draftRules.minimum_salary,
                value: Number.isNaN(nextValue as number) ? null : nextValue,
              },
            },
          },
        },
      })
      return
    }
    if (key === 'must_have_skills') {
      const skills = value.split(',').map((part) => part.trim()).filter(Boolean)
      setSettings({
        ...settings,
        must_have_skills: skills,
        policy: {
          ...currentPolicy,
          qualification: {
            ...currentPolicy.qualification,
            draft_rules: {
              ...draftRules,
              must_have_skills: {
                ...draftRules.must_have_skills,
                skills,
              },
            },
          },
        },
      })
      return
    }
    const nextValue = value.trim() === '' ? null : Number(value)
    setSettings({
      ...settings,
      qualification_threshold: Number.isNaN(nextValue as number) || nextValue == null ? settings.qualification_threshold : nextValue,
      policy: {
        ...currentPolicy,
        qualification: {
          ...currentPolicy.qualification,
          draft_rules: {
            ...draftRules,
            score_threshold: {
              ...draftRules.score_threshold,
              value: Number.isNaN(nextValue as number) ? null : nextValue,
            },
          },
        },
      },
    })
  }
  const addAcceptedLocation = (raw: string) => {
    const location = raw.trim()
    if (!location) return
    const current = draftRules.accepted_location.locations ?? settings.accepted_locations
    if (current.some((s) => s.toLowerCase() === location.toLowerCase())) {
      setAcceptedLocationDraft('')
      return
    }
    updateRuleValue('accepted_location', [...current, location].join(','))
    setAcceptedLocationDraft('')
  }
  const removeAcceptedLocation = (locationToRemove: string) => {
    const current = draftRules.accepted_location.locations ?? settings.accepted_locations
    updateRuleValue('accepted_location', current.filter((s) => s.toLowerCase() !== locationToRemove.toLowerCase()).join(','))
  }
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
  const activeConfigurationSettings = lastSavedSettings ?? settings
  const activeConfigurationPolicy = normalizeDynamicPolicy(
    activeConfigurationSettings.policy ?? defaultPolicy,
    activeConfigurationSettings,
  )
  const activeConfigurationDraftRules = activeConfigurationPolicy.qualification.draft_rules
  const activeConfigurationProfile = activeConfigurationSettings.policy_profile_selected
    ?? detectProfileFromPolicy(activeConfigurationPolicy)
    ?? 'Custom'
  const formatRuleMode = (mode: RuleMode) => `${mode.charAt(0).toUpperCase()}${mode.slice(1)}`
  const formatBool = (value: boolean) => (value ? 'On' : 'Off')
  const truncateConfigValue = (value: string, max = 60) => {
    const trimmed = (value ?? '').trim()
    if (!trimmed) return '(none)'
    return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed
  }
  const summarizeConfigList = (values: string[], max = 6) => {
    if (!values || values.length === 0) return '(none)'
    const shown = values.slice(0, max).join(', ')
    return values.length > max ? `${shown} +${values.length - max} more` : shown
  }
  const configRow = (label: string, value: ReactNode) => (
    <div className="configSummaryRow" key={label}>
      <span className="configSummaryLabel">{label}:</span>
      <span className="configSummaryValue">{value}</span>
    </div>
  )
  const nvoidsDetailTitleModeLabel = activeConfigurationSettings.nvoids_detail_title_mode === 'hotlist_details'
    ? 'Hotlist Details'
    : activeConfigurationSettings.nvoids_detail_title_mode === 'all'
      ? 'All'
      : 'Job Details'
  const settingsBootstrapReady = settingsBootstrapStatus === 'ready'

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

  const loadChatStatus = async () => {
    setChatStatus(await getChatStatus(apiBase))
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

  const normalizeSettingsPayload = (payload: SettingsPayload): SettingsPayload => {
    return {
      ...payload,
      feature_ai_extractor_enabled: Boolean(payload.feature_ai_extractor_enabled),
      feature_semantic_enabled: Boolean(payload.feature_semantic_enabled),
      feature_groq_job_parser_enabled: Boolean(payload.feature_groq_job_parser_enabled),
      feature_gmail_requirement_groups_enabled: Boolean(payload.feature_gmail_requirement_groups_enabled),
      feature_role_manifest_enabled: Boolean(payload.feature_role_manifest_enabled),
      feature_strict_candidate_screening_enabled: Boolean(payload.feature_strict_candidate_screening_enabled),
      feature_applications_enabled: Boolean(payload.feature_applications_enabled),
      feature_application_automation_enabled: Boolean(payload.feature_application_automation_enabled),
      feature_application_outreach_drafts_enabled: Boolean(payload.feature_application_outreach_drafts_enabled),
      feature_reminder_sweep_interval_minutes: Math.max(30, Math.min(payload.feature_reminder_sweep_interval_minutes || 240, 1440)),
      feature_resume_tracking_enabled: Boolean(payload.feature_resume_tracking_enabled),
      feature_resume_tracking_sweep_interval_minutes: Math.max(30, Math.min(payload.feature_resume_tracking_sweep_interval_minutes || 240, 1440)),
      candidate_work_authorizations: payload.candidate_work_authorizations ?? [],
      preferred_employment_types: payload.preferred_employment_types ?? [],
      visible_filters: payload.visible_filters ?? {},
      preferred_minimum_rate: payload.preferred_minimum_rate ?? null,
      candidate_total_experience_years: payload.candidate_total_experience_years ?? null,
      candidate_us_experience_years: payload.candidate_us_experience_years ?? null,
      candidate_current_location: payload.candidate_current_location ?? '',
      default_gmail_query: payload.default_gmail_query || payload.gmail_query || 'is:unread',
      saved_gmail_queries: payload.saved_gmail_queries ?? [],
      default_date_mode: payload.default_date_mode === 'off' ? 'off' : 'today',
      feature_auto_poll_interval_minutes: Math.max(1, Math.min(payload.feature_auto_poll_interval_minutes || 10, 1440)),
      feature_nvoids_enabled: Boolean(payload.feature_nvoids_enabled ?? true),
      feature_nvoids_auto_sync: Boolean(payload.feature_nvoids_auto_sync ?? false),
      feature_nvoids_poll_interval_minutes: Math.max(1, Math.min(payload.feature_nvoids_poll_interval_minutes || 30, 1440)),
      nvoids_batch_limit: Math.max(1, Math.min(payload.nvoids_batch_limit || 10, 50)),
      nvoids_detail_title_mode: normalizeNvoidsDetailTitleMode(payload.nvoids_detail_title_mode),
      nvoids_locations: payload.nvoids_locations ?? [],
      nvoids_job_role: payload.nvoids_job_role ?? '',
      nvoids_search_location: payload.nvoids_search_location ?? '',
      nvoids_custom_query: payload.nvoids_custom_query ?? '',
      employer_domains: payload.employer_domains ?? [],
      draft_text_size: normalizeDraftTextSize(payload.draft_text_size),
      preferred_employer_cc_emails:
        payload.preferred_employer_cc_emails ?? (payload.preferred_employer_cc_email ? [payload.preferred_employer_cc_email] : []),
      default_employer_cc_emails: payload.default_employer_cc_emails ?? [],
      preferred_employer_cc_email:
        payload.preferred_employer_cc_emails?.[0] ?? payload.preferred_employer_cc_email ?? '',
      resume_display_name: payload.resume_display_name ?? '',
      policy: normalizeDynamicPolicy(payload.policy ?? defaultPolicy, payload),
    }
  }

  useEffect(() => () => {
    if (filterVisibilitySaveTimerRef.current != null) window.clearTimeout(filterVisibilitySaveTimerRef.current)
    if (filterVisibilityStatusTimerRef.current != null) window.clearTimeout(filterVisibilityStatusTimerRef.current)
  }, [])

  const updateVisibleFilters = (next: Record<string, string[]>) => {
    const version = ++filterVisibilitySaveVersionRef.current
    setSettings({ ...settingsRef.current, visible_filters: next })
    setPageFilterValues((previous) => {
      let updated = previous
      for (const [registryKey, preference] of Object.entries(next)) {
        const config = resolveRegistryEntry(filterSortRegistry[registryKey], { resumeAssets })
        if (!config) continue
        const current = previous[registryKey] ?? config.defaultFilterValues
        const narrowed = narrowValuesToVisible(
          visibleFieldsFor(config.fields, preference),
          current,
          config.defaultFilterValues,
        )
        if (narrowed !== current) {
          if (updated === previous) updated = { ...previous }
          updated[registryKey] = narrowed
        }
      }
      return updated
    })
    if (filterVisibilitySaveTimerRef.current != null) window.clearTimeout(filterVisibilitySaveTimerRef.current)
    if (filterVisibilityStatusTimerRef.current != null) window.clearTimeout(filterVisibilityStatusTimerRef.current)
    setFilterVisibilityStatus('Saving\u2026')
    filterVisibilitySaveTimerRef.current = window.setTimeout(async () => {
      try {
        const response = await fetch(`${apiBase}/settings/visible-filters`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ visible_filters: next }),
        })
        if (!response.ok) throw new Error('Failed to save filter visibility')
        const saved = await response.json() as SettingsPayload
        if (version !== filterVisibilitySaveVersionRef.current) return
        setSettings({ ...settingsRef.current, visible_filters: saved.visible_filters ?? next })
        setFilterVisibilityStatus('Saved')
        filterVisibilityStatusTimerRef.current = window.setTimeout(() => setFilterVisibilityStatus(''), 2000)
      } catch {
        if (version === filterVisibilitySaveVersionRef.current) setFilterVisibilityStatus("Couldn't save")
      }
    }, 600)
  }

  const applySettingsBootstrapPayload = (payload: SettingsBootstrapPayload): SettingsPayload => {
    const normalized = normalizeSettingsPayload(payload.settings)
    setSettings(normalized)
    setRoleManifestChildCreationEnabled(Boolean(payload.role_manifest_child_creation_enabled))
    setGmailRequirementGroups(payload.gmail_requirement_groups ?? [])
    setResumeAssets(payload.resumes ?? [])
    setResumeSkillEdits(Object.fromEntries((payload.resumes ?? []).map((resume) => [resume.id, resume.skills_text ?? ''])))
    setResumeMetadataEdits(Object.fromEntries((payload.resumes ?? []).map((resume) => [resume.id, { primary_role: resume.primary_role ?? '', structured_skills: (resume.structured_skills ?? []).join(', '), variant_label: resume.variant_label ?? '' }])))
    setAttachmentFiles(payload.attachments ?? [])
    setPendingSkills(payload.pending_skills ?? [])
    setPendingJobIntentSignals(payload.pending_job_intent_signals ?? [])
    setApprovedJobIntentSignals(payload.approved_job_intent_signals ?? [])
    setSkillsLoading(false)
    setJobIntentLoading(false)
    if (payload.settings.policy_profile_selected && profileNames.includes(payload.settings.policy_profile_selected as PolicyProfileName)) {
      setSelectedProfileToApply(payload.settings.policy_profile_selected as PolicyProfileName)
      setLastAppliedProfile(payload.settings.policy_profile_selected as PolicyProfileName)
      return normalized
    }
    const detected = detectProfileFromPolicy(normalized.policy ?? defaultPolicy)
    if (detected) {
      setSelectedProfileToApply(detected)
      setLastAppliedProfile(detected)
    }
    return normalized
  }

  const loadLearningData = useCallback(async (): Promise<void> => {
    // Only show the blanking "Loading..." placeholder on the first load — a background
    // refresh after Approve/Dismiss/Toggle should update data in place, not collapse the
    // list and reset scroll position while data the user is looking at is still valid.
    if (!hasLoadedLearningData) {
      setSkillsLoading(true)
      setJobIntentLoading(true)
    }
    try {
      const [
        skillsResponse,
        embeddingResponse,
        companiesResponse,
        locationsResponse,
        pendingIntentResponse,
        approvedIntentResponse,
        embeddedIntentResponse,
      ] = await Promise.all([
        fetch(`${apiBase}/settings/skills/pending`),
        fetch(`${apiBase}/settings/skills/embedding-status`),
        fetch(`${apiBase}/settings/entities/company/pending`),
        fetch(`${apiBase}/settings/entities/location/pending`),
        fetch(`${apiBase}/settings/job-intent-learning/pending`),
        fetch(`${apiBase}/settings/job-intent-learning/approved`),
        fetch(`${apiBase}/settings/job-intent-learning/embedded`),
      ])
      if (
        !skillsResponse.ok ||
        !embeddingResponse.ok ||
        !companiesResponse.ok ||
        !locationsResponse.ok ||
        !pendingIntentResponse.ok ||
        !approvedIntentResponse.ok ||
        !embeddedIntentResponse.ok
      ) {
        throw new Error('Failed to load learning queues')
      }
      const [skills, embeddingStatus, companies, locations, pendingSignals, approvedSignals, embeddedSignals] = await Promise.all([
        skillsResponse.json() as Promise<PendingSkill[]>,
        embeddingResponse.json() as Promise<{ pending_count: number }>,
        companiesResponse.json() as Promise<PendingEntity[]>,
        locationsResponse.json() as Promise<PendingEntity[]>,
        pendingIntentResponse.json() as Promise<JobIntentLearningSignal[]>,
        approvedIntentResponse.json() as Promise<JobIntentLearningSignal[]>,
        embeddedIntentResponse.json() as Promise<EmbeddedJobIntentSignal[]>,
      ])
      setPendingSkills(skills)
      setEmbeddingPendingCount(embeddingStatus.pending_count)
      setPendingCompanies(companies)
      setPendingLocations(locations)
      setPendingJobIntentSignals(pendingSignals)
      setApprovedJobIntentSignals(approvedSignals)
      setEmbeddedJobIntentSignals(embeddedSignals)
      setHasLoadedLearningData(true)
    } finally {
      setSkillsLoading(false)
      setJobIntentLoading(false)
    }

    // Deliberately sequential, after the batch above has resolved.
    //
    // Every /settings/entities/*/pending call re-scans parser_details_json for all
    // ~8.5k candidate rows and takes 5-9s under load. Adding role harvesting to
    // that parallel batch pushed peak concurrency past what the backend would
    // serve: connections were closed mid-flight (ERR_EMPTY_RESPONSE), the
    // Promise.all rejected, and because the handler sets state only on full
    // success, *every* queue rendered empty - including companies and locations,
    // which have nothing to do with roles.
    //
    // Its own try/catch for the same reason: a role-harvest failure must not be
    // able to blank the queues that already loaded.
    try {
      const rolesResponse = await fetch(`${apiBase}/settings/entities/role/pending`)
      if (rolesResponse.ok) setPendingRoles((await rolesResponse.json()) as PendingEntity[])
    } catch {
      // Leave the roles queue empty; the rest of the learning data is still good.
    }
  }, [apiBase, hasLoadedLearningData])

  const loadSettingsBootstrap = async (): Promise<SettingsPayload> => {
    setSettingsBootstrapStatus('loading')
    setSettingsBootstrapError('')
    const res = await fetch(`${apiBase}/settings/bootstrap?include_learning_data=false`)
    if (!res.ok) {
      const message = 'Failed to load saved settings'
      setSettingsBootstrapStatus('error')
      setSettingsBootstrapError(message)
      throw new Error(message)
    }
    const payload = (await res.json()) as SettingsBootstrapPayload
    const normalized = applySettingsBootstrapPayload(payload)
    setSettingsBootstrapStatus('ready')
    setHasLoadedSettingsBootstrap(true)
    return normalized
  }

  const addTrustedGmailGroup = async (value: string, displayName: string) => {
    setGmailGroupsBusy(true)
    try {
      const res = await fetch(`${apiBase}/settings/gmail-groups`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value, display_name: displayName, enabled: true }),
      })
      if (!res.ok) throw new Error('Failed to add trusted Gmail group')
      await loadSettingsBootstrap()
    } finally {
      setGmailGroupsBusy(false)
    }
  }

  const bulkAddTrustedGmailGroups = async (values: string) => {
    setGmailGroupsBusy(true)
    try {
      const res = await fetch(`${apiBase}/settings/gmail-groups/bulk`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values }),
      })
      if (!res.ok) throw new Error('Failed to bulk add trusted Gmail groups')
      await loadSettingsBootstrap()
    } finally {
      setGmailGroupsBusy(false)
    }
  }

  const updateTrustedGmailGroup = async (groupId: number, patch: { display_name?: string; enabled?: boolean }) => {
    setGmailGroupsBusy(true)
    try {
      const res = await fetch(`${apiBase}/settings/gmail-groups/${groupId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!res.ok) throw new Error('Failed to update trusted Gmail group')
      await loadSettingsBootstrap()
    } finally {
      setGmailGroupsBusy(false)
    }
  }

  const deleteTrustedGmailGroup = async (groupId: number) => {
    setGmailGroupsBusy(true)
    try {
      const res = await fetch(`${apiBase}/settings/gmail-groups/${groupId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete trusted Gmail group')
      await loadSettingsBootstrap()
    } finally {
      setGmailGroupsBusy(false)
    }
  }

  const activeResume = resumeAssets.find((item) => item.is_current) ?? null

  const loadPremiumNumbers = async (_opts?: { append?: boolean; cursor?: number | null; mailDate?: string | null }) => {
    void _opts
    setPremiumRefreshToken((value) => value + 1)
    try {
      const response = await fetch(`${apiBase}/number-review/pending-count`)
      if (response.ok) {
        const payload = await response.json() as { count: number }
        setPremiumPendingCount(payload.count)
      }
    } catch {
      // Keep the last known sidebar badge if a background refresh is temporarily unavailable.
    }
  }

  const bucketForPage = (page: typeof activePage): CandidateState | null => {
    if (page === 'run_queue' || page === 'needs_review') return 'needs_review'
    if (page === 'failed_mapping') return 'failed'
    if (page === 'sent_items') return 'approved_sent'
    return null
  }

  const activeRegistryKey = activePage === 'premium_numbers'
    ? `premium_numbers:${premiumTab}`
    : activePage === 'application_tracking'
      ? `application_tracking:${applicationTrackingTab}`
      : activePage === 'resume_tracking'
        ? `resume_tracking:${resumeTrackingTab}`
        : activePage
  const activeFilterSortConfig = useMemo(
    () => resolveRegistryEntry(filterSortRegistry[activeRegistryKey], { resumeAssets }) ?? null,
    [activeRegistryKey, resumeAssets],
  )
  const activeVisibleFields = useMemo(
    () => activeFilterSortConfig
      ? visibleFieldsFor(activeFilterSortConfig.fields, settings.visible_filters?.[activeRegistryKey])
      : [],
    [activeFilterSortConfig, activeRegistryKey, settings.visible_filters],
  )
  const rawFilterValues = activeFilterSortConfig ? pageFilterValues[activeRegistryKey] ?? activeFilterSortConfig.defaultFilterValues : {}
  const activeFilterValues = useMemo(
    () => activeFilterSortConfig
      ? narrowValuesToVisible(activeVisibleFields, rawFilterValues, activeFilterSortConfig.defaultFilterValues)
      : {},
    [activeFilterSortConfig, activeVisibleFields, rawFilterValues],
  )
  useEffect(() => {
    if (!activeFilterSortConfig || activeFilterValues === rawFilterValues) return
    setPageFilterValues((current) => ({ ...current, [activeRegistryKey]: activeFilterValues }))
  }, [activeFilterSortConfig, activeFilterValues, activeRegistryKey, rawFilterValues])
  // The backend drops the implicit one-day `mail_date` scope when a text search is active
  // (see _text_search_active in main.py), but only on the endpoints that accept mail_date.
  // Surfacing it keeps the visible "Sep 1, 2026" chip from looking like a lie.
  const dateScopeWidened = !!settings.mail_date
    && !!activeFilterSortConfig
    && MAIL_DATE_SCOPED_BUCKETS.has(activeFilterSortConfig.bucket)
    && hasActiveTextSearch(activeVisibleFields, activeFilterValues)
  const activeSortValue = activeFilterSortConfig ? pageSortValues[activeRegistryKey] ?? activeFilterSortConfig.sortOptions[0]?.value ?? 'newest' : 'newest'
  const activeQueryOptions = useCallback((): CandidateQueryOptions | undefined => activeFilterSortConfig ? { sort: activeSortValue, filters: activeFilterSortConfig.toParams(activeFilterValues) } : undefined, [activeFilterSortConfig, activeFilterValues, activeSortValue])

  // Registry key each candidate bucket is filtered/sorted under (mirrors bucketForPage in reverse).
  const registryKeyForBucket: Record<CandidateState, string> = { needs_review: 'needs_review', failed: 'failed_mapping', approved_sent: 'sent_items' }
  // Buckets refresh independently (each may have its own sort/filter selected), so a post-mutation
  // refresh must look up each bucket's own registry entry rather than reusing activeQueryOptions()
  // (which only reflects whichever page is currently active) - otherwise refreshing e.g. Failed
  // Mapping after a Needs Review action would silently overwrite its sort with Needs Review's.
  const queryOptionsForBucket = (bucket: CandidateState): CandidateQueryOptions | undefined => {
    const key = registryKeyForBucket[bucket]
    const config = resolveRegistryEntry(filterSortRegistry[key], { resumeAssets })
    if (!config) return undefined
    const filterValues = narrowValuesToVisible(
      visibleFieldsFor(config.fields, settings.visible_filters?.[key]),
      pageFilterValues[key] ?? config.defaultFilterValues,
      config.defaultFilterValues,
    )
    const sortValue = pageSortValues[key] ?? config.sortOptions[0]?.value ?? 'newest'
    return { sort: sortValue, filters: config.toParams(filterValues) }
  }

  // Must run (and read window.location.search) before the URL-sync-write effect below, so it
  // captures the URL from actual browser navigation/popstate rather than a version the write
  // effect already rewrote this same commit using stale (not-yet-restored) tab/page state.
  useEffect(() => {
    const restore = () => {
      const params = new URLSearchParams(window.location.search)
      const page = params.get('page') as ActivePage | null
      if (!page || !ACTIVE_PAGES.has(page)) return
      const tab = params.get('tab')
      if (page === 'premium_numbers' && (tab === 'inventory' || tab === 'opportunities' || tab === 'recycle_bin')) setPremiumTab(tab)
      if (page === 'application_tracking' && (tab === 'bookmarked' || tab === 'tracked')) setApplicationTrackingTab(tab)
      if (page === 'resume_tracking' && (tab === 'resumes' || tab === 'submissions')) setResumeTrackingTab(tab)
      const key = tab && ['premium_numbers', 'application_tracking', 'resume_tracking'].includes(page) ? `${page}:${tab}` : page
      const entry = resolveRegistryEntry(filterSortRegistry[key], { resumeAssets: resumeAssetsRef.current })
      if (entry) {
        setPageFilterValues((current) => ({ ...current, [key]: (entry.fromParams ?? ((value) => parseFilterValuesFromParams(entry.fields, entry.defaultFilterValues, value)))(params) }))
        const sort = params.get('sort')
        if (sort && entry.sortOptions.some((option) => option.value === sort)) setPageSortValues((current) => ({ ...current, [key]: sort }))
      }
      setActivePage(page)
    }
    window.addEventListener('popstate', restore)
    restore()
    return () => window.removeEventListener('popstate', restore)
    // Runs once on mount plus on browser back/forward (popstate) only — resumeAssets is read
    // via a ref (see resumeAssetsRef above) so a resumeAssets reload elsewhere (e.g. after
    // Settings save) can't retrigger this and silently snap activePage back to a stale URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!activeFilterSortConfig) return
    // Deferred to a macrotask so this always runs after React (including StrictMode's
    // dev-mode double-invoke of effects) has fully settled on the current render's state.
    // Without this, StrictMode's synchronous mount->cleanup->remount cycle can run this
    // effect a second time using a stale tab value captured before the restore-on-mount
    // effect's setPremiumTab/etc had been applied, permanently overwriting the URL's tab
    // with the wrong one (window.history, unlike component state, isn't reset between
    // StrictMode's simulated passes).
    const timer = window.setTimeout(() => {
      const tab = activePage === 'premium_numbers' ? premiumTab : activePage === 'application_tracking' ? applicationTrackingTab : activePage === 'resume_tracking' ? resumeTrackingTab : null
      // Pagination position is intentionally not encoded here (0 = omit) — the registry's
      // paginationParamName for several pages is literally "page", which collides with the
      // section-navigation "page" key (?page=premium_numbers) also written by buildUrlSearch;
      // writing a real pagination value here would silently clobber the section identifier.
      const search = buildUrlSearch(activePage, tab, activeSortValue, activeFilterValues, activeFilterSortConfig, 0)
      window.history.replaceState(null, '', `${window.location.pathname}?${search}`)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [activeFilterSortConfig, activeFilterValues, activePage, activeSortValue, applicationTrackingTab, premiumTab, resumeTrackingTab])

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

  const loadJobsSummary = async () => {
    const res = await fetch(`${apiBase}/jobs/summary`)
    if (!res.ok) return
    setJobSummary((await res.json()) as JobQueueSummary)
  }

  const loadLiveReplyStatus = async () => {
    const res = await fetch(`${apiBase}/gmail/live-replies`)
    if (!res.ok) return
    setLiveReplyStatus((await res.json()) as LiveReplyStatus)
  }

  const loadRecentRuns = async (mailDate: string | null = settings.mail_date ?? null) => {
    const params = new URLSearchParams()
    params.set('limit', String(RECENT_RUNS_LIMIT))
    if (mailDate) params.set('mail_date', mailDate)
    const res = await fetch(`${apiBase}/recent-runs?${params.toString()}`)
    if (!res.ok) throw new Error('Failed to load recent runs')
    const payload = (await res.json()) as RecentRunListResponse
    setLogs(
      (payload.items ?? []).map((item) => ({
        ...item,
        email_id: item.email_id ?? null,
        skipped_items: item.skipped_items ?? [],
        skipped_items_loaded: false,
        skipped_items_loading: false,
        skipped_items_error: null,
      })),
    )
  }

  const loadInboxConversations = async (options?: { signal?: AbortSignal }): Promise<ConversationSummary[]> => {
    setInboxLoading(true)
    setInboxError('')
    try {
      const params = new URLSearchParams({ sort: activeSortValue })
      for (const [key, value] of Object.entries(activeFilterSortConfig?.toParams(activeFilterValues) ?? {})) params.set(key, value)
      const res = await fetch(`${apiBase}/inbox/conversations?${params}`, { signal: options?.signal })
      if (!res.ok) throw new Error('Failed to load inbox conversations')
      const payload = (await res.json()) as ConversationSummary[]
      setInboxConversations(payload)
      return payload
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setInboxError((e as Error).message)
      return []
    } finally {
      setInboxLoading(false)
    }
  }

  const refreshInboxReplies = async (): Promise<ConversationSummary[]> => {
    setInboxLoading(true)
    setInboxError('')
    try {
      const params = new URLSearchParams({ sort: activeSortValue })
      for (const [key, value] of Object.entries(activeFilterSortConfig?.toParams(activeFilterValues) ?? {})) params.set(key, value)
      const res = await fetch(`${apiBase}/inbox/conversations/refresh?${params}`, { method: 'POST' })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Failed to refresh conversations')
      }
      const payload = (await res.json()) as ConversationSummary[]
      setInboxConversations(payload)
      return payload
    } catch (e) {
      setInboxError((e as Error).message)
      return []
    } finally {
      setInboxLoading(false)
    }
  }

  const openInboxConversation = async (conversationId: number) => {
    setSelectedConversationId(conversationId)
    setInboxError('')
    const res = await fetch(`${apiBase}/inbox/conversations/${conversationId}`)
    if (!res.ok) {
      setInboxError('Failed to load conversation')
      return
    }
    let detail = (await res.json()) as ConversationDetail
    if (detail.unread_reply_count > 0) {
      const readRes = await fetch(`${apiBase}/inbox/conversations/${conversationId}/read`, { method: 'POST' })
      if (readRes.ok) detail = (await readRes.json()) as ConversationDetail
      setInboxConversations((rows) => rows.map((row) => (
        row.id === conversationId ? { ...row, unread_reply_count: 0 } : row
      )))
    }
    setSelectedConversation(detail)
  }

  const sendInboxReply = async () => {
    if (!selectedConversationId || !inboxReplyDraft.trim()) return
    setInboxSending(true)
    setInboxError('')
    try {
      const res = await fetch(`${apiBase}/inbox/conversations/${selectedConversationId}/reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body: inboxReplyDraft.trim() }),
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Failed to send reply')
      }
      setSelectedConversation((await res.json()) as ConversationDetail)
      setInboxReplyDraft('')
      await loadInboxConversations()
    } catch (e) {
      setInboxError((e as Error).message)
    } finally {
      setInboxSending(false)
    }
  }

  const toggleRecentRunItems = async (runKey: string | null | undefined) => {
    if (!runKey) return
    const current = logs.find((item) => item.run_key === runKey)
    if (current?.skipped_items_loaded) {
      setLogs((prev) =>
        prev.map((item) =>
          item.run_key === runKey
            ? { ...item, skipped_items_loaded: false }
            : item,
        ),
      )
      return
    }
    setLogs((prev) =>
      prev.map((item) =>
        item.run_key === runKey
          ? { ...item, skipped_items_loading: true, skipped_items_error: null }
          : item,
      ),
    )
    try {
      const res = await fetch(`${apiBase}/recent-runs/${encodeURIComponent(runKey)}/items?outcome=skipped&limit=50`)
      if (!res.ok) throw new Error('Failed to load skipped run items')
      const payload = (await res.json()) as RecentRunItemListResponse
      setLogs((prev) =>
        prev.map((item) =>
          item.run_key === runKey
            ? {
                ...item,
                skipped_items: payload.items ?? [],
                skipped_items_loaded: true,
                skipped_items_loading: false,
                skipped_items_error: null,
              }
            : item,
        ),
      )
    } catch (e) {
      setLogs((prev) =>
        prev.map((item) =>
          item.run_key === runKey
            ? {
                ...item,
                skipped_items_loading: false,
                skipped_items_error: (e as Error).message,
              }
            : item,
        ),
      )
    }
  }

  const toggleSkippedItemSelected = (runKey: string | null | undefined, itemId: number) => {
    if (!runKey) return
    setLogs((prev) =>
      prev.map((item) => {
        if (item.run_key !== runKey) return item
        const selected = item.selected_skipped_ids ?? []
        const next = selected.includes(itemId)
          ? selected.filter((id) => id !== itemId)
          : [...selected, itemId]
        return { ...item, selected_skipped_ids: next }
      }),
    )
  }

  const selectAllSkippedItems = (runKey: string | null | undefined, checked: boolean) => {
    if (!runKey) return
    setLogs((prev) =>
      prev.map((item) => {
        if (item.run_key !== runKey) return item
        const retryableIds = (item.skipped_items ?? [])
          .filter((skipped) => Boolean(skipped.external_message_id))
          .map((skipped) => skipped.id)
        return { ...item, selected_skipped_ids: checked ? retryableIds : [] }
      }),
    )
  }

  const retrySelectedSkippedItems = async (runKey: string | null | undefined) => {
    if (!runKey) return
    const current = logs.find((item) => item.run_key === runKey)
    const selected = current?.selected_skipped_ids ?? []
    if (selected.length === 0) return
    const confirmed = window.confirm(
      `Retry ${selected.length} selected email(s)? Each will be re-fetched from Gmail by message id and run through Sync + Queue again.`,
    )
    if (!confirmed) return
    setRunning(true)
    setError('')
    setLogs((prev) =>
      prev.map((item) => (item.run_key === runKey ? { ...item, retrying_skipped: true, retry_error: null } : item)),
    )
    try {
      const res = await fetch(`${apiBase}/recent-runs/skipped/retry`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skipped_item_ids: selected }),
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        const attachRunKey = details?.detail?.run_key as string | undefined
        if (details?.detail?.code === 'another_job_in_progress' && attachRunKey) {
          // Already-running job (e.g. a manual Sync + Queue): attach to it so the existing
          // poller shows real progress instead of a raw "another_job_in_progress" error.
          setAutomationJob({
            run_key: attachRunKey, job_id: details.detail.job_id ?? null, status: 'running',
            detail: 'Attaching to the automation run already in progress.',
            processed_items: 0, total_items: null, progress_pct: null, queue_name: 'automation_run',
          })
          setAutomationLiveSkipped([])
          setLogs((prev) =>
            prev.map((item) =>
              item.run_key === runKey ? { ...item, retrying_skipped: false, selected_skipped_ids: [] } : item,
            ),
          )
          return
        }
        throw new Error(typeof details?.detail === 'string' ? details.detail : 'Failed to retry selected items')
      }
      const data = (await res.json()) as JobEnqueueResponse
      setAutomationJob({
        ...data,
        detail: `Queued ${selected.length} selected email(s) for retry.`,
        processed_items: 0,
        total_items: selected.length,
        progress_pct: 0,
        queue_name: 'automation_run',
      })
      setAutomationLiveSkipped([])
      setLogs((prev) =>
        prev.map((item) =>
          item.run_key === runKey ? { ...item, retrying_skipped: false, selected_skipped_ids: [] } : item,
        ),
      )
      await loadRecentRuns(settings.mail_date ?? null)
    } catch (e) {
      setRunning(false)
      setLogs((prev) =>
        prev.map((item) =>
          item.run_key === runKey ? { ...item, retrying_skipped: false, retry_error: (e as Error).message } : item,
        ),
      )
    }
  }

  const trackViewEvent = async (page: typeof activePage) => {
    const eventMap: Record<typeof activePage, string> = {
      assistant: 'view_assistant',
      run_queue: 'view_run_queue',
      needs_review: 'view_needs_review',
      failed_mapping: 'view_failed_mapping',
      recent_runs: 'view_recent_runs',
      sent_items: 'view_sent_items',
      inbox: 'view_sent_items',
      premium_numbers: 'view_premium_numbers',
      resume_tracking: 'view_premium_numbers',
      application_tracking: 'view_premium_numbers',
      settings: 'view_run_queue',
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
      const activeBucket = bucketForPage(activePage) ?? 'needs_review'
      const bucketsToRefresh = (['needs_review', 'failed', 'approved_sent'] as CandidateState[]).filter((bucket) => bucket === activeBucket || bucketMeta[bucket].loaded)
      Promise.all(bucketsToRefresh.map((bucket) => refreshCandidates(settings.mail_date ?? null, bucket, { activeOnly: true, queryOptions: queryOptionsForBucket(bucket) }))).catch(() => {
        // Keep UI responsive if one refresh call fails; error surfaces on next action.
      })
      loadProductivityAnalytics(timeRange).catch(() => {
        // Keep UI responsive if analytics refresh fails transiently.
      })
      loadPremiumNumbers({ append: false, cursor: 0 }).catch(() => {
        // Keep UI responsive if premium numbers refresh fails transiently.
      })
      loadJobsSummary().catch(() => {
        // Keep UI responsive if job summary refresh fails transiently.
      })
    }, 200)
  }

  const runNeedsReviewBulk = async (action: 'approve' | 'regenerate' | 'reject' | 'send-to-failed-mapping') => {
    const ids = [...needsReviewSelected]
    if (!ids.length) return
    const messages = { approve: `Send ${ids.length} application${ids.length === 1 ? '' : 's'} now? This emails each recruiter directly and cannot be undone.`, regenerate: `Regenerate AI replies for ${ids.length} candidate${ids.length === 1 ? '' : 's'}? This overwrites the current draft reply for each.`, reject: `Reject ${ids.length} candidate${ids.length === 1 ? '' : 's'}?`, 'send-to-failed-mapping': `Move ${ids.length} candidate${ids.length === 1 ? '' : 's'} to Failed Mapping?` }
    let message = messages[action]
    if (action === 'approve') { const count = ids.filter((id) => draftEdits[id] !== undefined && draftEdits[id] !== queue.find((item) => item.id === id)?.draft_reply).length; if (count) message += ` ${count} of these have unsaved draft edits that will be sent as-is.` }
    if (!window.confirm(message)) return
    setNeedsReviewBulkAction(action)
    try {
      const endpoint = { approve: 'approve-bulk', regenerate: 'regenerate-bulk', reject: 'reject-bulk', 'send-to-failed-mapping': 'send-to-failed-mapping-bulk' }[action]
      const body = action === 'approve' ? { ids, edited_replies: Object.fromEntries(ids.map((id) => [id, draftEdits[id] ?? queue.find((item) => item.id === id)?.draft_reply ?? ''])), idempotency_key: crypto.randomUUID() } : { ids }
      const response = await fetch(`${apiBase}/candidates/${endpoint}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `Bulk ${action} failed`)
      const result = await response.json() as { succeeded_ids: number[]; failed: Array<{ id: number; error: string }> }
      if (result.failed.length) setError(`${result.succeeded_ids.length} succeeded, ${result.failed.length} failed: ${result.failed.map((item) => `#${item.id} (${item.error})`).join(', ')}`)
      setNeedsReviewSelected(new Set())
      schedulePostMutationRefresh()
    } catch (e) { setError((e as Error).message) } finally { setNeedsReviewBulkAction(null) }
  }

  const setBulkTracking = async (tracked: boolean) => {
    const ids = [...needsReviewSelected]
    if (!ids.length) return
    setNeedsReviewBulkAction(tracked ? 'track' : 'untrack')
    try {
      const response = await fetch(`${apiBase}/candidates/track-bulk`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids, tracked }) })
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? 'Bulk tracking update failed')
      setNeedsReviewSelected(new Set())
      schedulePostMutationRefresh()
    } catch (reason) { setError((reason as Error).message) } finally { setNeedsReviewBulkAction(null) }
  }

  const toggleTracking = async (candidateId: number) => {
    const response = await fetch(`${apiBase}/candidates/${candidateId}/track`, { method: 'POST' })
    if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? 'Tracking update failed')
    schedulePostMutationRefresh()
  }

  const runFailedMappingBulk = async (action: 'save' | 'delete') => {
    const ids = [...failedMappingSelected]
    const fixes = Object.fromEntries(ids.filter((id) => isValidEmailAddress(routingFixes[id]?.to ?? '') && isValidEmailAddress(routingFixes[id]?.cc ?? '')).map((id) => [id, { to_email: routingFixes[id].to, cc_email: routingFixes[id].cc }]))
    const ready = Object.keys(fixes).length
    if (!ids.length || (action === 'save' && !ready)) return
    if (!window.confirm(action === 'save' ? `Save routing corrections and move ${ready} candidate${ready === 1 ? '' : 's'} to Review?${ready < ids.length ? ` ${ids.length - ready} selected rows have no correction entered and will be skipped.` : ''}` : `Delete ${ids.length} failed mapping card${ids.length === 1 ? '' : 's'} from the dashboard?`)) return
    setFailedMappingBulkAction(action)
    try {
      const response = await fetch(`${apiBase}/candidates/${action === 'save' ? 'resolve-recipients-bulk' : 'delete-bulk'}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(action === 'save' ? { fixes } : { ids }) })
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `Bulk ${action} failed`)
      const result = await response.json() as { succeeded_ids: number[]; failed: Array<{ id: number; error: string }> }
      const skipped = action === 'save' ? ids.filter((id) => !(id in fixes)).map((id) => ({ id, error: 'No correction entered' })) : []
      const failed = [...result.failed, ...skipped]
      if (failed.length) setError(`${result.succeeded_ids.length} succeeded, ${failed.length} skipped: ${failed.map((item) => `#${item.id} (${item.error})`).join(', ')}`)
      setFailedMappingSelected(new Set())
      schedulePostMutationRefresh()
    } catch (e) { setError((e as Error).message) } finally { setFailedMappingBulkAction(null) }
  }

  const retrySettingsBootstrap = async () => {
    setError('')
    try {
      const normalizedSettings = await loadSettingsBootstrap()
      await loadRecentRuns(normalizedSettings.mail_date ?? null)
      await refreshVisibleCandidates(normalizedSettings.mail_date ?? null, { activeOnly: true, initialLoad: true })
      await loadPremiumNumbers({ append: false, cursor: 0, mailDate: normalizedSettings.mail_date ?? null })
      hasBootstrappedCandidatesRef.current = true
    } catch {
      // The bootstrap loader owns the user-facing error state for this path.
    }
  }

  useEffect(() => {
    const bootstrap = async () => {
      try {
        const [, , , , normalizedSettings] = await Promise.all([
          loadStatus(),
          loadAiStatus(),
          loadChatStatus().catch(() => {}),
          loadTelegramStatus(),
          loadSettingsBootstrap(),
        ])
        loadJobsSummary().catch(() => {})
        await loadRecentRuns(normalizedSettings.mail_date ?? null)
        await refreshVisibleCandidates(normalizedSettings.mail_date ?? null, { activeOnly: true, initialLoad: true })
        await loadPremiumNumbers({ append: false, cursor: 0, mailDate: normalizedSettings.mail_date ?? null })
        hasBootstrappedCandidatesRef.current = true
      } catch (e) {
        setError((e as Error).message)
      }
    }
    bootstrap().catch((e) => setError((e as Error).message))
  }, [])

  useEffect(() => {
    if (activePage !== 'settings' || hasLoadedLearningData) return
    loadLearningData().catch((e) => setError((e as Error).message))
  }, [activePage, hasLoadedLearningData, loadLearningData])

  useEffect(() => {
    if (!hasBootstrappedCandidatesRef.current || !settingsBootstrapReady) return
    refreshVisibleCandidates(settings.mail_date ?? null, { activeOnly: true, includeLoaded: true }).catch((e) => setError((e as Error).message))
    loadRecentRuns(settings.mail_date ?? null).catch((e) => setError((e as Error).message))
  }, [settings.mail_date, settingsBootstrapReady])

  useEffect(() => {
    if (!hasBootstrappedCandidatesRef.current || !settingsBootstrapReady) return
    const key = bucketForPage(activePage)
    if (!key) return
    if (bucketMeta[key].loaded) return
    loadCandidateBucket(key, settings.mail_date ?? null, {
      append: false,
      cursor: null,
      limit: INITIAL_BUCKET_LIMIT,
      markRefreshing: true,
    }).catch((e) => setError((e as Error).message))
  }, [activePage, settings.mail_date, bucketMeta.failed.loaded, bucketMeta.needs_review.loaded, bucketMeta.approved_sent.loaded, settingsBootstrapReady])

  useEffect(() => {
    if (!hasBootstrappedCandidatesRef.current || !settingsBootstrapReady || !activeFilterSortConfig) return
    filterRequestControllerRef.current?.abort()
    const controller = new AbortController()
    filterRequestControllerRef.current = controller
    const bucket = activeFilterSortConfig.bucket
    const request =
      bucket === 'inbox_conversations'
        ? loadInboxConversations({ signal: controller.signal })
        : bucket === 'needs_review' || bucket === 'failed' || bucket === 'approved_sent'
          ? refreshCandidates(settings.mail_date ?? null, bucket, { activeOnly: true, queryOptions: { ...activeQueryOptions(), signal: controller.signal } })
          : null
    request?.catch((e) => {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message)
    })
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRegistryKey, pageFilterValues[activeRegistryKey], pageSortValues[activeRegistryKey]])

  useEffect(() => setNeedsReviewSelected(new Set()), [pageFilterValues.needs_review, pageSortValues.needs_review])
  useEffect(() => setFailedMappingSelected(new Set()), [pageFilterValues.failed_mapping, pageSortValues.failed_mapping])

  useEffect(() => {
    if (!emailSearchTarget) return
    const relatedId = emailSearchRelatedId(emailSearchTarget)
    if (relatedId == null) return
    const section = emailSearchTarget.section
    if (section === 'needs_review' && bucketMeta.needs_review.hasNext && !queue.some((item) => String(item.id) === relatedId)) {
      loadMoreCandidates('needs_review', settings.mail_date ?? null, activeQueryOptions())
    } else if (section === 'failed_mapping' && bucketMeta.failed.hasNext && !failedQueue.some((item) => String(item.id) === relatedId)) {
      loadMoreCandidates('failed', settings.mail_date ?? null, activeQueryOptions())
    } else if (section === 'sent_items' && bucketMeta.approved_sent.hasNext && !sentQueue.some((item) => String(item.id) === relatedId)) {
      loadMoreCandidates('approved_sent', settings.mail_date ?? null, activeQueryOptions())
    }
  }, [emailSearchTarget, queue, failedQueue, sentQueue, bucketMeta, settings.mail_date])


  useEffect(() => {
    if (activePage !== 'inbox') return
    loadInboxConversations().then((rows) => {
      const selectedId = rows.some((row) => row.id === selectedConversationId)
        ? selectedConversationId
        : rows[0]?.id
      if (selectedId) openInboxConversation(selectedId).catch((e) => setInboxError((e as Error).message))
      else setSelectedConversation(null)
    }).catch((e) => setInboxError((e as Error).message))
  }, [activePage])

  useEffect(() => {
    loadProductivityAnalytics(timeRange).catch((e) => setError((e as Error).message))
  }, [timeRange])

  useEffect(() => {
    if (activePage !== 'run_queue') return
    loadJobsSummary().catch(() => {})
    const intervalId = window.setInterval(() => {
      loadJobsSummary().catch(() => {})
    }, 5000)
    return () => window.clearInterval(intervalId)
  }, [activePage])

  useEffect(() => {
    loadLiveReplyStatus().catch(() => {})
    const intervalId = window.setInterval(() => {
      loadLiveReplyStatus().catch(() => {})
    }, 15000)
    return () => window.clearInterval(intervalId)
  }, [])

  useEffect(() => {
    trackViewEvent(activePage)
      .catch((e) => setError((e as Error).message))
  }, [activePage])

  useEffect(() => {
    if (!emailSearchTarget) return
    const relatedId = emailSearchRelatedId(emailSearchTarget)
    if (relatedId == null) return
    const findTarget = () => Array.from(document.querySelectorAll<HTMLElement>('[data-email-search-section]')).find((element) => (
      element.dataset.emailSearchSection === emailSearchTarget.section &&
      element.dataset.emailSearchRelatedId === relatedId
    ))
    // Bucket data for a freshly-navigated section can still be loading, so retry briefly
    // instead of depending on queue/failedQueue/etc - those change on every unrelated
    // background refresh and would re-trigger this scroll long after the user moved on.
    let attempts = 0
    let timerId: number
    const tryScroll = () => {
      const target = findTarget()
      if (target) {
        target.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
        return
      }
      attempts += 1
      if (attempts < 10) timerId = window.setTimeout(tryScroll, 200)
    }
    timerId = window.setTimeout(tryScroll, 0)
    return () => window.clearTimeout(timerId)
  }, [emailSearchTarget])

  useEffect(() => {
    if (!emailSearchTarget) return
    const relatedId = emailSearchRelatedId(emailSearchTarget)
    if (relatedId == null) return
    const detach = (event: MouseEvent) => {
      const card = (event.target as HTMLElement).closest<HTMLElement>('[data-email-search-section]')
      const isCurrentCard = card?.dataset.emailSearchSection === emailSearchTarget.section
        && card?.dataset.emailSearchRelatedId === relatedId
      if (!isCurrentCard) setEmailSearchTarget(null)
    }
    document.addEventListener('mousedown', detach)
    return () => document.removeEventListener('mousedown', detach)
  }, [emailSearchTarget])

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
    const pendingJobs = [
      automationJob ? { kind: 'automation' as const, job: automationJob } : null,
      nvoidsJob ? { kind: 'nvoids' as const, job: nvoidsJob } : null,
    ].filter((item): item is { kind: 'automation' | 'nvoids'; job: BackgroundJob } => item !== null)
      .filter(({ job }) => job.status === 'queued' || job.status === 'running')
    if (pendingJobs.length === 0) return

    let canceled = false
    let timerId: number | null = null
    const poll = async () => {
      const results = await Promise.all(pendingJobs.map(async ({ kind, job }) => {
        const [statusRes, itemsRes] = await Promise.all([
          fetch(`${apiBase}/jobs/${encodeURIComponent(job.run_key)}`),
          fetch(`${apiBase}/recent-runs/${encodeURIComponent(job.run_key)}/items?outcome=skipped&limit=25`),
        ])
        if (!statusRes.ok) throw new Error(`Failed to load ${kind} job progress`)
        const items = itemsRes.ok
          ? (((await itemsRes.json()) as RecentRunItemListResponse).items ?? [])
          : []
        return { kind, job: (await statusRes.json()) as BackgroundJob, items }
      }))
      if (canceled) return

      let terminal = false
      for (const result of results) {
        const isTerminal = result.job.status !== 'queued' && result.job.status !== 'running'
        if (result.kind === 'automation') {
          setAutomationJob(result.job)
          setAutomationLiveSkipped(result.items)
          if (isTerminal) setRunning(false)
        } else {
          setNvoidsJob(result.job)
          setNvoidsLiveSkipped(result.items)
          if (isTerminal) setNvoidsRunning(false)
        }
        terminal = terminal || isTerminal
        if (result.job.status === 'failed' || result.job.status === 'canceled') {
          setError(result.job.detail)
        }
      }

      if (terminal) {
        await Promise.all([
          loadStatus(),
          loadAiStatus(),
          refreshVisibleCandidates(settings.mail_date ?? null, { activeOnly: false }),
          loadPremiumNumbers(),
          loadRecentRuns(settings.mail_date ?? null),
          loadSettingsBootstrap(),
        ])
      }
      if (!canceled && results.some(({ job }) => job.status === 'queued' || job.status === 'running')) {
        timerId = window.setTimeout(() => {
          poll().catch((e) => setError((e as Error).message))
        }, 1500)
      }
    }

    poll().catch((e) => setError((e as Error).message))
    return () => {
      canceled = true
      if (timerId !== null) window.clearTimeout(timerId)
    }
    // Polling intentionally keys only on job identity; the loop carries each fresh status forward.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [automationJob?.run_key, nvoidsJob?.run_key])

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
        body: JSON.stringify(settingsRef.current),
      })
      if (!res.ok) throw new Error('Failed to save settings')
      const savedSettings = await loadSettingsBootstrap()
      setLastSavedSettings(savedSettings)
      setLastSavedAt(new Date().toISOString())
      setActivePage('run_queue')
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
    fd.append('skills_text', resumeSkillsInput)
    fd.append('primary_role', resumePrimaryRoleInput)
    fd.append('structured_skills_text', resumeStructuredSkillsInput)
    fd.append('variant_label', resumeVariantLabelInput)
    setResumeUploading(true)
    try {
      const res = await fetch(`${apiBase}/settings/resume`, { method: 'POST', body: fd })
      if (!res.ok) throw new Error('Failed to upload resume')
      setResumeFile(null)
      setResumeSkillsInput('')
      setResumePrimaryRoleInput('')
      setResumeStructuredSkillsInput('')
      setResumeVariantLabelInput('')
      await loadSettingsBootstrap()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setResumeUploading(false)
    }
  }

  const saveResumeSkills = async (resumeId: number) => {
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/resumes/${resumeId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          skills_text: resumeSkillEdits[resumeId] ?? '',
          primary_role: resumeMetadataEdits[resumeId]?.primary_role ?? '',
          structured_skills: (resumeMetadataEdits[resumeId]?.structured_skills ?? '').split(',').map((value) => value.trim()).filter(Boolean),
          variant_label: resumeMetadataEdits[resumeId]?.variant_label ?? '',
        }),
      })
      if (!res.ok) throw new Error('Failed to save resume skills')
      await loadSettingsBootstrap()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const toggleResumeAsset = async (resumeId: number, isEnabled: boolean) => {
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/resumes/${resumeId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_enabled: isEnabled }),
      })
      if (!res.ok) throw new Error('Failed to update resume')
      await loadSettingsBootstrap()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const deleteResumeAsset = async (resumeId: number) => {
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/resumes/${resumeId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete resume')
      await loadSettingsBootstrap()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const uploadAttachmentFiles = async () => {
    if (attachmentUploadFiles.length === 0) return
    setError('')
    const fd = new FormData()
    for (const file of attachmentUploadFiles) {
      fd.append('files', file)
    }
    try {
      const res = await fetch(`${apiBase}/settings/attachments`, { method: 'POST', body: fd })
      if (!res.ok) throw new Error('Failed to upload attachment files')
      setAttachmentUploadFiles([])
      await loadSettingsBootstrap()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const toggleAttachmentFile = async (attachmentId: number, isEnabled: boolean) => {
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/attachments/${attachmentId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_enabled: isEnabled }),
      })
      if (!res.ok) throw new Error('Failed to update attachment file')
      await loadSettingsBootstrap()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const deleteAttachmentFile = async (attachmentId: number) => {
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/attachments/${attachmentId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete attachment file')
      await loadSettingsBootstrap()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const approvePendingSkill = async (skill: PendingSkill) => {
    setSkillActionKey(`approve:${skill.normalized_name}`)
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/skills/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_name: skill.skill_name }),
      })
      if (!res.ok) throw new Error('Failed to approve skill')
      await loadLearningData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSkillActionKey(null)
    }
  }

  const approveAllPendingSkills = async () => {
    setSkillActionKey('approve-all-skills')
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/skills/approve-all`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      if (!res.ok) throw new Error('Failed to approve all skills')
      await loadLearningData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSkillActionKey(null)
    }
  }

  const dismissPendingSkill = async (skill: PendingSkill) => {
    setSkillActionKey(`dismiss:${skill.normalized_name}`)
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/skills/dismiss`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_name: skill.skill_name }),
      })
      if (!res.ok) throw new Error('Failed to dismiss skill')
      await loadLearningData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSkillActionKey(null)
    }
  }

  const embedPendingSkills = async () => {
    setSkillActionKey('embed-skills')
    setEmbeddingSummary('')
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/skills/embed-pending`, { method: 'POST' })
      if (!res.ok) throw new Error('Failed to embed approved skills')
      const result = (await res.json()) as EmbedPendingSkillsResponse
      setEmbeddingSummary(`Embedded ${result.embedded_count} entries in ${(result.duration_ms / 1000).toFixed(1)}s.`)
      await loadLearningData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSkillActionKey(null)
    }
  }

  const runEntityAction = async (
    entityType: PendingEntity['entity_type'],
    action: 'approve' | 'approve-all' | 'dismiss',
    entity?: PendingEntity,
  ) => {
    setEntityActionKey(`${entityType}:${action === 'approve-all' ? 'approve-all' : entity?.normalized_name ?? action}`)
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/entities/${entityType}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: action === 'approve-all' ? undefined : JSON.stringify({ display_name: entity?.display_name }),
      })
      if (!res.ok) throw new Error(`Failed to ${action} ${entityType} candidate`)
      await loadLearningData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setEntityActionKey(null)
    }
  }

  const approvePendingJobIntentSignal = async (signal: JobIntentLearningSignal) => {
    setJobIntentActionKey(`approve-intent:${signal.id}`)
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/job-intent-learning/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phrase: signal.phrase, polarity: signal.polarity }),
      })
      if (!res.ok) throw new Error('Failed to approve job-intent signal')
      await loadLearningData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setJobIntentActionKey(null)
    }
  }

  const dismissPendingJobIntentSignal = async (signal: JobIntentLearningSignal) => {
    setJobIntentActionKey(`dismiss-intent:${signal.id}`)
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/job-intent-learning/dismiss`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phrase: signal.phrase, polarity: signal.polarity }),
      })
      if (!res.ok) throw new Error('Failed to dismiss job-intent signal')
      await loadLearningData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setJobIntentActionKey(null)
    }
  }

  const toggleJobIntentSignalPolarity = async (signal: { id: number }) => {
    setJobIntentActionKey(`toggle-polarity:${signal.id}`)
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/job-intent-learning/${signal.id}/toggle-polarity`, {
        method: 'POST',
      })
      if (!res.ok) throw new Error('Failed to change the intent signal polarity')
      await loadLearningData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setJobIntentActionKey(null)
    }
  }

  const approveAllPendingJobIntentSignals = async () => {
    setJobIntentActionKey('approve-all-intents')
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/job-intent-learning/approve-all`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      if (!res.ok) throw new Error('Failed to approve all job-intent signals')
      await loadLearningData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setJobIntentActionKey(null)
    }
  }

  const runAutomation = async () => {
    setRunning(true)
    setError('')
    try {
      const res = await fetch(`${apiBase}/jobs/automation-run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mail_date: settings.mail_date || null }),
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        const runKey = details?.detail?.run_key as string | undefined
        if (details?.detail?.code === 'another_job_in_progress' && runKey) {
          // Already-running job: attach to it so the existing poller shows real progress
          // instead of a raw "another_job_in_progress" error string.
          setAutomationJob({
            run_key: runKey, job_id: details.detail.job_id ?? null, status: 'running',
            detail: 'Attaching to the automation run already in progress.',
            processed_items: 0, total_items: null, progress_pct: null, queue_name: 'automation_run',
          })
          setAutomationLiveSkipped([])
          return
        }
        throw new Error(typeof details?.detail === 'string' ? details.detail : 'Automation run failed')
      }
      const data = (await res.json()) as JobEnqueueResponse
      setAutomationJob({
        ...data,
        detail: 'Waiting for the automation worker.',
        processed_items: 0,
        total_items: 1,
        progress_pct: 0,
        queue_name: 'automation_run',
      })
      setAutomationLiveSkipped([])
      await loadRecentRuns(settings.mail_date ?? null)
    } catch (e) {
      setError((e as Error).message)
      setRunning(false)
    }
  }

  const enabledAttachmentNames = attachmentFiles.filter((item) => item.is_enabled).map((item) => item.file_name)

  const runNvoidsSync = async () => {
    setNvoidsRunning(true)
    setError('')
    try {
      const params = new URLSearchParams()
      params.set('batch_limit', String(Math.max(1, Math.min(settings.nvoids_batch_limit || 10, 50))))
      const res = await fetch(`${apiBase}/jobs/nvoids-sync?${params.toString()}`, {
        method: 'POST',
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        const runKey = details?.detail?.run_key as string | undefined
        if (details?.detail?.code === 'another_job_in_progress' && runKey) {
          // Already-running job: attach to it so the existing poller shows real progress
          // instead of a raw "another_job_in_progress" error string.
          setNvoidsJob({
            run_key: runKey, job_id: details.detail.job_id ?? null, status: 'running',
            detail: 'Attaching to the Nvoids sync already in progress.',
            processed_items: 0, total_items: null, progress_pct: null, queue_name: 'nvoids_sync',
          })
          setNvoidsLiveSkipped([])
          return
        }
        throw new Error(typeof details?.detail === 'string' ? details.detail : 'Nvoids sync failed')
      }
      const data = (await res.json()) as JobEnqueueResponse
      setNvoidsJob({
        ...data,
        detail: 'Waiting for the Nvoids worker.',
        processed_items: 0,
        total_items: Math.max(1, Math.min(settings.nvoids_batch_limit || 10, 50)),
        progress_pct: 0,
        queue_name: 'nvoids_sync',
      })
      setNvoidsLiveSkipped([])
      await loadRecentRuns(settings.mail_date ?? null)
    } catch (e) {
      setError((e as Error).message)
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

  const approveSend = async (candidate: Candidate, confirmAdditionalSend = false) => {
    setSendingId(candidate.id)
    setError('')
    try {
      const res = await fetch(`${apiBase}/candidates/${candidate.id}/approve-send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          edited_reply: draftEdits[candidate.id] ?? candidate.draft_reply,
          confirm_same_source_additional_send: confirmAdditionalSend,
        }),
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        if (
          res.status === 409 &&
          candidate.is_multi_role_child &&
          window.confirm(`${details?.detail ?? 'Another role from this source was already sent.'}\n\nSend this additional role anyway?`)
        ) {
          await approveSend(candidate, true)
          return
        }
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

  const regenerateCandidate = async (candidateId: number) => {
    setRegeneratingId(candidateId)
    setError('')
    try {
      const requestRegeneration = async (allowRoleManifestFork: boolean): Promise<Candidate | null> => {
        const res = await fetch(`${apiBase}/candidates/${candidateId}/regenerate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            preserve_manual_routing: true,
            preserve_review_visibility: true,
            allow_role_manifest_fork: allowRoleManifestFork,
          }),
        })
        if (!res.ok) {
          const details = await res.json().catch(() => null)
          const fork = details?.detail
          if (!allowRoleManifestFork && res.status === 409 && fork?.code === 'role_manifest_fork_required') {
            const count = Number(fork.requirement_count ?? 0)
            if (!window.confirm(`This requirement contains ${count} roles and regeneration will create ${count} separate candidate cards. Continue?`)) return null
            return requestRegeneration(true)
          }
          throw new Error(typeof details?.detail === 'string' ? details.detail : 'Regenerate failed')
        }
        return await res.json() as Candidate
      }
      const updated = await requestRegeneration(false)
      if (!updated) return
      setDraftEdits((prev) => ({ ...prev, [updated.id]: updated.draft_reply ?? '' }))
      schedulePostMutationRefresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRegeneratingId(null)
    }
  }

  const retryRoleDetection = async (candidateId: number) => {
    setRegeneratingId(candidateId)
    setError('')
    try {
      const res = await fetch(`${apiBase}/candidates/${candidateId}/retry-role-detection`, { method: 'POST' })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Retry Detection failed')
      }
      schedulePostMutationRefresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRegeneratingId(null)
    }
  }

  const saveRoutingAndRequeue = async (candidateId: number) => {
    const fix = routingFixes[candidateId]
    if (!fix || !isValidEmailAddress(fix.to) || !isValidEmailAddress(fix.cc)) return
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

  const deleteFailedMapping = async (candidateId: number) => {
    const confirmed = window.confirm(
      'Delete this failed mapping card from the dashboard? This will hide it from Failed Mapping without deleting the original Gmail or Nvoids source item.',
    )
    if (!confirmed) return
    setDeletingFailedId(candidateId)
    setError('')
    try {
      const res = await fetch(`${apiBase}/candidates/${candidateId}`, {
        method: 'DELETE',
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Failed to delete failed mapping card')
      }
      await res.json() as CandidateDeleteResponse
      setRoutingFixes((prev) => {
        const next = { ...prev }
        delete next[candidateId]
        return next
      })
      schedulePostMutationRefresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setDeletingFailedId(null)
    }
  }

  const fetchSentDetails = async (candidateId: number): Promise<SentItemDetails> => {
    const res = await fetch(`${apiBase}/candidates/${candidateId}/sent-details`)
    if (!res.ok) {
      const details = await res.json().catch(() => null)
      throw new Error(details?.detail ?? 'Failed to load sent item details')
    }
    return (await res.json()) as SentItemDetails
  }

  const toggleSentDetails = async (candidateId: number) => {
    const isExpanded = Boolean(expandedSentDetailIds[candidateId])
    if (isExpanded) {
      setExpandedSentDetailIds((prev) => ({ ...prev, [candidateId]: false }))
      return
    }
    setExpandedSentDetailIds((prev) => ({ ...prev, [candidateId]: true }))
    if (sentDetailsById[candidateId] || sentDetailLoadingIds[candidateId]) return
    setSentDetailLoadingIds((prev) => ({ ...prev, [candidateId]: true }))
    setSentDetailErrors((prev) => ({ ...prev, [candidateId]: undefined }))
    try {
      const payload = await fetchSentDetails(candidateId)
      setSentDetailsById((prev) => ({ ...prev, [candidateId]: payload }))
    } catch (e) {
      setSentDetailErrors((prev) => ({ ...prev, [candidateId]: (e as Error).message }))
    } finally {
      setSentDetailLoadingIds((prev) => ({ ...prev, [candidateId]: false }))
    }
  }

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
  const groqLastDuration = aiStatus?.groq_last_duration_ms
    ? `${(aiStatus.groq_last_duration_ms / 1000).toFixed(1)}s`
    : null
  const intentGateLastDuration = aiStatus?.intent_gate_last_duration_ms
    ? `${(aiStatus.intent_gate_last_duration_ms / 1000).toFixed(1)}s`
    : null
  const trendBars: ProductivityBarPoint[] = productivityTrend?.bars ?? []
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
    if (range === 'current_week') {
      return dt.toLocaleDateString([], { weekday: 'short', day: 'numeric' })
    }
    if (range === 'current_month') {
      return dt.toLocaleDateString([], { month: 'short', day: 'numeric' })
    }
    return dt.toLocaleDateString([], { month: 'short', year: '2-digit' })
  }

  const addMustHaveSkill = (raw: string) => {
    const skill = raw.trim()
    if (!skill) return
    const exists = settings.must_have_skills.some((s) => s.toLowerCase() === skill.toLowerCase())
    if (exists) {
      setSkillDraft('')
      return
    }
    const nextSkills = [...settings.must_have_skills, skill]
    setSettings({
      ...settings,
      must_have_skills: nextSkills,
      policy: {
        ...currentPolicy,
        qualification: {
          ...currentPolicy.qualification,
          draft_rules: {
            ...draftRules,
            must_have_skills: {
              ...draftRules.must_have_skills,
              skills: nextSkills,
            },
          },
        },
      },
    })
    setSkillDraft('')
  }

  const removeMustHaveSkill = (skillToRemove: string) => {
    const nextSkills = settings.must_have_skills.filter((s) => s.toLowerCase() !== skillToRemove.toLowerCase())
    setSettings({
      ...settings,
      must_have_skills: nextSkills,
      policy: {
        ...currentPolicy,
        qualification: {
          ...currentPolicy.qualification,
          draft_rules: {
            ...draftRules,
            must_have_skills: {
              ...draftRules.must_have_skills,
              skills: nextSkills,
            },
          },
        },
      },
    })
  }

  const addNvoidsLocation = (raw: string) => {
    const location = raw.trim()
    if (!location) return
    const exists = settings.nvoids_locations.some((s) => s.toLowerCase() === location.toLowerCase())
    if (exists) {
      setNvoidsLocationDraft('')
      return
    }
    setSettings({ ...settings, nvoids_locations: [...settings.nvoids_locations, location] })
    setNvoidsLocationDraft('')
  }

  const removeNvoidsLocation = (locationToRemove: string) => {
    setSettings({
      ...settings,
      nvoids_locations: settings.nvoids_locations.filter((s) => s.toLowerCase() !== locationToRemove.toLowerCase()),
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
    const nextSettings = { ...settings, saved_gmail_queries: nextSavedQueries }
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
    await loadSettingsBootstrap()
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

  const isEmailSearchHighlight = (section: EmailSearchHit['section'], relatedId: string | number | null | undefined) => (
    emailSearchTarget?.section === section &&
    relatedId != null &&
    emailSearchRelatedId(emailSearchTarget) === String(relatedId)
  )

  const missingFocusedCandidateId = focusedCandidateMissing(
    emailSearchTarget,
    queue,
    bucketMeta.needs_review.hasNext,
  )

  // Opens one candidate in Needs Review. Deliberately reuses emailSearchTarget
  // rather than a ?focus= URL param: that machinery already auto-paginates until
  // the record loads, retries the scroll while the bucket is still fetching,
  // highlights, and clears on click-away - and a URL param would not survive
  // buildUrlSearch, which rebuilds the query string from scratch on every render.
  const focusCandidateRecord = (candidateId: number) => {
    setEmailSearchTarget({
      section: 'needs_review',
      recruiter_email_id: candidateId,
      sender: '',
      subject: '',
      state: '',
      detail: {},
      occurred_at: new Date().toISOString(),
    })
    window.history.pushState(null, '', `${window.location.pathname}?page=needs_review`)
    setActivePage('needs_review')
  }

  const navigateFromEmailSearch = (hit: EmailSearchHit) => {
    if (hit.section === 'other') return
    setEmailSearchTarget(hit)
    if (hit.section === 'inbox' && typeof hit.detail.conversation_id === 'number') {
      setSelectedConversationId(hit.detail.conversation_id)
    }
    if (hit.section === 'recent_runs' && hit.detail.recent_run_skipped_item_id != null) {
      // The skipped-item row only exists in the DOM once its parent run's
      // "Skipped Items" section is expanded - without this the scroll-to-target
      // effect never finds it.
      const runKey = typeof hit.detail.run_key === 'string' ? hit.detail.run_key : null
      const run = logs.find((item) => item.run_key === runKey)
      if (run && !run.skipped_items_loaded) void toggleRecentRunItems(runKey)
    }
    setActivePage(hit.section)
  }

  const inboxUnreadCount = inboxConversations.reduce((total, row) => total + row.unread_reply_count, 0)

  const renderQueueStatusBar = () => (
    <>
      <section className="statsGrid">
        <article className="statCard">
          <p>Needs Review</p>
          <strong>{bucketMeta.needs_review.total ?? queue.length}</strong>
        </article>
        <article className="statCard error">
          <p>Failed Mapping</p>
          <strong>{bucketMeta.failed.total ?? failedQueue.length}</strong>
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
        <EmailSearch apiBase={apiBase} onNavigate={navigateFromEmailSearch} currentSection={activePage} />
        <button
          type="button"
          className="syncBtn topBarAction"
          onClick={runNvoidsSync}
          disabled={nvoidsRunning || !settings.feature_nvoids_enabled}
        >
          {nvoidsRunning ? 'Syncing Nvoids...' : 'Sync + Queue Nvoids'}
        </button>
      </section>
      <FilterSortBar
        apiBase={apiBase}
        fields={activeVisibleFields}
        values={activeFilterValues}
        onFieldChange={(key, value) => setPageFilterValues((prev) => ({ ...prev, [activeRegistryKey]: { ...(prev[activeRegistryKey] ?? activeFilterSortConfig?.defaultFilterValues ?? {}), [key]: value } }))}
        onClear={() => setPageFilterValues((prev) => ({ ...prev, [activeRegistryKey]: activeFilterSortConfig?.defaultFilterValues ?? {} }))}
        sortOptions={activeFilterSortConfig?.sortOptions ?? []}
        sortValue={activeSortValue}
        onSortChange={(value) => setPageSortValues((prev) => ({ ...prev, [activeRegistryKey]: value }))}
        disabled={!activeFilterSortConfig}
        loading={activeFilterSortConfig?.bucket === 'inbox_conversations' ? inboxLoading : isCandidateRefreshing}
        dateScopeWidened={dateScopeWidened}
      />
      {automationJob || nvoidsJob ? (
        <section className="jobProgressGrid" aria-label="Background job progress">
          {[
            automationJob ? { job: automationJob, liveSkipped: automationLiveSkipped } : null,
            nvoidsJob ? { job: nvoidsJob, liveSkipped: nvoidsLiveSkipped } : null,
          ]
            .filter((entry): entry is { job: BackgroundJob; liveSkipped: RecentRunItem[] } => entry !== null)
            .map(({ job, liveSkipped }) => {
              const meta = jobStatusMeta(job.status)
              const isTerminal = job.status !== 'queued' && job.status !== 'running'
              const total = Math.max(job.total_items ?? job.processed_items, job.processed_items, 1)
              const skippedCount = isTerminal
                ? (job.skipped_item_count ?? liveSkipped.length)
                : liveSkipped.length
              const doneCount = Math.max(0, job.processed_items - skippedCount)
              const donePct = Math.min(100, (doneCount / total) * 100)
              const skippedPct = Math.min(100 - donePct, (skippedCount / total) * 100)
              return (
                <article className={`jobProgressCard jobProgressCard--${meta.className}`} key={job.run_key}>
                  <div>
                    <strong>{job.queue_name === 'nvoids_sync' ? 'Nvoids sync' : 'Gmail automation'}</strong>
                    <span className={`jobStatusPill jobStatusPill--${meta.className}`}>
                      <i className="jobStatusDot" aria-hidden="true" />
                      {meta.checkmark ? '✓ ' : ''}{meta.label}
                    </span>
                  </div>
                  <div>
                    <span>{job.processed_items}/{job.total_items ?? '?'} processed</span>
                    <span>{job.progress_pct ?? Math.round(donePct + skippedPct)}%</span>
                  </div>
                  <div className="jobProgressBar" role="progressbar" aria-valuenow={job.progress_pct ?? 0} aria-valuemin={0} aria-valuemax={100}>
                    <div className="jobProgressBar__segment jobProgressBar__segment--done" style={{ flexBasis: `${donePct}%` }} />
                    <div className="jobProgressBar__segment jobProgressBar__segment--skipped" style={{ flexBasis: `${skippedPct}%` }} />
                    <div className="jobProgressBar__segment jobProgressBar__segment--remaining" style={{ flexBasis: `${Math.max(0, 100 - donePct - skippedPct)}%` }} />
                  </div>
                  <div className="jobProgressLegend">
                    <span><i style={{ background: '#1e9e4c' }} />Processed {doneCount}</span>
                    <span><i style={{ background: 'var(--danger)' }} />Skipped {skippedCount}</span>
                  </div>
                  <p>{job.detail}</p>
                  {liveSkipped.length > 0 ? (
                    <div className="jobLiveSkipped" aria-label="Recently skipped items">
                      {liveSkipped.slice(0, 8).map((item) => (
                        <div className="jobLiveSkippedRow" key={item.id}>
                          <p>{renderTextOrDash(item.title_or_subject)}</p>
                          <p>{renderTextOrDash(item.reason_detail || item.reason_code)}</p>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </article>
              )
            })}
        </section>
      ) : null}
    </>
  )

  return (
    // Mounted inside App rather than around it in main.tsx: App's own tests
    // render <App /> directly, so a provider above it would leave every one of
    // them without context. The trade-off is that App itself cannot call
    // useChat() - the Assistant sidebar badge reads it from a small consumer
    // rendered below this point instead. Children are left at their original
    // indentation to keep this a two-line diff rather than a 2,200-line reflow.
    <ChatProvider apiBase={apiBase} onFocusCandidate={focusCandidateRecord}>
    <main className="gmailShell">
      <SidebarWithAssistantBadge
        running={running}
        queueCount={bucketMeta.needs_review.total ?? queue.length}
        failedCount={bucketMeta.failed.total ?? failedQueue.length}
        runCount={logs.length}
        sentCount={bucketMeta.approved_sent.total ?? sentQueue.length}
        inboxCount={inboxUnreadCount}
        premiumCount={premiumPendingCount}
        resumeTrackingEnabled={settings.feature_resume_tracking_enabled}
        applicationsEnabled={settings.feature_applications_enabled}
        activePage={activePage}
        onNavigate={(page) => { window.history.pushState(null, '', `${window.location.pathname}?page=${page}`); setActivePage(page) }}
      />

      <section className="mainPane">
        <header className="topHeader">
          <div className="headerSearches">
            <div className="topSearch">
              <label htmlFor="gmail-sync-query" className="visuallyHidden">Gmail sync query</label>
              <input
                id="gmail-sync-query"
                className="search"
                value={hasLoadedSettingsBootstrap ? settings.gmail_query : ''}
                onChange={(e) => setSettings({ ...settings, gmail_query: e.target.value })}
                placeholder={hasLoadedSettingsBootstrap ? 'Gmail sync query...' : 'Loading saved settings...'}
                disabled={!hasLoadedSettingsBootstrap}
              />
            </div>
          </div>
          <div className="topActions">
            <button type="button" className="btnMuted">Batch Queue</button>
            <button
              type="button"
              className="btnMuted"
              onClick={runNvoidsSync}
              disabled={nvoidsRunning || !settings.feature_nvoids_enabled}
            >
              {nvoidsRunning ? 'Nvoids Syncing...' : 'Sync Nvoids'}
            </button>
            <span className="syncNowWrap">
              <button
                type="button"
                className="btnPrimary"
                onClick={status?.authenticated ? runAutomation : connectGmail}
                disabled={running || oauthInProgress}
              >
                {running ? 'Running...' : status?.authenticated ? 'Sync Now' : oauthInProgress ? 'OAuth In Progress...' : 'Connect Gmail'}
              </button>
              {!running && liveReplyStatus && liveReplyStatus.count > 0 ? (
                <span
                  className="liveReplyBadge"
                  title={`${liveReplyStatus.count} unread in Primary inbox (approx., not confirmed recruiter replies)${liveReplyStatus.checked_at ? ` — checked ${liveReplyStatus.checked_at}` : ''}`}
                >
                  {liveReplyStatus.count > 99 ? '99+' : liveReplyStatus.count}
                </span>
              ) : null}
            </span>
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
                disabled={!hasLoadedSettingsBootstrap}
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
            <h1>{PAGE_TITLES[activePage]}</h1>
            <p>
              {PAGE_SUBTITLES[activePage]}
            </p>
          </div>

          {activePage !== 'settings' && activePage !== 'assistant' ? renderQueueStatusBar() : null}

          {activePage === 'assistant' ? <AssistantPage /> : null}

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
              <h3 className="monitorSectionTitle">Worker Queue</h3>
              <div className="monitorMeta">
                <span>Queued: {jobSummary?.queued ?? 0}</span>
                <span>Processing: {jobSummary?.processing ?? 0}</span>
                <span>Succeeded: {jobSummary?.succeeded ?? 0}</span>
                <span>Failed: {jobSummary?.failed ?? 0}</span>
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

          {activePage === 'run_queue' && hasLoadedSettingsBootstrap ? (
            <section className="liveMonitorCard configSummaryIntro" aria-label="Active Configuration Summary">
              <div className="liveMonitorHeader">
                <div>
                  <h2>Active Configuration Summary</h2>
                  <p>Every saved setting, by panel, currently loaded for automation.</p>
                </div>
              </div>
              <h3 className="monitorSectionTitle">
                {lastSavedAt ? `Last saved: ${new Date(lastSavedAt).toLocaleString()}` : 'Loaded from saved settings'}
              </h3>
            </section>
          ) : null}

          {activePage === 'run_queue' && hasLoadedSettingsBootstrap ? (
            <div className="configGrid runQueueGrid configSummaryGrid">
              <section className="liveMonitorCard configSummaryCard">
                <h3>Gmail Access</h3>
                <div className="configSummaryList">
                  {configRow('Status', status?.authenticated ? 'Authenticated' : 'Not authenticated')}
                  {configRow('Configured', status?.configured ? 'Yes' : 'No')}
                  {configRow('Account', status?.token_path ?? '-')}
                  {configRow('Last Sync', status?.last_sync_at ?? 'Never')}
                  {configRow('Telegram', telegramStatus?.polling ? 'Connected' : telegramStatus?.enabled ? 'Starting' : 'Disabled')}
                  {configRow('Authorized Chats', telegramStatus?.authorized_chats ?? 0)}
                </div>
              </section>

              <section className="liveMonitorCard configSummaryCard">
                <h3>AI Access</h3>
                <div className="configSummaryList">
                  {configRow('Provider', aiStatus?.provider ?? 'DeepSeek')}
                  {configRow('Model', aiStatus?.model ?? 'deepseek-v4-flash')}
                  {configRow('Connection', aiStatus?.connected ? 'Healthy' : 'Disconnected')}
                  {configRow('Intent Gate', formatBool(!!aiStatus?.intent_gate_enabled_in_settings))}
                  {configRow('Intent Gate Provider', aiStatus?.intent_gate_provider || 'Unknown')}
                  {configRow('Intent Gate Model', aiStatus?.intent_gate_model || 'Unknown')}
                  {configRow('Intent Gate Config', typeof aiStatus?.intent_gate_configured === 'boolean' ? (aiStatus.intent_gate_configured ? 'Configured' : 'Missing setup') : 'Unknown')}
                  {configRow('Intent Gate Runtime', typeof aiStatus?.intent_gate_runtime_healthy === 'boolean' ? (aiStatus.intent_gate_runtime_healthy ? 'Healthy' : 'Fallback') : 'Unknown')}
                  {aiStatus?.intent_gate_last_rung ? configRow('Intent Gate Rung', aiStatus.intent_gate_last_rung) : null}
                  {aiStatus?.intent_gate_last_error ? configRow('Intent Gate Error', aiStatus.intent_gate_last_error) : null}
                  {aiStatus?.intent_gate_detail ? configRow('Intent Gate Detail', aiStatus.intent_gate_detail) : null}
                  {aiStatus?.intent_gate_last_success_at ? configRow('Intent Gate Last Success', aiStatus.intent_gate_last_success_at) : null}
                  {intentGateLastDuration ? configRow('Intent Gate Duration', intentGateLastDuration) : null}
                  {configRow('Groq Enabled', formatBool(!!aiStatus?.groq_enabled_in_settings))}
                  {configRow('Groq Config', typeof aiStatus?.groq_configured === 'boolean' ? (aiStatus.groq_configured ? 'Configured' : 'Missing setup') : 'Unknown')}
                  {configRow('Groq Model', aiStatus?.groq_model ?? 'llama-3.1-8b-instant')}
                  {configRow('Groq Request Mode', aiStatus?.groq_request_mode || 'Unknown')}
                  {configRow('Groq Runtime', typeof aiStatus?.groq_runtime_healthy === 'boolean' ? (aiStatus.groq_runtime_healthy ? 'Healthy' : 'Fallback') : 'Unknown')}
                  {aiStatus?.groq_last_error ? configRow('Groq Error', aiStatus.groq_last_error) : null}
                  {aiStatus?.groq_detail ? configRow('Groq Detail', aiStatus.groq_detail) : null}
                  {aiStatus?.groq_last_success_at ? configRow('Groq Last Success', aiStatus.groq_last_success_at) : null}
                  {groqLastDuration ? configRow('Groq Duration', groqLastDuration) : null}
                  {configRow(
                    'Embedding',
                    (typeof aiStatus?.embedding_runtime_healthy === 'boolean'
                      ? (aiStatus.embedding_runtime_healthy ? 'Healthy' : 'Disconnected')
                      : typeof aiStatus?.embedding_connected === 'boolean'
                        ? (aiStatus.embedding_connected ? 'Healthy' : 'Unknown')
                        : 'Unknown')
                    + (aiStatus?.embedding_provider ? ` (${aiStatus.embedding_provider}${aiStatus.embedding_model ? ` / ${aiStatus.embedding_model}` : ''})` : ''),
                  )}
                  {configRow('Embedding Config', typeof aiStatus?.embedding_configured === 'boolean' ? (aiStatus.embedding_configured ? 'Configured' : 'Missing setup') : 'Unknown')}
                  {aiStatus?.embedding_last_error ? configRow('Embedding Error', aiStatus.embedding_last_error) : null}
                  {aiStatus?.embedding_last_success_at ? configRow('Embedding Last Success', aiStatus.embedding_last_success_at) : null}
                  {embeddingLastDuration ? configRow('Embedding Duration', embeddingLastDuration) : null}
                  {configRow('Chatbot (Ollama)', chatStatus?.enabled ? (chatStatus.ollama_running ? 'Running' : 'Not Running') : 'Disabled')}
                  {chatStatus?.model ? configRow('Ollama Model', chatStatus.model) : null}
                  {chatStatus?.mcp_status ? configRow('Ollama MCP Status', chatStatus.mcp_status) : null}
                  {chatStatus?.ollama_last_error ? configRow('Ollama Error', chatStatus.ollama_last_error) : null}
                  {chatStatus?.ollama_last_success_at ? configRow('Ollama Last Success', chatStatus.ollama_last_success_at) : null}
                  {aiStatus?.last_draft_source ? configRow('Draft Source', getDraftSourceLabel(aiStatus.last_draft_source)) : null}
                  {aiLastDuration ? configRow('Last Duration', aiLastDuration) : null}
                </div>
              </section>

              <section className="liveMonitorCard configSummaryCard">
                <h3>AI Automation Access</h3>
                <div className="configSummaryList">
                  {configRow('Enable AI Features', formatBool(activeConfigurationSettings.feature_ai_enabled))}
                  {configRow('Enable AI Extractor', formatBool(activeConfigurationSettings.feature_ai_extractor_enabled))}
                  {configRow('Enable Role Manifest Detection', formatBool(activeConfigurationSettings.feature_role_manifest_enabled))}
                  {configRow('Enable Semantic Matching', formatBool(activeConfigurationSettings.feature_semantic_enabled))}
                  {configRow('Enable AI Job Intent Gate', formatBool(activeConfigurationSettings.feature_groq_job_parser_enabled))}
                </div>
              </section>

              <section className="liveMonitorCard configSummaryCard">
                <h3>Employer Domains</h3>
                <div className="configSummaryList">
                  {configRow('Employer Domains', summarizeConfigList(activeConfigurationSettings.employer_domains))}
                </div>
              </section>

              <section className="liveMonitorCard configSummaryCard">
                <h3>Dynamic Policy</h3>
                <div className="configSummaryList">
                  {configRow('Policy Profile', activeConfigurationProfile)}
                  {configRow('Force Unread In Query', formatBool(activeConfigurationPolicy.query.force_unread))}
                  {configRow('Include Labels', summarizeConfigList(activeConfigurationPolicy.query.include_labels))}
                  {configRow('Exclude Labels', summarizeConfigList(activeConfigurationPolicy.query.exclude_labels))}
                </div>
              </section>

              <section className="liveMonitorCard configSummaryCard">
                <h3>Draft Qualification Rules</h3>
                <div className="configSummaryList">
                  {configRow('Recruiter-like Gmail Rule', formatRuleMode(activeConfigurationDraftRules.recruiter_like_gmail.mode))}
                  {configRow('Accepted Location Rule', formatRuleMode(activeConfigurationDraftRules.accepted_location.mode))}
                  {configRow('Accepted Locations', summarizeConfigList(activeConfigurationDraftRules.accepted_location.locations ?? activeConfigurationSettings.accepted_locations))}
                  {configRow('Minimum Salary Rule', formatRuleMode(activeConfigurationDraftRules.minimum_salary.mode))}
                  {configRow('Minimum Salary Or Rate', activeConfigurationDraftRules.minimum_salary.value ?? activeConfigurationSettings.min_salary ?? '(none)')}
                  {configRow('Must-have Skills Rule', formatRuleMode(activeConfigurationDraftRules.must_have_skills.mode))}
                  {configRow('Must-have Skills', summarizeConfigList(activeConfigurationDraftRules.must_have_skills.skills ?? activeConfigurationSettings.must_have_skills))}
                  {configRow('Score Threshold Rule', formatRuleMode(activeConfigurationDraftRules.score_threshold.mode))}
                  {configRow('Score Threshold Value', activeConfigurationDraftRules.score_threshold.value ?? activeConfigurationSettings.qualification_threshold)}
                  {configRow('F2F Location Rule', formatRuleMode(activeConfigurationDraftRules.f2f_non_texas.mode))}
                  {configRow('Unknown Location Rule', formatRuleMode(activeConfigurationDraftRules.unknown_location.mode))}
                  {configRow('Recipient Mapping Rule', formatRuleMode(activeConfigurationDraftRules.recipient_mapping.mode))}
                </div>
              </section>

              <section className="liveMonitorCard configSummaryCard">
                <h3>Profile Settings</h3>
                <div className="configSummaryList">
                  {configRow('Enforce Strict Candidate Screening', formatBool(activeConfigurationSettings.feature_strict_candidate_screening_enabled))}
                  {configRow('Candidate Work Authorizations', summarizeConfigList(activeConfigurationSettings.candidate_work_authorizations))}
                  {configRow('Total Experience Years', activeConfigurationSettings.candidate_total_experience_years ?? '(none)')}
                  {configRow('U.S. Experience Years', activeConfigurationSettings.candidate_us_experience_years ?? '(none)')}
                  {configRow('Current Location', truncateConfigValue(activeConfigurationSettings.candidate_current_location))}
                  {configRow('Default Query', truncateConfigValue(activeConfigurationSettings.default_gmail_query))}
                  {configRow('Default Date', activeConfigurationSettings.default_date_mode === 'today' ? 'Today (auto)' : 'Off')}
                  {configRow('Auto Run Every N Minutes', formatBool(activeConfigurationSettings.feature_auto_polling))}
                  {configRow('Auto Run Interval (minutes)', activeConfigurationSettings.feature_auto_poll_interval_minutes)}
                  {configRow('Signature Name', truncateConfigValue(activeConfigurationSettings.signature_name))}
                  {configRow('Signature Phone', truncateConfigValue(activeConfigurationSettings.signature_phone))}
                  {configRow('Signature Email', truncateConfigValue(activeConfigurationSettings.signature_email))}
                  {configRow('Resume Name', truncateConfigValue(activeConfigurationSettings.resume_display_name))}
                </div>
              </section>

              <section className="liveMonitorCard configSummaryCard">
                <h3>Execution Control</h3>
                <div className="configSummaryList">
                  {configRow('Dry Run Mode', formatBool(activeConfigurationPolicy.run.dry_run))}
                  {configRow('Auto Send Current Run Queue', formatBool(activeConfigurationSettings.feature_auto_send))}
                  {configRow('Retry Failed Queue', formatBool(activeConfigurationSettings.feature_retry_queue))}
                  {configRow('Batch Limit', activeConfigurationPolicy.run.batch_limit)}
                  {configRow('Date Mode', activeConfigurationPolicy.query.date_mode === 'custom' ? 'Use selected date' : 'Ignore selected date')}
                  {configRow('Draft Text Size', activeConfigurationSettings.draft_text_size)}
                  {configRow('Preferred Employer CCs', summarizeConfigList(activeConfigurationSettings.preferred_employer_cc_emails))}
                  {configRow('Default Employer CCs', summarizeConfigList(activeConfigurationSettings.default_employer_cc_emails))}
                  {configRow('Fallback Draft Template', truncateConfigValue(activeConfigurationSettings.fallback_draft_template, 80))}
                </div>
              </section>

              <section className="liveMonitorCard configSummaryCard">
                <h3>Nvoids Control</h3>
                <div className="configSummaryList">
                  {configRow('Enable Nvoids Pipeline', activeConfigurationSettings.feature_nvoids_enabled ? 'Enabled' : 'Disabled')}
                  {configRow('Auto Sync Nvoids', formatBool(activeConfigurationSettings.feature_nvoids_auto_sync))}
                  {configRow('Nvoids Detail Page Type', nvoidsDetailTitleModeLabel)}
                  {configRow('Preferred Nvoids Locations', summarizeConfigList(activeConfigurationSettings.nvoids_locations))}
                  {configRow('Nvoids Job Role', truncateConfigValue(activeConfigurationSettings.nvoids_job_role))}
                  {configRow('Nvoids Search Location', truncateConfigValue(activeConfigurationSettings.nvoids_search_location))}
                  {configRow('Nvoids Custom Query', truncateConfigValue(activeConfigurationSettings.nvoids_custom_query, 80))}
                </div>
              </section>
            </div>
          ) : null}

          {settingsBootstrapError ? (
            <section className="card">
              <h2>Settings Load Status</h2>
              <p className="errorMessage">{settingsBootstrapError}</p>
              {!settingsBootstrapReady ? (
                <button type="button" onClick={() => retrySettingsBootstrap().catch(() => {
                  // The retry helper owns bootstrap-specific error state.
                })}>
                  Retry Loading Settings
                </button>
              ) : null}
            </section>
          ) : null}

          {activePage === 'run_queue' && !hasLoadedSettingsBootstrap ? (
            <section className="card pageSection">
              <h2>Settings Bootstrap</h2>
              <p className="subtle">
                {settingsBootstrapStatus === 'loading'
                  ? 'Loading saved settings, resumes, attachments, and learning data...'
                  : 'Saved settings are not loaded yet. Retry loading settings to avoid showing empty defaults.'}
              </p>
              {settingsBootstrapStatus === 'error' ? (
                <button type="button" onClick={() => retrySettingsBootstrap().catch(() => {
                  // The retry helper owns bootstrap-specific error state.
                })}>
                  Retry Loading Settings
                </button>
              ) : null}
            </section>
          ) : null}

          {activePage === 'settings' && hasLoadedSettingsBootstrap ? (
            <form className="configGrid runQueueGrid" onSubmit={saveSettings}>
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
                  <div className="row"><span className="label">Model</span><span className="tag">{aiStatus?.model ?? 'deepseek-v4-flash'}</span></div>
                  <div className="row"><span className="label">Connection</span><span className="dotOk">{aiStatus?.connected ? 'Healthy' : 'Disconnected'}</span></div>
                  <div className="row"><span className="label">Intent Gate</span><span>{aiStatus?.intent_gate_enabled_in_settings ? 'On' : 'Off'}</span></div>
                  <div className="row"><span className="label">Intent Gate Provider</span><span className="tag">{aiStatus?.intent_gate_provider || 'Unknown'}</span></div>
                  <div className="row"><span className="label">Intent Gate Model</span><span className="tag">{aiStatus?.intent_gate_model || 'Unknown'}</span></div>
                  <div className="row"><span className="label">Intent Gate Config</span><span>{typeof aiStatus?.intent_gate_configured === 'boolean' ? (aiStatus.intent_gate_configured ? 'Configured' : 'Missing setup') : 'Unknown'}</span></div>
                  <div className="row"><span className="label">Intent Gate Runtime</span><span>{typeof aiStatus?.intent_gate_runtime_healthy === 'boolean' ? (aiStatus.intent_gate_runtime_healthy ? 'Healthy' : 'Fallback') : 'Unknown'}</span></div>
                  {aiStatus?.intent_gate_last_rung ? <div className="row"><span className="label">Intent Gate Rung</span><span>{aiStatus.intent_gate_last_rung}</span></div> : null}
                  {aiStatus?.intent_gate_last_error ? <div className="row"><span className="label">Intent Gate Error</span><span>{aiStatus.intent_gate_last_error}</span></div> : null}
                  {aiStatus?.intent_gate_detail ? <div className="row"><span className="label">Intent Gate Detail</span><span>{aiStatus.intent_gate_detail}</span></div> : null}
                  {aiStatus?.intent_gate_last_success_at ? <div className="row"><span className="label">Intent Gate Last Success</span><span>{aiStatus.intent_gate_last_success_at}</span></div> : null}
                  {intentGateLastDuration ? <div className="row"><span className="label">Intent Gate Duration</span><span>{intentGateLastDuration}</span></div> : null}
                  <div className="row"><span className="label">Groq Enabled</span><span>{aiStatus?.groq_enabled_in_settings ? 'On' : 'Off'}</span></div>
                  <div className="row"><span className="label">Groq Config</span><span>{typeof aiStatus?.groq_configured === 'boolean' ? (aiStatus.groq_configured ? 'Configured' : 'Missing setup') : 'Unknown'}</span></div>
                  <div className="row"><span className="label">Groq Model</span><span className="tag">{aiStatus?.groq_model ?? 'llama-3.1-8b-instant'}</span></div>
                  <div className="row"><span className="label">Groq Request Mode</span><span>{aiStatus?.groq_request_mode || 'Unknown'}</span></div>
                  <div className="row"><span className="label">Groq Runtime</span><span>{typeof aiStatus?.groq_runtime_healthy === 'boolean' ? (aiStatus.groq_runtime_healthy ? 'Healthy' : 'Fallback') : 'Unknown'}</span></div>
                  {aiStatus?.groq_last_error ? <div className="row"><span className="label">Groq Error</span><span>{aiStatus.groq_last_error}</span></div> : null}
                  {aiStatus?.groq_detail ? <div className="row"><span className="label">Groq Detail</span><span>{aiStatus.groq_detail}</span></div> : null}
                  {aiStatus?.groq_last_success_at ? <div className="row"><span className="label">Groq Last Success</span><span>{aiStatus.groq_last_success_at}</span></div> : null}
                  {groqLastDuration ? <div className="row"><span className="label">Groq Duration</span><span>{groqLastDuration}</span></div> : null}
                  <div className="row"><span className="label">Embedding</span><span className="dotOk">{typeof aiStatus?.embedding_runtime_healthy === 'boolean' ? (aiStatus.embedding_runtime_healthy ? 'Healthy' : 'Disconnected') : typeof aiStatus?.embedding_connected === 'boolean' ? (aiStatus.embedding_connected ? 'Healthy' : 'Unknown') : 'Unknown'}{aiStatus?.embedding_provider ? ` (${aiStatus.embedding_provider}${aiStatus.embedding_model ? ` / ${aiStatus.embedding_model}` : ''})` : ''}</span></div>
                  <div className="row"><span className="label">Embedding Config</span><span>{typeof aiStatus?.embedding_configured === 'boolean' ? (aiStatus.embedding_configured ? 'Configured' : 'Missing setup') : 'Unknown'}</span></div>
                  {aiStatus?.embedding_last_error ? <div className="row"><span className="label">Embedding Error</span><span>{aiStatus.embedding_last_error}</span></div> : null}
                  {aiStatus?.embedding_last_success_at ? <div className="row"><span className="label">Embedding Last Success</span><span>{aiStatus.embedding_last_success_at}</span></div> : null}
                  {embeddingLastDuration ? <div className="row"><span className="label">Embedding Duration</span><span>{embeddingLastDuration}</span></div> : null}
                  <div className="row"><span className="label">Chatbot (Ollama)</span><span>{chatStatus?.enabled ? (chatStatus.ollama_running ? 'Running' : 'Not Running') : 'Disabled'}</span></div>
                  {chatStatus?.model ? <div className="row"><span className="label">Ollama Model</span><span className="tag">{chatStatus.model}</span></div> : null}
                  {chatStatus?.mcp_status ? <div className="row"><span className="label">Ollama MCP Status</span><span>{chatStatus.mcp_status}</span></div> : null}
                  {chatStatus?.ollama_last_error ? <div className="row"><span className="label">Ollama Error</span><span>{chatStatus.ollama_last_error}</span></div> : null}
                  {chatStatus?.ollama_last_success_at ? <div className="row"><span className="label">Ollama Last Success</span><span>{chatStatus.ollama_last_success_at}</span></div> : null}
                  {aiStatus?.last_draft_source ? <div className="row"><span className="label">Draft Source</span><span>{getDraftSourceLabel(aiStatus.last_draft_source)}</span></div> : null}
                  {aiLastDuration ? <div className="row"><span className="label">Last Duration</span><span>{aiLastDuration}</span></div> : null}
                </div>
              </section>

              <section className="card">
                <h2>AI Automation Access</h2>
                <div className="stack">
                  <label className="toggleRow">
                    <span>Enable AI Features</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_ai_enabled}
                        onChange={(e) => setSettings({ ...settings, feature_ai_enabled: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <label className="toggleRow">
                    <span>Enable AI Extractor</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_ai_extractor_enabled}
                        onChange={(e) => setSettings({ ...settings, feature_ai_extractor_enabled: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <label className="toggleRow">
                    <span>Enable Role Manifest Detection</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_role_manifest_enabled}
                        onChange={(e) => setSettings({ ...settings, feature_role_manifest_enabled: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <p className="subtle">
                    {roleManifestChildCreationEnabled
                      ? 'Detection and child drafting are active at the deployment level.'
                      : 'Detection only (dark-run). Child drafts are disabled at the deployment level.'}
                  </p>
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
                  <label className="toggleRow">
                    <span>Enable AI Job Intent Gate</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_groq_job_parser_enabled}
                        onChange={(e) => setSettings({ ...settings, feature_groq_job_parser_enabled: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <p className="subtle">Lets an AI model make the final call on whether an email is a genuine requirement, a hotlist, or noise. Off leaves that decision to the rules taxonomy alone. Which model answers is shown under AI Access.</p>
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
                    onClick={() => applyPolicyProfile(selectedProfileToApply)}
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
                <h2>Draft Qualification Rules</h2>
                <div className="stack">
                  <label>
                    Recruiter-like Gmail rule
                    <select
                      value={draftRules.recruiter_like_gmail.mode}
                      onChange={(e) => updateRuleMode('recruiter_like_gmail', e.target.value as RuleMode)}
                    >
                      <option value="ignore">Ignore</option>
                      <option value="warn">Warn Only</option>
                      <option value="block">Block Draft</option>
                    </select>
                  </label>
                  <p className="subtle">Controls what happens when a Gmail message does not look recruiter or staffing related.</p>

                  <label>
                    Accepted location rule
                    <select
                      value={draftRules.accepted_location.mode}
                      onChange={(e) => updateRuleMode('accepted_location', e.target.value as RuleMode)}
                    >
                      <option value="ignore">Ignore</option>
                      <option value="warn">Warn Only</option>
                      <option value="block">Block Draft</option>
                    </select>
                  </label>
                  <label>
                    Accepted locations
                    <div className="skillBox">
                      {(draftRules.accepted_location.locations ?? settings.accepted_locations).map((location) => (
                        <span key={location} className="skillChip">
                          {location}
                          <button
                            type="button"
                            className="chipRemove"
                            onClick={() => removeAcceptedLocation(location)}
                            aria-label={`Remove ${location}`}
                            title={`Remove ${location}`}
                          >
                            x
                          </button>
                        </span>
                      ))}
                      <input
                        value={acceptedLocationDraft}
                        className="skillInput"
                        onChange={(e) => setAcceptedLocationDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ',') {
                            e.preventDefault()
                            addAcceptedLocation(acceptedLocationDraft)
                          } else if (e.key === 'Backspace' && !acceptedLocationDraft) {
                            const current = draftRules.accepted_location.locations ?? settings.accepted_locations
                            if (current.length > 0) removeAcceptedLocation(current[current.length - 1])
                          }
                        }}
                        onBlur={() => addAcceptedLocation(acceptedLocationDraft)}
                        placeholder="Add location..."
                      />
                    </div>
                  </label>
                  <p className="subtle">Uses your accepted location list and can ignore, warn, or block when parsed locations do not match.</p>

                  <label>
                    Minimum salary rule
                    <select
                      value={draftRules.minimum_salary.mode}
                      onChange={(e) => updateRuleMode('minimum_salary', e.target.value as RuleMode)}
                    >
                      <option value="ignore">Ignore</option>
                      <option value="warn">Warn Only</option>
                      <option value="block">Block Draft</option>
                    </select>
                  </label>
                  <label>
                    Minimum salary or rate
                    <input
                      type="number"
                      value={draftRules.minimum_salary.value ?? settings.min_salary ?? ''}
                      onChange={(e) => updateRuleValue('minimum_salary', e.target.value)}
                      placeholder="60"
                    />
                  </label>
                  <p className="subtle">Controls whether low rates are ignored, surfaced as warnings, or block draft creation.</p>

                  <label>
                    Must-have skills rule
                    <select
                      value={draftRules.must_have_skills.mode}
                      onChange={(e) => updateRuleMode('must_have_skills', e.target.value as RuleMode)}
                    >
                      <option value="ignore">Ignore</option>
                      <option value="warn">Warn Only</option>
                      <option value="block">Block Draft</option>
                    </select>
                  </label>
                  <label>
                    Must-have skills
                    <div className="skillBox">
                      {(draftRules.must_have_skills.skills ?? settings.must_have_skills).map((skill) => (
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
                          } else if (e.key === 'Backspace' && !skillDraft) {
                            const current = draftRules.must_have_skills.skills ?? settings.must_have_skills
                            if (current.length > 0) removeMustHaveSkill(current[current.length - 1])
                          }
                        }}
                        onBlur={() => addMustHaveSkill(skillDraft)}
                        placeholder="Add skill..."
                      />
                    </div>
                  </label>
                  <p className="subtle">Controls whether missing required skills are ignored, shown as warnings, or block drafting.</p>

                  <label>
                    Score threshold rule
                    <select
                      value={draftRules.score_threshold.mode}
                      onChange={(e) => updateRuleMode('score_threshold', e.target.value as RuleMode)}
                    >
                      <option value="ignore">Ignore</option>
                      <option value="warn">Warn Only</option>
                      <option value="block">Block Draft</option>
                    </select>
                  </label>
                  <label>
                    Score threshold value
                    <input
                      type="number"
                      min={0}
                      max={1}
                      step={0.01}
                      value={draftRules.score_threshold.value ?? settings.qualification_threshold}
                      onChange={(e) => updateRuleValue('score_threshold', e.target.value)}
                    />
                  </label>
                  <p className="subtle">Low scores can be ignored, surfaced as warnings, or block drafting.</p>

                  <label>
                    F2F location rule
                    <select
                      value={draftRules.f2f_non_texas.mode}
                      onChange={(e) => updateRuleMode('f2f_non_texas', e.target.value as RuleMode)}
                    >
                      <option value="ignore">Ignore</option>
                      <option value="warn">Warn Only</option>
                      <option value="block">Block Draft</option>
                    </select>
                  </label>
                  <p className="subtle">Controls how face-to-face roles outside your accepted locations are handled.</p>

                  <label>
                    Unknown location rule
                    <select
                      value={draftRules.unknown_location.mode}
                      onChange={(e) => updateRuleMode('unknown_location', e.target.value as RuleMode)}
                    >
                      <option value="ignore">Ignore</option>
                      <option value="warn">Warn Only</option>
                      <option value="block">Block Draft</option>
                    </select>
                  </label>
                  <p className="subtle">When strict location policy is active, unclear locations can be ignored, warned, or blocked.</p>

                  <label>
                    Recipient mapping rule
                    <select
                      value={draftRules.recipient_mapping.mode}
                      onChange={(e) => updateRuleMode('recipient_mapping', e.target.value as RuleMode)}
                    >
                      <option value="ignore">Ignore</option>
                      <option value="warn">Warn Only</option>
                      <option value="block">Block Draft</option>
                    </select>
                  </label>
                  <p className="subtle">If set to Warn Only or Ignore, drafts can still reach Needs Review with missing recipients, but approval-time send safety still blocks sending.</p>
                </div>
              </section>

              <section className="card">
                <h2>Profile Settings</h2>
                <div className="stack">
                  <h3>Candidate Eligibility Profile</h3>
                  <label className="toggleRow">
                    <span>Enforce Strict Candidate Screening</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_strict_candidate_screening_enabled}
                        onChange={(e) => setSettings({
                          ...settings,
                          feature_strict_candidate_screening_enabled: e.target.checked,
                        })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <p className="subtle">
                    Off keeps eligibility and mandatory-resume mismatches advisory so otherwise-qualified opportunities still receive ATS scoring and drafts. On blocks mismatches before scoring and sending.
                  </p>
                  <label>
                    Candidate Work Authorizations (comma separated)
                    <input
                      value={settings.candidate_work_authorizations.join(', ')}
                      onChange={(e) => setSettings({
                        ...settings,
                        candidate_work_authorizations: e.target.value.split(',').map((value) => value.trim()).filter(Boolean),
                      })}
                    />
                  </label>
                  <label>
                    Total Experience Years
                    <input
                      type="number"
                      min={0}
                      step={0.5}
                      value={settings.candidate_total_experience_years ?? ''}
                      onChange={(e) => setSettings({ ...settings, candidate_total_experience_years: e.target.value === '' ? null : Number(e.target.value) })}
                    />
                  </label>
                  <label>
                    U.S. Experience Years
                    <input
                      type="number"
                      min={0}
                      step={0.5}
                      value={settings.candidate_us_experience_years ?? ''}
                      onChange={(e) => setSettings({ ...settings, candidate_us_experience_years: e.target.value === '' ? null : Number(e.target.value) })}
                    />
                  </label>
                  <label>
                    Current Location
                    <input
                      value={settings.candidate_current_location}
                      onChange={(e) => setSettings({ ...settings, candidate_current_location: e.target.value })}
                    />
                  </label>
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
                  <label>
                    Resume Name
                    <input
                      value={settings.resume_display_name}
                      onChange={(e) => setSettings({ ...settings, resume_display_name: e.target.value })}
                      placeholder="Chaithanya Dheeraj Resume"
                    />
                  </label>
                  <p className="subtle">Used as the sent attachment name for resume variants. Review and database cards will still show the real selected variant file name.</p>
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
                  <label className="toggleRow pillRow">
                    <span>Email Open Tracking</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_email_tracking_enabled}
                        onChange={(e) => setSettings({ ...settings, feature_email_tracking_enabled: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <p className="subtle">Inert until the backend has a public HTTPS base URL and tracking secret. Opens are heuristic because mail clients proxy, cache, or block images.</p>
                  <label className="toggleRow pillRow">
                    <span>Reply Inbox</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_reply_inbox_enabled}
                        onChange={(e) => setSettings({ ...settings, feature_reply_inbox_enabled: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <p className="subtle">Checks unread Gmail on the existing polling interval and captures replies from previously sent threads before JD parsing.</p>
                  <label className="toggleRow pillRow">
                    <span>Application Tracker</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_applications_enabled}
                        onChange={(e) => setSettings({ ...settings, feature_applications_enabled: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <label className="toggleRow pillRow">
                    <span>Resume Tracking</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_resume_tracking_enabled}
                        onChange={(e) => setSettings({ ...settings, feature_resume_tracking_enabled: e.target.checked })}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <label>
                    Resume suggestion sweep (minutes)
                    <input type="number" min={30} max={1440} value={settings.feature_resume_tracking_sweep_interval_minutes} onChange={(event) => setSettings({ ...settings, feature_resume_tracking_sweep_interval_minutes: Number(event.target.value) })} />
                  </label>
                  <fieldset>
                    <legend>Preferred employment types</legend>
                    <div className="settingsCheckboxGrid">
                      {(['C2C', 'W2', '1099', 'FT'] as const).map((employmentType) => (
                        <label key={employmentType} className="checkboxLabel">
                          <input
                            type="checkbox"
                            checked={settings.preferred_employment_types.includes(employmentType)}
                            onChange={(event) => setSettings({
                              ...settings,
                              preferred_employment_types: event.target.checked
                                ? [...settings.preferred_employment_types, employmentType]
                                : settings.preferred_employment_types.filter((value) => value !== employmentType),
                            })}
                          />
                          {employmentType}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <label>
                    Preferred minimum rate
                    <input
                      type="number"
                      min={0}
                      step="any"
                      value={settings.preferred_minimum_rate ?? ''}
                      onChange={(event) => setSettings({ ...settings, preferred_minimum_rate: event.target.value === '' ? null : Number(event.target.value) })}
                    />
                  </label>
                  <p className="subtle">Shows the manual application pipeline inside Premium Numbers.</p>
                  <label className="toggleRow pillRow">
                    <span>Application Automation</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_application_automation_enabled}
                        onChange={(event) => setSettings({ ...settings, feature_application_automation_enabled: event.target.checked })}
                        disabled={!settings.feature_applications_enabled}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <p className="subtle">Requires Application Tracker. Generates reviewable reply and reminder suggestions; it never advances a stage by itself.</p>
                  <label className="toggleRow pillRow">
                    <span>AI-assisted outreach drafts</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_application_outreach_drafts_enabled}
                        onChange={(event) => setSettings({ ...settings, feature_application_outreach_drafts_enabled: event.target.checked })}
                        disabled={!settings.feature_applications_enabled}
                      />
                      <span className="toggleTrack" />
                    </span>
                  </label>
                  <p className="subtle">Uses AI only to propose editable application emails. Sending always requires a separate click.</p>
                  <label>
                    Reminder sweep interval (minutes)
                    <input
                      type="number"
                      min={30}
                      max={1440}
                      value={settings.feature_reminder_sweep_interval_minutes}
                      onChange={(event) => setSettings({ ...settings, feature_reminder_sweep_interval_minutes: Number(event.target.value) })}
                      disabled={!settings.feature_applications_enabled || !settings.feature_application_automation_enabled}
                    />
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
                    Draft Text Size
                    <select
                      value={settings.draft_text_size}
                      onChange={(e) => setSettings({ ...settings, draft_text_size: normalizeDraftTextSize(e.target.value) })}
                    >
                      <option value="small">Small</option>
                      <option value="normal">Normal</option>
                      <option value="large">Large</option>
                      <option value="huge">Huge</option>
                    </select>
                  </label>
                  <CcEmailList
                    label="Preferred Employer CCs"
                    emails={settings.preferred_employer_cc_emails}
                    onChange={(emails) => setSettings({
                      ...settings,
                      preferred_employer_cc_emails: emails,
                      preferred_employer_cc_email: emails[0] ?? '',
                    })}
                    placeholder="Add preferred CC..."
                  />
                  <p className="subtle">Added after source-derived employer contacts for Gmail, Nvoids, and future sources. Outgoing CC is capped at three unique addresses.</p>
                  <CcEmailList
                    label="Default Employer CCs"
                    emails={settings.default_employer_cc_emails}
                    onChange={(emails) => setSettings({ ...settings, default_employer_cc_emails: emails })}
                    placeholder="Add default CC..."
                  />
                  <p className="subtle">Last resort only when no source-derived or Preferred Employer CC exists. Missing values are shown in Failed Mapping.</p>
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
                  <button type="submit" disabled={saving}>{saving ? 'Saving...' : 'Save Settings'}</button>
                  <div className="stack">
                    <strong>Attachment files</strong>
                    <p className="subtle">Upload global reusable files that will be sent alongside the active resume.</p>
                    <input
                      type="file"
                      multiple
                      aria-label="Upload attachment files"
                      onChange={(e) => setAttachmentUploadFiles(Array.from(e.target.files ?? []))}
                    />
                    <button type="button" onClick={uploadAttachmentFiles} disabled={attachmentUploadFiles.length === 0}>
                      Upload Attachment Files
                    </button>
                    {attachmentFiles.length === 0 ? (
                      <p className="subtle">No extra attachment files uploaded yet.</p>
                    ) : (
                      attachmentFiles.map((attachment) => (
                        <div key={attachment.id} className="pillRow">
                          <label className="toggleRow" style={{ flex: 1 }}>
                            <span>
                              {attachment.file_name}
                              {` (${formatAttachmentSize(attachment.file_size)})`}
                            </span>
                            <span className="toggleSwitch">
                              <input
                                type="checkbox"
                                checked={attachment.is_enabled}
                                onChange={(e) => toggleAttachmentFile(attachment.id, e.target.checked)}
                              />
                              <span className="toggleTrack" />
                            </span>
                          </label>
                          <button type="button" onClick={() => deleteAttachmentFile(attachment.id)}>
                            Delete
                          </button>
                        </div>
                      ))
                    )}
                  </div>
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
                    Nvoids Detail Page Type
                    <select
                      value={settings.nvoids_detail_title_mode}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          nvoids_detail_title_mode: normalizeNvoidsDetailTitleMode(e.target.value),
                        })
                      }
                    >
                      <option value="job_details">Job Details</option>
                      <option value="hotlist_details">Hotlist Details</option>
                      <option value="all">All</option>
                    </select>
                  </label>
                  <label>
                    Preferred Nvoids Locations
                    <div className="skillBox">
                      {settings.nvoids_locations.map((location) => (
                        <span key={location} className="skillChip">
                          {location}
                          <button
                            type="button"
                            className="chipRemove"
                            onClick={() => removeNvoidsLocation(location)}
                            aria-label={`Remove ${location}`}
                            title={`Remove ${location}`}
                          >
                            x
                          </button>
                        </span>
                      ))}
                      <input
                        value={nvoidsLocationDraft}
                        className="skillInput"
                        onChange={(e) => setNvoidsLocationDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ',') {
                            e.preventDefault()
                            addNvoidsLocation(nvoidsLocationDraft)
                          } else if (e.key === 'Backspace' && !nvoidsLocationDraft && settings.nvoids_locations.length > 0) {
                            removeNvoidsLocation(settings.nvoids_locations[settings.nvoids_locations.length - 1])
                          }
                        }}
                        onBlur={() => addNvoidsLocation(nvoidsLocationDraft)}
                        placeholder="Add location..."
                      />
                    </div>
                  </label>
                  <p className="subtle">
                    Preferred Nvoids Locations filters results after they're fetched, before they're forwarded as
                    candidates. Use Search Location below to narrow the actual Nvoids search itself.
                  </p>
                  <label>
                    Nvoids Job Role
                    <input
                      value={settings.nvoids_job_role}
                      onChange={(e) => setSettings({ ...settings, nvoids_job_role: e.target.value })}
                      placeholder="e.g. AI Engineer, Machine Learning Engineer"
                    />
                  </label>
                  <label>
                    Nvoids Search Location
                    <input
                      value={settings.nvoids_search_location}
                      onChange={(e) => setSettings({ ...settings, nvoids_search_location: e.target.value })}
                      placeholder="e.g. New Jersey"
                    />
                  </label>
                  <label>
                    Nvoids Custom Query
                    <input
                      value={settings.nvoids_custom_query}
                      onChange={(e) => setSettings({ ...settings, nvoids_custom_query: e.target.value })}
                      placeholder="Overrides Job Role and Search Location when set, e.g. python and (aws or gcp)"
                    />
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
                    disabled={nvoidsRunning || !settings.feature_nvoids_enabled}
                  >
                    {nvoidsRunning ? 'Running Nvoids Sync...' : 'Run Nvoids Sync Now'}
                  </button>
                  <p className="subtle">
                    Nvoids sync is isolated from Gmail run queue, filters by the saved Nvoids locations and detail-page title type, and is serialized to avoid concurrent DB load.
                  </p>
                </div>
              </section>
            </form>
          ) : null}

          {activePage === 'settings' ? (
            <div className="configGrid runQueueGrid">
              <SkillUpgradeSection
                pendingSkills={pendingSkills}
                loading={skillsLoading}
                busySkillKey={skillActionKey}
                approveAllSkills={approveAllPendingSkills}
                approveSkill={approvePendingSkill}
                dismissSkill={dismissPendingSkill}
                embeddingPendingCount={embeddingPendingCount}
                embeddingSummary={embeddingSummary}
                embedSkills={embedPendingSkills}
              />
              <FilterVisibilitySettings
                visibleFilters={settings.visible_filters}
                onChange={updateVisibleFilters}
                resumeAssets={resumeAssets}
                savingLabel={filterVisibilityStatus}
              />
              <EntityUpgradeSection
                title="Upgrade Companies"
                pendingEntities={pendingCompanies}
                loading={skillsLoading}
                busyKey={entityActionKey?.startsWith('company:') ? entityActionKey.slice('company:'.length) : null}
                approveAll={() => runEntityAction('company', 'approve-all')}
                approve={(entity) => runEntityAction('company', 'approve', entity)}
                dismiss={(entity) => runEntityAction('company', 'dismiss', entity)}
              />
              <EntityUpgradeSection
                title="Upgrade Locations"
                pendingEntities={pendingLocations}
                loading={skillsLoading}
                busyKey={entityActionKey?.startsWith('location:') ? entityActionKey.slice('location:'.length) : null}
                approveAll={() => runEntityAction('location', 'approve-all')}
                approve={(entity) => runEntityAction('location', 'approve', entity)}
                dismiss={(entity) => runEntityAction('location', 'dismiss', entity)}
              />
              <EntityUpgradeSection
                title="Upgrade Job Roles"
                pendingEntities={pendingRoles}
                loading={skillsLoading}
                busyKey={entityActionKey?.startsWith('role:') ? entityActionKey.slice('role:'.length) : null}
                approveAll={() => runEntityAction('role', 'approve-all')}
                approve={(entity) => runEntityAction('role', 'approve', entity)}
                dismiss={(entity) => runEntityAction('role', 'dismiss', entity)}
              />
              <JobIntentLearningSection
                pendingSignals={pendingJobIntentSignals}
                approvedSignals={approvedJobIntentSignals}
                embeddedSignals={embeddedJobIntentSignals}
                loading={jobIntentLoading}
                busySignalKey={jobIntentActionKey}
                approveAllSignals={approveAllPendingJobIntentSignals}
                approveSignal={approvePendingJobIntentSignal}
                dismissSignal={dismissPendingJobIntentSignal}
                togglePolarity={toggleJobIntentSignalPolarity}
              />
              <TrustedGmailGroupsPanel
                featureEnabled={settings.feature_gmail_requirement_groups_enabled}
                groups={gmailRequirementGroups}
                busy={gmailGroupsBusy}
                onFeatureToggle={(enabled) => setSettings({ ...settings, feature_gmail_requirement_groups_enabled: enabled })}
                onAddGroup={addTrustedGmailGroup}
                onBulkAdd={bulkAddTrustedGmailGroups}
                onUpdateGroup={updateTrustedGmailGroup}
                onDeleteGroup={deleteTrustedGmailGroup}
              />
              <ResumeDatabaseSection
                activeResume={activeResume}
                resumeFile={resumeFile}
                resumeSkillsInput={resumeSkillsInput}
                resumePrimaryRoleInput={resumePrimaryRoleInput}
                resumeStructuredSkillsInput={resumeStructuredSkillsInput}
                resumeVariantLabelInput={resumeVariantLabelInput}
                resumeSkillEdits={resumeSkillEdits}
                resumeMetadataEdits={resumeMetadataEdits}
                resumeAssets={resumeAssets}
                resumeUploading={resumeUploading}
                focusResumeId={focusResumeId}
                setResumeFile={setResumeFile}
                setResumeSkillsInput={setResumeSkillsInput}
                setResumePrimaryRoleInput={setResumePrimaryRoleInput}
                setResumeStructuredSkillsInput={setResumeStructuredSkillsInput}
                setResumeVariantLabelInput={setResumeVariantLabelInput}
                setResumeSkillEdits={setResumeSkillEdits}
                setResumeMetadataEdits={setResumeMetadataEdits}
                uploadResume={uploadResume}
                saveResumeSkills={saveResumeSkills}
                toggleResumeAsset={toggleResumeAsset}
                deleteResumeAsset={deleteResumeAsset}
              />
            </div>
          ) : null}

          {error ? <p className="errorMessage">{error}</p> : null}

          {activePage === 'needs_review' ? (
            <section className="card pageSection">
          <h2>Needs Review (Manual Approval Required)</h2>
          {missingFocusedCandidateId != null ? (
            <p className="focusMissNotice" role="status">
              Record {missingFocusedCandidateId} isn't in the current filter.
              <button
                type="button"
                onClick={() => setPageFilterValues((prev) => ({ ...prev, needs_review: activeFilterSortConfig?.defaultFilterValues ?? {} }))}
              >
                Clear filters
              </button>
              <button type="button" onClick={() => setEmailSearchTarget(null)}>Dismiss</button>
            </p>
          ) : null}
          <label className="selectAllRow"><input type="checkbox" checked={queue.filter((item) => !item.is_source_parent).length > 0 && queue.filter((item) => !item.is_source_parent).every((item) => needsReviewSelected.has(item.id))} onChange={(event) => setNeedsReviewSelected(event.target.checked ? new Set(queue.filter((item) => !item.is_source_parent).map((item) => item.id)) : new Set())} /> Select all visible</label>
          <SelectionActionBar selectedCount={needsReviewSelected.size} busyKey={needsReviewBulkAction} onClearSelection={() => setNeedsReviewSelected(new Set())} actions={[{ key: 'track', label: 'Track Application', onClick: () => void setBulkTracking(true) }, { key: 'untrack', label: 'Untrack selected', onClick: () => void setBulkTracking(false) }, { key: 'approve', label: 'Approve & Send', onClick: () => void runNeedsReviewBulk('approve') }, { key: 'regenerate', label: 'Regenerate', onClick: () => void runNeedsReviewBulk('regenerate') }, { key: 'reject', label: 'Reject', onClick: () => void runNeedsReviewBulk('reject'), variant: 'danger' }, { key: 'send-to-failed-mapping', label: 'Send to Failed Mapping', onClick: () => void runNeedsReviewBulk('send-to-failed-mapping') }]} />
          {queue.filter((item) => !item.is_source_parent).length === 0 ? <p className="subtle">No queued emails match these filters.</p> : null}
          {queue.filter((item) => !item.is_source_parent).map((item, index, visibleQueue) => {
            const effectiveDraft = draftEdits[item.id] ?? item.draft_reply
            const showSourceHeader = Boolean(
              item.source_parent_email_id &&
              visibleQueue[index - 1]?.source_parent_email_id !== item.source_parent_email_id,
            )
            return (
              <div key={item.id} className="multiRoleCandidateGroup">
              {showSourceHeader ? (
                <div className="card">
                  <h3>Email {item.source_parent_email_id} — {item.requirement_count ?? 0} roles detected</h3>
                  <p className="subtle">Each role is processed, scored, drafted, and approved independently.</p>
                  <button type="button" onClick={() => retryRoleDetection(item.source_parent_email_id!)}>
                    Retry Detection
                  </button>
                </div>
              ) : null}
              <CandidateCard
                item={item}
                searchSection="needs_review"
                isSearchHighlighted={isEmailSearchHighlight('needs_review', item.id)}
                draftValue={effectiveDraft}
                onDraftChange={(value) => setDraftEdits((prev) => ({ ...prev, [item.id]: value }))}
                draftTextSize={settings.draft_text_size}
                enabledAttachmentNames={enabledAttachmentNames}
                activeResumeName={activeResume?.file_name}
                parserExpanded={Boolean(expandedParserDetailIds[item.id])}
                onToggleParserExpanded={() => setExpandedParserDetailIds((prev) => ({ ...prev, [item.id]: !prev[item.id] }))}
                selection={{
                  checked: needsReviewSelected.has(item.id),
                  onToggle: () => setNeedsReviewSelected((previous) => { const next = new Set(previous); if (next.has(item.id)) next.delete(item.id); else next.add(item.id); return next }),
                }}
                isSending={sendingId === item.id}
                onApprove={approveSend}
                isRegenerating={regeneratingId === item.id}
                onRegenerate={regenerateCandidate}
                onRetryDetection={retryRoleDetection}
                isRejecting={rejectingId === item.id}
                onReject={rejectSend}
                isMovingToFailedMapping={movingToFailedId === item.id}
                onSendToFailedMapping={moveToFailedMapping}
                onToggleTracking={(id) => void toggleTracking(id).catch((reason) => setError((reason as Error).message))}
                sentDetailsExpanded={Boolean(expandedSentDetailIds[item.id])}
                onToggleSentDetails={toggleSentDetails}
                sentDetailsLoading={Boolean(sentDetailLoadingIds[item.id])}
                sentDetailsError={sentDetailErrors[item.id]}
                sentDetails={sentDetailsById[item.id]}
              />
              </div>
            )
          })}
          {bucketMeta.needs_review.hasNext ? (
            <button
              type="button"
              onClick={() => loadMoreCandidates('needs_review', settings.mail_date ?? null, activeQueryOptions())}
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
          <label className="selectAllRow"><input type="checkbox" checked={failedQueue.length > 0 && failedQueue.every((item) => failedMappingSelected.has(item.id))} onChange={(event) => setFailedMappingSelected(event.target.checked ? new Set(failedQueue.map((item) => item.id)) : new Set())} /> Select all visible</label>
          <SelectionActionBar selectedCount={failedMappingSelected.size} busyKey={failedMappingBulkAction} onClearSelection={() => setFailedMappingSelected(new Set())} actions={[{ key: 'save', label: 'Save Mapping & Move to Review', onClick: () => void runFailedMappingBulk('save'), disabled: ![...failedMappingSelected].some((id) => isValidEmailAddress(routingFixes[id]?.to ?? '') && isValidEmailAddress(routingFixes[id]?.cc ?? '')) }, { key: 'delete', label: 'Delete', onClick: () => void runFailedMappingBulk('delete'), variant: 'danger' }]} />
          {failedQueue.length === 0 ? <p className="subtle">No failed emails match these filters.</p> : null}
          {failedQueue.map((item) => {
            const fix = routingFixes[item.id] ?? { to: '', cc: '' }
            const openUrl = sourceListingUrl(item) ?? item.gmail_message_url
            const atsStrength = getAtsStrengthLabel(item.ats_score)
            const atsTone = atsStrength === 'Strong' ? 'active' : atsStrength === 'Moderate' ? 'pending' : 'flagged'
            const hasCandidateBadges = Boolean(
              item.ats_score != null || item.premium_status || item.premium_verification_level || item.following_badge,
            )
            return (
              <article
                key={`failed-${item.id}`}
                className={`emailItem ${isEmailSearchHighlight('failed_mapping', item.id) ? 'emailSearchHighlight' : ''}`}
                data-email-search-section="failed_mapping"
                data-email-search-related-id={item.id}
              >
                <div className="candidateCardTop">
                  <input type="checkbox" className="emailItemCheckbox candidateCardCheckbox" aria-label={`Select candidate ${item.id}`} checked={failedMappingSelected.has(item.id)} onChange={() => setFailedMappingSelected((previous) => { const next = new Set(previous); if (next.has(item.id)) next.delete(item.id); else next.add(item.id); return next })} />

                  <div className="candidateCardHeaderMain">
                    <h3 className="candidateCardTitle">{item.role || item.subject || 'Unknown Role'}</h3>
                    <p className="candidateCardSubtitle">
                      {item.location || '-'}
                      {' · '}{item.salary_text || 'Salary not specified'}
                      {' · '}{jdSummarySkills(item).join(', ') || '-'}
                    </p>
                    <p className="candidateCardMeta"><strong>From:</strong> {item.sender}</p>
                    <p className="candidateCardMeta"><strong>Subject:</strong> {item.subject}</p>
                    {openUrl ? (
                      <p className="candidateCardMeta candidateCardLinks">
                        <a href={openUrl} target="_blank" rel="noreferrer">
                          {item.source === 'nvoids' ? 'Open Original Post' : 'Open exact email in Gmail'}
                        </a>
                      </p>
                    ) : null}
                    <p className="candidateCardMeta"><strong>Reason:</strong> {item.last_error ?? item.state}</p>
                    <p className="candidateCardRecordId">Record ID: {item.record_id ?? '-'}</p>
                  </div>

                  {hasCandidateBadges ? (
                    <div className="candidateCardBadges" aria-label="Candidate status badges">
                      {item.ats_score != null ? (
                        <span className={`statusBadge statusBadge--lg statusBadge--${atsTone}`}>
                          ATS {atsStrength} · {formatAtsScore(item.ats_score)}
                        </span>
                      ) : null}
                      {item.premium_status ? <span className={`statusBadge statusBadge--${item.premium_status === 'Active' ? 'active' : 'flagged'}`}>{item.premium_status}</span> : null}
                      {item.premium_verification_level ? <VerificationBadge level={item.premium_verification_level} /> : null}
                      {item.following_badge ? <span className="statusBadge statusBadge--pending" title={item.following_warning ?? undefined}>{item.following_badge === 'active' ? 'Active Following' : item.following_badge === 'tracked' ? 'Tracked' : 'Bookmarked Requirement'}</span> : null}
                    </div>
                  ) : null}
                </div>

                <section className="detailSection">
                  <h4>Routing &amp; Screening</h4>
                  {renderRoutingPanel(item)}
                  {renderCandidateEmails(item)}
                </section>

                <section className="detailSection">
                  <h4>Correct Recipients</h4>
                  <label>
                    Full Email Content (for recipient mapping)
                    <textarea value={item.body ?? ''} readOnly rows={8} />
                  </label>
                  <label>
                    Correct To
                    <input
                      type="email"
                      value={fix.to}
                      aria-invalid={Boolean(fix.to) && !isValidEmailAddress(fix.to)}
                      onChange={(e) =>
                        setRoutingFixes((prev) => ({ ...prev, [item.id]: { ...fix, to: e.target.value } }))
                      }
                    />
                  </label>
                  <label>
                    Correct CC
                    <input
                      type="email"
                      value={fix.cc}
                      aria-invalid={Boolean(fix.cc) && !isValidEmailAddress(fix.cc)}
                      onChange={(e) =>
                        setRoutingFixes((prev) => ({ ...prev, [item.id]: { ...fix, cc: e.target.value } }))
                      }
                    />
                  </label>
                </section>

                <div className="rowBtns">
                  <button
                    type="button"
                    className="sendActionButton"
                    onClick={() => saveRoutingAndRequeue(item.id)}
                    disabled={fixingId === item.id || deletingFailedId === item.id || !isValidEmailAddress(fix.to) || !isValidEmailAddress(fix.cc)}
                  >
                    {fixingId === item.id ? 'Saving...' : 'Save Mapping & Move to Review'}
                  </button>
                  <button
                    type="button"
                    className="dangerButton"
                    onClick={() => deleteFailedMapping(item.id)}
                    disabled={deletingFailedId === item.id || fixingId === item.id}
                    title="Delete this failed mapping card from the dashboard"
                  >
                    {deletingFailedId === item.id ? 'Deleting...' : 'Delete'}
                  </button>
                </div>
              </article>
            )
          })}
          {bucketMeta.failed.hasNext ? (
            <button
              type="button"
              onClick={() => loadMoreCandidates('failed', settings.mail_date ?? null, activeQueryOptions())}
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
            <article
              key={item.run_key ?? `${item.email_id ?? 'none'}-${index}`}
              className={`emailItem ${isEmailSearchHighlight('recent_runs', item.run_key) ? 'emailSearchHighlight' : ''}`}
              data-email-search-section="recent_runs"
              data-email-search-related-id={item.run_key ?? undefined}
            >
              <p><strong>Status:</strong> {item.status}</p>
              <p><strong>Detail:</strong> {item.detail}</p>
              {item.run_source ? <p><strong>Run Source:</strong> {item.run_source}</p> : null}
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
              {item.effective_query || item.matched_count != null || item.queued_count != null || item.skipped_count != null || item.failed_count != null || item.source_count != null || item.requirement_count != null ? (
                <p className="subtle">
                  <strong>Summary:</strong>{' '}
                  {[
                    item.effective_query ? `Query: ${item.effective_query}` : null,
                    item.matched_count != null ? `Matched: ${item.matched_count}` : null,
                    item.queued_count != null ? `Queued: ${item.queued_count}` : null,
                    item.skipped_count != null ? `Skipped: ${item.skipped_count}` : null,
                    item.failed_count != null ? `Failed: ${item.failed_count}` : null,
                    item.source_count != null ? `Sources: ${item.source_count}` : null,
                    item.requirement_count != null ? `Requirements: ${item.requirement_count}` : null,
                    item.multi_role_source_count != null ? `Multi-role: ${item.multi_role_source_count}` : null,
                    item.manifest_review_count != null ? `Manifest review: ${item.manifest_review_count}` : null,
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
              {((item.skipped_item_count ?? 0) > 0 || (item.skipped_items?.length ?? 0) > 0) ? (
                <div className="stack">
                  <button type="button" onClick={() => void toggleRecentRunItems(item.run_key)}>
                    {item.skipped_items_loaded ? `Hide Skipped Items (${item.skipped_item_count ?? item.skipped_items?.length ?? 0})` : `Skipped Items (${item.skipped_item_count ?? 0})`}
                  </button>
                  {item.skipped_items_loading ? <p className="subtle">Loading skipped items...</p> : null}
                  {item.skipped_items_error ? <p className="errorMessage">{item.skipped_items_error}</p> : null}
                  {item.retry_error ? <p className="errorMessage">{item.retry_error}</p> : null}
                  {item.skipped_items_loaded ? (
                    item.skipped_items && item.skipped_items.length > 0 ? (
                      <div className="stack">
                        {(() => {
                          const retryableIds = item.skipped_items
                            .filter((skipped) => Boolean(skipped.external_message_id))
                            .map((skipped) => skipped.id)
                          const selected = item.selected_skipped_ids ?? []
                          if (retryableIds.length === 0) return null
                          return (
                            <div className="automationMetrics">
                              <label>
                                <input
                                  type="checkbox"
                                  checked={selected.length > 0 && retryableIds.every((id) => selected.includes(id))}
                                  onChange={(e) => selectAllSkippedItems(item.run_key, e.target.checked)}
                                />{' '}
                                Select All
                              </label>
                              <button
                                type="button"
                                disabled={selected.length === 0 || item.retrying_skipped}
                                onClick={() => void retrySelectedSkippedItems(item.run_key)}
                              >
                                {item.retrying_skipped ? 'Retrying...' : `Retry Selected (${selected.length})`}
                              </button>
                            </div>
                          )
                        })()}
                        {item.skipped_items.map((skipped) => {
                          const intentEvidence = skipped.intent_evidence ?? []
                          const intentNegativeEvidence = skipped.intent_negative_evidence ?? []
                          const isSelected = (item.selected_skipped_ids ?? []).includes(skipped.id)
                          return (
                          <article
                            key={`${item.run_key}-${skipped.id}`}
                            className={`emailItem ${isEmailSearchHighlight('recent_runs', skipped.id) ? 'emailSearchHighlight' : ''}`}
                            data-email-search-section="recent_runs"
                            data-email-search-related-id={skipped.id}
                          >
                            <div className="skippedItemRow">
                              <div className="skippedItemCheckbox">
                                {skipped.external_message_id ? (
                                  <input
                                    type="checkbox"
                                    checked={isSelected}
                                    aria-label="Select for retry"
                                    title="Select for retry"
                                    onChange={() => toggleSkippedItemSelected(item.run_key, skipped.id)}
                                  />
                                ) : null}
                              </div>
                              <div className="skippedItemContent">
                                <p><strong>Source:</strong> {getSourceLabel(skipped.source_type)}</p>
                                <p><strong>Title:</strong> {renderTextOrDash(skipped.title_or_subject)}</p>
                                <p><strong>Why:</strong> {renderTextOrDash(skipped.reason_detail || skipped.reason_code)}</p>
                                {skipped.source_group_name || skipped.source_group_email ? (
                                  <p>
                                    <strong>Source Group:</strong>{' '}
                                    {[skipped.source_group_name, skipped.source_group_email].filter(Boolean).join(' | ')}
                                  </p>
                                ) : null}
                                {skipped.source_group_match_method ? (
                                  <p><strong>Matched Through:</strong> {skipped.source_group_match_method}</p>
                                ) : null}
                                {skipped.intent_type || skipped.gate_action || skipped.gate_provider ? (
                                  <p>
                                    <strong>Gate:</strong>{' '}
                                    {[skipped.intent_type, skipped.gate_action, skipped.gate_provider].filter(Boolean).join(' | ')}
                                  </p>
                                ) : null}
                                {skipped.intent_confidence != null ? (
                                  <p><strong>Confidence:</strong> {skipped.intent_confidence.toFixed(2)}</p>
                                ) : null}
                                {skipped.intent_reason && skipped.intent_reason !== skipped.reason_detail ? (
                                  <p><strong>Intent Reason:</strong> {skipped.intent_reason}</p>
                                ) : null}
                                {skipped.qualification_result ? (
                                  <p><strong>Qualification Result:</strong> {skipped.qualification_result}</p>
                                ) : null}
                                {skipped.blocking_rule ? (
                                  <p><strong>Blocking Rule:</strong> {skipped.blocking_rule}</p>
                                ) : null}
                                {skipped.qualification_detail && skipped.qualification_detail !== skipped.reason_detail ? (
                                  <p><strong>Qualification Detail:</strong> {skipped.qualification_detail}</p>
                                ) : null}
                                {intentEvidence.length > 0 ? (
                                  <div className="automationMetrics">
                                    <strong>Evidence:</strong>
                                    {intentEvidence.map((entry) => (
                                      <span key={`${skipped.id}-${entry}`} className="tag">{entry}</span>
                                    ))}
                                  </div>
                                ) : null}
                                {intentNegativeEvidence.length > 0 ? (
                                  <div className="automationMetrics">
                                    <strong>Negative Evidence:</strong>
                                    {intentNegativeEvidence.map((entry) => (
                                      <span key={`${skipped.id}-neg-${entry}`} className="tag">{entry}</span>
                                    ))}
                                  </div>
                                ) : null}
                                {skipped.source_url || skipped.gmail_message_url ? (
                                  <p>
                                    <strong>Open:</strong>{' '}
                                    <a href={skipped.source_url ?? skipped.gmail_message_url ?? undefined} target="_blank" rel="noreferrer">
                                      {skipped.source_type === 'nvoids' ? 'Open Original Post' : 'Open exact email in Gmail'}
                                    </a>
                                  </p>
                                ) : null}
                              </div>
                            </div>
                          </article>
                          )
                        })}
                      </div>
                    ) : (
                      <p className="subtle">No skipped items found for this run.</p>
                    )
                  ) : null}
                </div>
              ) : null}
            </article>
          ))}
            </section>
          ) : null}

          {activePage === 'premium_numbers' ? (
            <PremiumNumbersPage
              apiBase={apiBase}
              mailDate={settings.mail_date ?? null}
              emailSearchTarget={emailSearchTarget}
              refreshToken={premiumRefreshToken}
              applicationsEnabled={settings.feature_applications_enabled}
              onPendingCountChange={setPremiumPendingCount}
              activeTab={premiumTab}
              onTabChange={setPremiumTab}
              filterValues={activeFilterValues}
              sortValue={activeSortValue}
            />
          ) : null}

          {activePage === 'application_tracking' ? <AppTSPage apiBase={apiBase} refreshToken={premiumRefreshToken} activeTab={applicationTrackingTab} onTabChange={setApplicationTrackingTab} filterValues={activeFilterValues} sortValue={activeSortValue} /> : null}

          {activePage === 'resume_tracking' ? <ResumeTrackingPage apiBase={apiBase} onNavigateToSettings={(resumeId) => { setFocusResumeId(resumeId); setActivePage('settings') }} activeTab={resumeTrackingTab} onTabChange={setResumeTrackingTab} filterValues={activeFilterValues} sortValue={activeSortValue} /> : null}

          {activePage === 'inbox' ? (
            <section className="card pageSection inboxSection">
              <div className="inboxHeader">
                <div>
                  <h2>Reply Inbox</h2>
                  <p className="subtle">Replies are authoritative. Open counts are only a best-effort image signal.</p>
                </div>
                <div className="inboxHeaderActions">
                  <button
                    type="button"
                    className={`iconBtn inboxRefreshBtn ${inboxLoading ? 'loading' : ''}`}
                    onClick={() => void refreshInboxReplies()}
                    disabled={inboxLoading}
                    aria-label="Refresh conversations"
                    aria-busy={inboxLoading}
                    title="Refresh"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M20 6v5h-5M4 18v-5h5M5.8 9a7 7 0 0 1 11.7-2.6L20 9M4 15l2.5 2.6A7 7 0 0 0 18.2 15" />
                    </svg>
                  </button>
                </div>
              </div>
              {!settings.feature_reply_inbox_enabled ? (
                <p className="inboxNotice">Reply capture is off. Enable Reply Inbox in Settings to scan sent Gmail threads.</p>
              ) : null}
              {inboxError ? <p className="errorMessage">{inboxError}</p> : null}
              <div className="inboxLayout">
                <div className="conversationList" aria-label="Email conversations">
                  {inboxConversations.length === 0 && !inboxLoading ? (
                    <p className="subtle">No tracked conversations yet.</p>
                  ) : null}
                  {inboxConversations.map((conversation) => {
                    const isUnread = conversation.unread_reply_count > 0
                    const displayTime = conversation.last_inbound_reply_at ?? conversation.last_message_at
                    const absoluteTime = new Date(displayTime).toLocaleString()
                    return (
                      <div key={conversation.id} className="conversationListItemWrap">
                        <button
                          type="button"
                          className={`conversationListItem ${isUnread ? 'unread' : ''} ${selectedConversationId === conversation.id ? 'active' : ''} ${isEmailSearchHighlight('inbox', conversation.id) ? 'emailSearchHighlight' : ''}`}
                          data-email-search-section="inbox"
                          data-email-search-related-id={conversation.id}
                          onClick={() => void openInboxConversation(conversation.id)}
                          aria-label={`${isUnread ? 'Unread: ' : ''}${conversation.recruiter}, ${conversation.subject}, ${absoluteTime}`}
                          title={absoluteTime}
                        >
                          <span className="conversationListTopline">
                            <span className="conversationListIdentity">
                              <span className="conversationListSender">{conversation.recruiter}</span>
                              <span className="conversationListSubject">{conversation.subject}</span>
                            </span>
                            <span className="conversationListMeta">
                              {isUnread ? <span className="unreadDot" aria-hidden="true" /> : null}
                              <time dateTime={displayTime} title={absoluteTime}>
                                {formatRelativeInboxTime(displayTime)}
                              </time>
                            </span>
                          </span>
                          <small className="conversationPreview">{conversation.last_message_preview || 'No message preview'}</small>
                        </button>
                        {conversation.gmail_thread_link ? (
                          <a
                            className="conversationGmailLink"
                            href={conversation.gmail_thread_link}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            title="Open original thread in Gmail"
                            aria-label="Open original thread in Gmail"
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <path d="M14 4h6v6M20 4 11 13M9 5H5a1 1 0 0 0-1 1v13a1 1 0 0 0 1 1h13a1 1 0 0 0 1-1v-4" />
                            </svg>
                          </a>
                        ) : null}
                      </div>
                    )
                  })}
                </div>
                <div className="conversationDetail">
                  {selectedConversation ? (
                    <>
                      <div className="conversationDetailHeader">
                        <div>
                          <h3>{selectedConversation.subject}</h3>
                          <p className="subtle">To: {selectedConversation.to_email ?? '-'} · CC: {selectedConversation.cc_email ?? '-'}</p>
                        </div>
                        <span
                          className="sourceBadge conversationStatusBadge"
                          title={`Conversation status: ${selectedConversation.status}`}
                        >
                          {selectedConversation.status}
                        </span>
                      </div>
                      <div className="conversationThread">
                        {selectedConversation.messages.map((message) => (
                          <article key={message.id} className={`conversationMessage ${message.direction}`}>
                            <span className="conversationAvatar" aria-hidden="true">
                              {getInitials(message.direction === 'outbound' ? 'You' : message.sender)}
                            </span>
                            <div className="conversationMessageContent">
                              <div className="conversationMessageMeta">
                                <strong>{message.direction === 'outbound' ? 'You' : message.sender}</strong>
                                <span>{new Date(message.occurred_at).toLocaleString()}</span>
                              </div>
                              <p>{message.body}</p>
                            </div>
                          </article>
                        ))}
                      </div>
                      <label className="inboxComposer">
                        <span>Send Reply</span>
                        <textarea
                          rows={5}
                          value={inboxReplyDraft}
                          onChange={(e) => setInboxReplyDraft(e.target.value)}
                          placeholder="Write your reply..."
                        />
                      </label>
                      <div className="composerToolbar">
                        <div className="composerToolGroup" role="group" aria-label="Formatting tools">
                          <button type="button" className="composerToolBtn" disabled aria-disabled="true" aria-label="Bold" title="Coming soon">
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <path d="M7 4h6a4 4 0 0 1 0 8H7V4Zm0 8h7a4 4 0 0 1 0 8H7v-8Z" />
                            </svg>
                          </button>
                          <button type="button" className="composerToolBtn" disabled aria-disabled="true" aria-label="Italic" title="Coming soon">
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <path d="M10 4h8M6 20h8M14 4l-4 16" />
                            </svg>
                          </button>
                          <span className="composerToolDivider" aria-hidden="true" />
                          <button type="button" className="composerToolBtn" disabled aria-disabled="true" aria-label="Attach file" title="Coming soon">
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <path d="m20.5 11.5-8.7 8.7a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7L9 17.4a2 2 0 0 1-2.8-2.8l8.5-8.5" />
                            </svg>
                          </button>
                          <button type="button" className="composerToolBtn" disabled aria-disabled="true" aria-label="Insert emoji" title="Coming soon">
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM8.5 10h.01M15.5 10h.01M8 14a5 5 0 0 0 8 0" />
                            </svg>
                          </button>
                        </div>
                        <button
                          type="button"
                          className="btnPrimary composerSendBtn"
                          onClick={() => void sendInboxReply()}
                          disabled={inboxSending || !inboxReplyDraft.trim()}
                        >
                          <svg viewBox="0 0 24 24" aria-hidden="true">
                            <path d="m3 3 18 9-18 9 4-9-4-9Zm4 9h14" />
                          </svg>
                          {inboxSending ? 'Sending...' : 'Send Reply'}
                        </button>
                      </div>
                    </>
                  ) : (
                    <p className="subtle">Select a conversation to view the thread.</p>
                  )}
                </div>
              </div>
            </section>
          ) : null}

          {activePage === 'sent_items' ? (
            <section className="card pageSection">
          <h2>Sent Items</h2>
          {sentQueue.length === 0 ? <p className="subtle">No approved and sent emails yet.</p> : null}
          {sentQueue.map((item) => {
            const isExpanded = Boolean(expandedSentDetailIds[item.id])
            const sentDetails = sentDetailsById[item.id]
            const sentDetailError = sentDetailErrors[item.id]
            const sentDetailLoading = Boolean(sentDetailLoadingIds[item.id])
            const parserExpanded = Boolean(expandedParserDetailIds[item.id])
            const atsStrength = getAtsStrengthLabel(item.ats_score)
            const atsTone = atsStrength === 'Strong' ? 'active' : atsStrength === 'Moderate' ? 'pending' : 'flagged'
            const hasCandidateBadges = Boolean(
              item.ats_score != null || item.premium_status || item.premium_verification_level || item.following_badge,
            )
            return (
              <article
                key={`sent-${item.id}`}
                className={`emailItem sentItemCard ${isEmailSearchHighlight('sent_items', item.id) ? 'emailSearchHighlight' : ''}`}
                data-email-search-section="sent_items"
                data-email-search-related-id={item.id}
              >
                <div className="candidateCardTop">
                  <div className="candidateCardHeaderMain">
                    <h3 className="candidateCardTitle">{item.role || item.subject || 'Unknown Role'}</h3>
                    <p className="candidateCardSubtitle">
                      {item.location || '-'}
                      {' · '}{item.salary_text || 'Salary not specified'}
                      {' · '}{jdSummarySkills(item).join(', ') || '-'}
                    </p>
                    <p className="candidateCardMeta"><strong>From:</strong> {item.sender}</p>
                    <p className="candidateCardMeta"><strong>Subject:</strong> {item.subject}</p>
                    <p className="candidateCardMeta"><strong>Sent at:</strong> {item.sent_at ? new Date(item.sent_at).toLocaleString() : '-'}</p>
                    <p className="candidateCardRecordId">Record ID: {item.record_id ?? '-'}</p>
                  </div>

                  {hasCandidateBadges ? (
                    <div className="candidateCardBadges" aria-label="Candidate status badges">
                      {item.ats_score != null ? (
                        <span className={`statusBadge statusBadge--lg statusBadge--${atsTone}`}>
                          ATS {atsStrength} · {formatAtsScore(item.ats_score)}
                        </span>
                      ) : null}
                      {item.premium_status ? <span className={`statusBadge statusBadge--${item.premium_status === 'Active' ? 'active' : 'flagged'}`}>{item.premium_status}</span> : null}
                      {item.premium_verification_level ? <VerificationBadge level={item.premium_verification_level} /> : null}
                      {item.following_badge ? <span className="statusBadge statusBadge--pending" title={item.following_warning ?? undefined}>{item.following_badge === 'active' ? 'Active Following' : item.following_badge === 'tracked' ? 'Tracked' : 'Bookmarked Requirement'}</span> : null}
                    </div>
                  ) : null}
                </div>

                <div className="sentItemHeaderActions">
                  <span className="sourceBadge">{getSourceLabel(item.source)}</span>
                  {settings.feature_applications_enabled ? <button type="button" onClick={() => void toggleTracking(item.id).catch((reason) => setError((reason as Error).message))}>Track Application</button> : null}
                  <button type="button" onClick={() => void toggleSentDetails(item.id)}>
                    {isExpanded ? 'Hide Sourcing Audit Trail' : 'Sourcing Audit Trail'}
                  </button>
                </div>
                {isExpanded ? (
                  <div className="parserDetailsPanel sentItemDetailsPanel">
                    {sentDetailLoading ? <p className="subtle">Loading sent item details...</p> : null}
                    {sentDetailError ? <p className="errorMessage">{sentDetailError}</p> : null}
                    {sentDetails ? (
                      <>
                        <div className="trackingSummary">
                          <span
                            className={`trackingBadge ${sentDetails.open_count > 0 ? 'opened' : ''}`}
                            title="Open tracking is best-effort: Gmail may proxy or cache images, scanners may trigger false opens, and blocked images cause missed opens. Replies are authoritative."
                          >
                            {sentDetails.open_count > 0
                              ? `Opened (heuristic) ${sentDetails.open_count} time${sentDetails.open_count === 1 ? '' : 's'}${sentDetails.opened_at ? ` · First seen ${new Date(sentDetails.opened_at).toLocaleString()}` : ''}`
                              : 'Not opened (heuristic)'}
                          </span>
                          <span className="trackingBadge">Replies: {sentDetails.reply_count}</span>
                        </div>
                        {renderContactDetailsGrid(sentDetails, item)}
                        <ParserDetailsPanel
                          candidateId={item.id}
                          source={item.source}
                          parserDetails={item.parser_details}
                          atsScore={item.ats_score}
                          atsSource={item.ats_score_source}
                          atsSummary={item.ats_summary}
                          atsBreakdown={item.ats_breakdown}
                          resumePickerBreakdown={item.resume_picker_breakdown}
                          expanded={parserExpanded}
                          onToggle={(candidateId) =>
                            setExpandedParserDetailIds((prev) => ({
                              ...prev,
                              [candidateId]: !prev[candidateId],
                            }))
                          }
                        />
                      </>
                    ) : null}
                  </div>
                ) : null}
              </article>
            )
          })}
          {bucketMeta.approved_sent.hasNext ? (
            <button
              type="button"
              onClick={() => loadMoreCandidates('approved_sent', settings.mail_date ?? null, activeQueryOptions())}
              disabled={loadingMoreKey === 'approved_sent'}
            >
              {loadingMoreKey === 'approved_sent' ? 'Loading...' : 'Load More'}
            </button>
          ) : null}
            </section>
          ) : null}
        </div>
      </section>
      {/* Hidden on the workspace itself: the launcher is fixed bottom-right and
          lands on top of the page's own Send button, and a floating copy of the
          surface you are already looking at is noise either way. */}
      {activePage !== 'assistant' ? <ChatWidget /> : null}
    </main>
    </ChatProvider>
  )
}

export default App
