import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import Sidebar from './components/Sidebar'
import { withAiToggle } from './features/ai/state'
import { getDraftSourceLabel } from './features/ai/ui'
import { withSavedQueries } from './features/query_bucket/api'
import QueryBucket from './features/query_bucket/QueryBucket'
import { type CandidateState, useCandidateBuckets } from './candidateBuckets'
import { addEmployerDomain, removeEmployerDomain } from './employerDomains'
import { buildPremiumScopeUrl, defaultPremiumPageMeta, type PremiumScope } from './premiumNumbers'

const GMAIL_OAUTH_POLL_INTERVAL_MS = 2000
const GMAIL_OAUTH_POLL_TIMEOUT_MS = 180000
const VIEW_EVENT_THROTTLE_MS = 60000
const PREMIUM_PAGE_LIMIT = 25
let hasBootstrappedAppOnce = false
export const DRAFT_TEXT_SIZE_OPTIONS = ['small', 'normal', 'large', 'huge'] as const
export type DraftTextSize = (typeof DRAFT_TEXT_SIZE_OPTIONS)[number]
const DRAFT_TEXT_SIZE_STYLES: Record<DraftTextSize, { fontSize: string; lineHeight: string }> = {
  small: { fontSize: '12px', lineHeight: '1.5' },
  normal: { fontSize: '16px', lineHeight: '1.5' },
  large: { fontSize: '20px', lineHeight: '1.5' },
  huge: { fontSize: '28px', lineHeight: '1.4' },
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
  nvoids_locations: string[]
  feature_auto_send: boolean
  feature_retry_queue: boolean
  feature_ai_enabled: boolean
  feature_ai_extractor_enabled: boolean
  feature_semantic_enabled: boolean
  feature_groq_job_parser_enabled: boolean
  draft_text_size: DraftTextSize
  fallback_draft_template: string
  signature_name: string
  signature_phone: string
  signature_email: string
  preferred_employer_cc_email: string
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

type ResumeDatabaseSectionProps = {
  activeResume: ResumeAsset | null
  resumeFile: File | null
  resumeSkillsInput: string
  resumeSkillEdits: Record<number, string>
  resumeAssets: ResumeAsset[]
  setResumeFile: (file: File | null) => void
  setResumeSkillsInput: (value: string) => void
  setResumeSkillEdits: React.Dispatch<React.SetStateAction<Record<number, string>>>
  uploadResume: () => void
  saveResumeSkills: (resumeId: number) => void
  toggleResumeAsset: (resumeId: number, isEnabled: boolean) => void
  deleteResumeAsset: (resumeId: number) => void
}

export function ResumeDatabaseSection({
  activeResume,
  resumeFile,
  resumeSkillsInput,
  resumeSkillEdits,
  resumeAssets,
  setResumeFile,
  setResumeSkillsInput,
  setResumeSkillEdits,
  uploadResume,
  saveResumeSkills,
  toggleResumeAsset,
  deleteResumeAsset,
}: ResumeDatabaseSectionProps) {
  const [expandedResumeIds, setExpandedResumeIds] = useState<Record<number, boolean>>({})

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
          <input type="file" accept=".pdf,.doc,.docx" onChange={(e) => setResumeFile(e.target.files?.[0] ?? null)} />
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
          <p className="subtle resumeDatabaseHelp">Use clean comma-separated skills for faster and more accurate resume matching.</p>
          <button type="button" onClick={uploadResume} disabled={!resumeFile}>
            Upload Resume To Database
          </button>
        </div>
        {resumeAssets.length === 0 ? (
          <p className="subtle">No resumes stored yet.</p>
        ) : (
          <div className="resumeDatabaseList">
            {resumeAssets.map((resume) => {
              const isExpanded = !!expandedResumeIds[resume.id]
              return (
                <article key={resume.id} className="resumeDatabaseItem pillRow">
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
                            Save Skills
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
}

export function SkillUpgradeSection({
  pendingSkills,
  loading,
  busySkillKey,
  approveAllSkills,
  approveSkill,
  dismissSkill,
}: SkillUpgradeSectionProps) {
  return (
    <section className="card skillUpgradeCard">
      <h2>Upgrade Skills</h2>
      <div className="stack skillUpgradeStack">
        <p className="subtle skillUpgradeIntro">
          Review parser-extracted unknown skills here. Approve adds them to your
          custom taxonomy; dismiss removes them from this queue.
        </p>

        <section className="skillUpgradeColumn">
          <div className="skillUpgradeColumnHeader">
            <h3>Pending Unknown Skills</h3>
            <div className="rowBtns">
              {!loading && pendingSkills.length > 0 ? (
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
              {pendingSkills.map((skill) => {
                const approveKey = `approve:${skill.normalized_name}`
                const dismissKey = `dismiss:${skill.normalized_name}`
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
                    <div className="skillUpgradeActions">
                      <button
                        type="button"
                        className="primary"
                        onClick={() => approveSkill(skill)}
                        disabled={busySkillKey !== null}
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
            </div>
          )}
        </section>
      </div>
    </section>
  )
}

type JobIntentLearningSectionProps = {
  pendingSignals: JobIntentLearningSignal[]
  approvedSignals: JobIntentLearningSignal[]
  loading: boolean
  busySignalKey: string | null
  approveSignal: (signal: JobIntentLearningSignal) => void
  dismissSignal: (signal: JobIntentLearningSignal) => void
}

export function JobIntentLearningSection({
  pendingSignals,
  approvedSignals,
  loading,
  busySignalKey,
  approveSignal,
  dismissSignal,
}: JobIntentLearningSectionProps) {
  return (
    <section className="card skillUpgradeCard">
      <h2>Job Intent Learning</h2>
      <div className="stack skillUpgradeStack">
        <p className="subtle skillUpgradeIntro">
          Groq-suggested Gmail intent phrases land here for review. Approve lets fallback mode use them later; dismiss keeps them suppressed.
        </p>

        <section className="skillUpgradeColumn">
          <div className="skillUpgradeColumnHeader">
            <h3>Pending Intent Signals</h3>
            <span className="skillUpgradeCount">{pendingSignals.length}</span>
          </div>
          {loading ? (
            <p className="subtle">Loading intent signals...</p>
          ) : pendingSignals.length === 0 ? (
            <p className="subtle">No pending job-intent learning right now.</p>
          ) : (
            <div className="skillUpgradeList">
              {pendingSignals.map((signal) => {
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
            </div>
          )}
        </section>

        <section className="skillUpgradeColumn">
          <div className="skillUpgradeColumnHeader">
            <h3>Approved Intent Signals</h3>
            <span className="skillUpgradeCount">{approvedSignals.length}</span>
          </div>
          {loading ? (
            <p className="subtle">Loading approved intent signals...</p>
          ) : approvedSignals.length === 0 ? (
            <p className="subtle">No approved intent signals yet.</p>
          ) : (
            <div className="skillUpgradeList">
              {approvedSignals.map((signal) => (
                <article key={`approved-${signal.id}`} className="skillUpgradeItem">
                  <div className="skillUpgradeItemHeader">
                    <strong className="skillUpgradeName">{signal.phrase}</strong>
                    <span className="skillUpgradeBadge">{signal.polarity}</span>
                  </div>
                  <p className="subtle skillUpgradeMeta">Examples: {signal.source_examples_count}</p>
                  <p className="subtle skillUpgradeMeta">Confidence: {signal.confidence_aggregate.toFixed(2)}</p>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </section>
  )
}

type Candidate = {
  id: number
  subject: string
  sender: string
  body: string
  role: string
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
}

type SentItemDetails = {
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
  recruiter_phone: string | null
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

type PremiumNumberConfidence = 'high' | 'medium' | 'low'

type PaginatedListResponse<TItem> = {
  items: TItem[]
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

function formatAtsScore(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-'
  return String(Math.round(value))
}

function getAtsStrengthLabel(value: number | null | undefined): string {
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
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function normalizeParserDetails(value: unknown): ParserDetailsPayload | null {
  if (!isRecord(value)) return null
  return value as ParserDetailsPayload
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

type ParserDetailsPanelProps = {
  candidateId: number
  source: string
  parserDetails: Record<string, unknown> | null
  atsScore?: number | null
  atsSource?: string | null
  atsSummary?: string | null
  atsBreakdown?: Record<string, unknown> | null
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
  expanded,
  onToggle,
}: ParserDetailsPanelProps) {
  const normalized = normalizeParserDetails(parserDetails)
  if (!normalized) return null
  const finalResult = normalized.merged_result ?? {}
  const baseResult = normalized.base_parser_result ?? {}
  const skillsAudit = isRecord(normalized.skills_audit) ? normalized.skills_audit : null
  const approvedSkills = (() => {
    const audited = skillsAudit ? recordStringArray(skillsAudit, 'known').join(', ') : ''
    if (audited) return audited
    return (normalized.approved_skills_text || recordStringValue(finalResult, 'skills_text')).trim()
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
  const sourceHints = isRecord(normalized.source_hints) ? normalized.source_hints : null
  const parserMode = normalized.parser_mode || (aiExtractor ? 'ai_primary' : 'base_only')
  const parserWarning = renderParserValue(normalized.parser_warning)
  const fallbackUsed = Boolean(normalized.fallback_used)
  const parserStatus = {
    mode: parserMode,
    fallback_used: fallbackUsed,
    warning: parserWarning === '-' ? null : parserWarning,
  }
  return (
    <div className="parserDetailsSection">
      <button type="button" className="parserDetailsToggle" onClick={() => onToggle(candidateId)}>
        {expanded ? 'Hide Details' : 'View Details'}
      </button>
      {expanded ? (
        <div className="parserDetailsPanel">
          <div className="parserDetailsMeta">
            <span><strong>Parser Version:</strong> {normalized.parser_version ?? '-'}</span>
            <span><strong>Source:</strong> {normalized.source ?? source}</span>
            <span><strong>Mode:</strong> {parserMode}</span>
            <span><strong>Fallback Used:</strong> {fallbackUsed ? 'Yes' : 'No'}</span>
          </div>
          <div className="parserDetailsSummaryGrid">
            <section className="parserDetailsBlock parserDetailsSummaryBlock">
              <h3>Parser Status</h3>
              <pre>{renderParserValue(parserStatus)}</pre>
            </section>
            <section className="parserDetailsBlock parserDetailsSummaryBlock">
              <h3>Final Skills Text</h3>
              <pre>{recordStringValue(finalResult, 'skills_text') || '-'}</pre>
            </section>
            <section className="parserDetailsBlock parserDetailsSummaryBlock">
              <h3>Approved Skills</h3>
              <pre>{approvedSkills || '-'}</pre>
            </section>
            <section className="parserDetailsBlock parserDetailsSummaryBlock">
              <h3>Unknown Skills</h3>
              <pre>{unknownSkills.length > 0 ? unknownSkills.join(', ') : '-'}</pre>
            </section>
            <section className="parserDetailsBlock parserDetailsSummaryBlock">
              <h3>ATS Summary</h3>
              <pre>{atsSummary || `ATS Score: ${formatAtsScore(atsScore)} (${getAtsStrengthLabel(atsScore)})`}</pre>
            </section>
          </div>
          <div className="parserDetailsGrid">
            <section className="parserDetailsBlock">
              <h3>Final Extracted Result</h3>
              <pre>{renderParserValue(finalResult)}</pre>
            </section>
            <section className="parserDetailsBlock">
              <h3>{fallbackUsed ? 'Base Fallback Result' : 'Base Parser Result'}</h3>
              <pre>{renderParserValue(baseResult)}</pre>
            </section>
            <section className="parserDetailsBlock">
              <h3>AI Extractor Result</h3>
              <pre>{renderParserValue(aiExtractor ?? {})}</pre>
            </section>
            <section className="parserDetailsBlock">
              <h3>Skills Audit</h3>
              <pre>{renderParserValue(skillsAudit ?? {})}</pre>
            </section>
            <section className="parserDetailsBlock">
              <h3>ATS Breakdown</h3>
              <pre>{renderParserValue({
                score: atsScore == null ? '-' : `${formatAtsScore(atsScore)} (${getAtsStrengthLabel(atsScore)})`,
                source: atsSource ?? '-',
                ...(atsBreakdown ?? {}),
              })}</pre>
            </section>
            <section className="parserDetailsBlock">
              <h3>Source Hints</h3>
              <pre>{renderParserValue(sourceHints ?? {})}</pre>
            </section>
            <section className="parserDetailsBlock">
              <h3>AI Evidence</h3>
              <pre>{renderParserValue(aiExtractor ? { confidence: aiExtractor.confidence, evidence: aiExtractor.evidence, error: aiExtractor.error } : {})}</pre>
            </section>
          </div>
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
    nvoids_locations: [],
    feature_auto_send: false,
    feature_retry_queue: false,
    feature_ai_enabled: false,
    feature_ai_extractor_enabled: false,
    feature_semantic_enabled: false,
    feature_groq_job_parser_enabled: false,
    draft_text_size: 'normal',
    fallback_draft_template: '',
    signature_name: '',
    signature_phone: '',
    signature_email: '',
    preferred_employer_cc_email: '',
    resume_display_name: '',
    policy: defaultPolicy,
  })
  const [resumeFile, setResumeFile] = useState<File | null>(null)
  const [resumeSkillsInput, setResumeSkillsInput] = useState('')
  const [resumeSkillEdits, setResumeSkillEdits] = useState<Record<number, string>>({})
  const [attachmentUploadFiles, setAttachmentUploadFiles] = useState<File[]>([])
  const [resumeAssets, setResumeAssets] = useState<ResumeAsset[]>([])
  const [attachmentFiles, setAttachmentFiles] = useState<AttachmentAsset[]>([])
  const [pendingSkills, setPendingSkills] = useState<PendingSkill[]>([])
  const [skillsLoading, setSkillsLoading] = useState(false)
  const [skillActionKey, setSkillActionKey] = useState<string | null>(null)
  const [pendingJobIntentSignals, setPendingJobIntentSignals] = useState<JobIntentLearningSignal[]>([])
  const [approvedJobIntentSignals, setApprovedJobIntentSignals] = useState<JobIntentLearningSignal[]>([])
  const [jobIntentLoading, setJobIntentLoading] = useState(false)
  const [jobIntentActionKey, setJobIntentActionKey] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [nvoidsRunning, setNvoidsRunning] = useState(false)
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
  const [activePage, setActivePage] = useState<'run_queue' | 'needs_review' | 'failed_mapping' | 'recent_runs' | 'sent_items' | 'premium_numbers'>('run_queue')
  const [dynamicPolicyBeta, setDynamicPolicyBeta] = useState(false)
  const [selectedProfileToApply, setSelectedProfileToApply] = useState<PolicyProfileName>('Balanced')
  const [lastAppliedProfile, setLastAppliedProfile] = useState<PolicyProfileName | null>(null)
  const [skillDraft, setSkillDraft] = useState('')
  const [nvoidsLocationDraft, setNvoidsLocationDraft] = useState('')
  const [employerDomainDraft, setEmployerDomainDraft] = useState('')
  const [employerDomainError, setEmployerDomainError] = useState('')
  const [numberReviewCards, setNumberReviewCards] = useState<NumberReviewCard[]>([])
  const [recruiterNumberCards, setRecruiterNumberCards] = useState<RecruiterNumberCard[]>([])
  const [employerNumberCards, setEmployerNumberCards] = useState<EmployerNumberCard[]>([])
  const [opportunityCards, setOpportunityCards] = useState<RecruiterOpportunityCard[]>([])
  const [premiumPageMeta, setPremiumPageMeta] = useState(defaultPremiumPageMeta())
  const [premiumLoading, setPremiumLoading] = useState(false)
  const [premiumError, setPremiumError] = useState('')
  const [premiumScopeFilter, setPremiumScopeFilter] = useState<PremiumScope>('all_review')
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
  const premiumRequestTrackerRef = useRef(0)

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
      feature_ai_extractor_enabled: Boolean(payload.feature_ai_extractor_enabled),
      feature_semantic_enabled: Boolean(payload.feature_semantic_enabled),
      feature_groq_job_parser_enabled: Boolean(payload.feature_groq_job_parser_enabled),
      default_gmail_query: payload.default_gmail_query || payload.gmail_query || 'is:unread',
      saved_gmail_queries: payload.saved_gmail_queries ?? [],
      default_date_mode: payload.default_date_mode === 'off' ? 'off' : 'today',
      feature_auto_poll_interval_minutes: Math.max(1, Math.min(payload.feature_auto_poll_interval_minutes || 10, 1440)),
      feature_nvoids_enabled: Boolean(payload.feature_nvoids_enabled ?? true),
      feature_nvoids_auto_sync: Boolean(payload.feature_nvoids_auto_sync ?? false),
      feature_nvoids_poll_interval_minutes: Math.max(1, Math.min(payload.feature_nvoids_poll_interval_minutes || 30, 1440)),
      nvoids_batch_limit: Math.max(1, Math.min(payload.nvoids_batch_limit || 10, 50)),
      nvoids_locations: payload.nvoids_locations ?? [],
      employer_domains: payload.employer_domains ?? [],
      draft_text_size: normalizeDraftTextSize(payload.draft_text_size),
      preferred_employer_cc_email: payload.preferred_employer_cc_email ?? '',
      resume_display_name: payload.resume_display_name ?? '',
      policy: normalizeDynamicPolicy(payload.policy ?? defaultPolicy, payload),
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

  const loadResumes = async () => {
    const res = await fetch(`${apiBase}/settings/resumes`)
    if (!res.ok) throw new Error('Failed to load resumes')
    const payload = (await res.json()) as ResumeAsset[]
    setResumeAssets(payload)
    setResumeSkillEdits(Object.fromEntries(payload.map((resume) => [resume.id, resume.skills_text ?? ''])))
  }

  const loadAttachmentFiles = async () => {
    const res = await fetch(`${apiBase}/settings/attachments`)
    if (!res.ok) throw new Error('Failed to load attachment files')
    setAttachmentFiles((await res.json()) as AttachmentAsset[])
  }

  const loadPendingSkills = async () => {
    const res = await fetch(`${apiBase}/settings/skills/pending`)
    if (!res.ok) throw new Error('Failed to load pending skills')
    setPendingSkills((await res.json()) as PendingSkill[])
  }

  const loadPendingJobIntentSignals = async () => {
    const res = await fetch(`${apiBase}/settings/job-intent-learning/pending`)
    if (!res.ok) throw new Error('Failed to load pending job-intent learning')
    setPendingJobIntentSignals((await res.json()) as JobIntentLearningSignal[])
  }

  const loadApprovedJobIntentSignals = async () => {
    const res = await fetch(`${apiBase}/settings/job-intent-learning/approved`)
    if (!res.ok) throw new Error('Failed to load approved job-intent learning')
    setApprovedJobIntentSignals((await res.json()) as JobIntentLearningSignal[])
  }

  const loadSkillUpgradeData = async () => {
    setSkillsLoading(true)
    try {
      await loadPendingSkills()
    } finally {
      setSkillsLoading(false)
    }
  }

  const loadJobIntentLearningData = async () => {
    setJobIntentLoading(true)
    try {
      await Promise.all([loadPendingJobIntentSignals(), loadApprovedJobIntentSignals()])
    } finally {
      setJobIntentLoading(false)
    }
  }

  const activeResume = resumeAssets.find((item) => item.is_current) ?? null

  const loadPremiumNumbers = async (opts?: { append?: boolean; cursor?: number | null }) => {
    const append = Boolean(opts?.append)
    const cursor = opts?.cursor ?? 0
    const scope = premiumScopeFilter
    const requestId = premiumRequestTrackerRef.current + 1
    premiumRequestTrackerRef.current = requestId
    setPremiumLoading(true)
    setPremiumError('')
    try {
      const url = buildPremiumScopeUrl({
        apiBase,
        scope,
        cursor,
        limit: PREMIUM_PAGE_LIMIT,
        q: premiumSearch,
        mailDate: settings.mail_date,
        opportunityStatus: opportunityStatusFilter,
        opportunitySource: opportunitySourceFilter,
      })
      const res = await fetch(url)
      if (!res.ok) {
        const errorLabel =
          scope === 'all_review'
            ? 'number review queue'
            : scope === 'recruiter_numbers'
              ? 'recruiter numbers'
              : scope === 'employer_numbers'
                ? 'employer numbers'
                : 'recruiter opportunities'
        throw new Error(`Failed to load ${errorLabel}`)
      }
      if (requestId !== premiumRequestTrackerRef.current) return
      if (scope === 'all_review') {
        const payload = (await res.json()) as PaginatedListResponse<NumberReviewCard>
        setNumberReviewCards((prev) => (append ? [...prev, ...payload.items] : payload.items))
        setPremiumPageMeta((prev) => ({
          ...prev,
          [scope]: { nextCursor: payload.next_cursor, hasNext: payload.has_next },
        }))
      } else if (scope === 'recruiter_numbers') {
        const payload = (await res.json()) as PaginatedListResponse<RecruiterNumberCard>
        setRecruiterNumberCards((prev) => (append ? [...prev, ...payload.items] : payload.items))
        setPremiumPageMeta((prev) => ({
          ...prev,
          [scope]: { nextCursor: payload.next_cursor, hasNext: payload.has_next },
        }))
      } else if (scope === 'employer_numbers') {
        const payload = (await res.json()) as PaginatedListResponse<EmployerNumberCard>
        setEmployerNumberCards((prev) => (append ? [...prev, ...payload.items] : payload.items))
        setPremiumPageMeta((prev) => ({
          ...prev,
          [scope]: { nextCursor: payload.next_cursor, hasNext: payload.has_next },
        }))
      } else {
        const payload = (await res.json()) as PaginatedListResponse<RecruiterOpportunityCard>
        setOpportunityCards((prev) => (append ? [...prev, ...payload.items] : payload.items))
        setPremiumPageMeta((prev) => ({
          ...prev,
          [scope]: { nextCursor: payload.next_cursor, hasNext: payload.has_next },
        }))
      }
    } catch (e) {
      if (requestId === premiumRequestTrackerRef.current) {
        setPremiumError((e as Error).message)
      }
    } finally {
      if (requestId === premiumRequestTrackerRef.current) {
        setPremiumLoading(false)
      }
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

  const loadRecentRuns = async () => {
    const res = await fetch(`${apiBase}/recent-runs?limit=${RECENT_RUNS_LIMIT}`)
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
          loadResumes(),
          loadAttachmentFiles(),
          loadSkillUpgradeData(),
          loadJobIntentLearningData(),
          loadAiStatus(),
          loadTelegramStatus(),
          loadRecentRuns(),
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
  }, [activePage, premiumScopeFilter, premiumSearch, settings.mail_date, opportunityStatusFilter, opportunitySourceFilter])

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
    fd.append('skills_text', resumeSkillsInput)
    try {
      const res = await fetch(`${apiBase}/settings/resume`, { method: 'POST', body: fd })
      if (!res.ok) throw new Error('Failed to upload resume')
      setResumeFile(null)
      setResumeSkillsInput('')
      await loadResumes()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const saveResumeSkills = async (resumeId: number) => {
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/resumes/${resumeId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skills_text: resumeSkillEdits[resumeId] ?? '' }),
      })
      if (!res.ok) throw new Error('Failed to save resume skills')
      await loadResumes()
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
      await loadResumes()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const deleteResumeAsset = async (resumeId: number) => {
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/resumes/${resumeId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete resume')
      await loadResumes()
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
      await loadAttachmentFiles()
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
      await loadAttachmentFiles()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const deleteAttachmentFile = async (attachmentId: number) => {
    setError('')
    try {
      const res = await fetch(`${apiBase}/settings/attachments/${attachmentId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete attachment file')
      await loadAttachmentFiles()
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
      await loadSkillUpgradeData()
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
      await loadSkillUpgradeData()
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
      await loadSkillUpgradeData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSkillActionKey(null)
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
      await loadJobIntentLearningData()
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
      await loadJobIntentLearningData()
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
      setLogs((prev) => [
        {
          ...data,
          run_source: data.run_source ?? 'automation_run',
          skipped_item_count: data.skipped_item_count ?? data.skipped_count ?? 0,
          skipped_items: [],
          skipped_items_loaded: false,
          skipped_items_loading: false,
          skipped_items_error: null,
        },
        ...prev.filter((item) => item.run_key !== data.run_key),
      ].slice(0, RECENT_RUNS_LIMIT))
      if (data.status === 'oauth_required' || data.status === 'oauth_in_progress') {
        setError(data.detail)
      }
      await loadStatus()
      await loadAiStatus()
      await loadTelegramStatus()
      await refreshVisibleCandidates(settings.mail_date ?? null, { activeOnly: false })
      await loadProductivityAnalytics(timeRange)
      await loadRecentRuns()
      await loadJobIntentLearningData()
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

  const enabledAttachmentNames = attachmentFiles.filter((item) => item.is_enabled).map((item) => item.file_name)

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
      const data = (await res.json()) as { source_type: string; run_key?: string | null; fetched_count: number; created_count: number; deduped_count: number; failed_count: number; skipped_location_count: number }
      setLogs((prev) => [
        {
          run_key: data.run_key ?? null,
          run_source: 'nvoids_sync',
          status: 'ok',
          detail: `nvoids sync complete: fetched=${data.fetched_count} created=${data.created_count} deduped=${data.deduped_count} skipped_location=${data.skipped_location_count} failed=${data.failed_count}`,
          skipped_count: data.skipped_location_count,
          failed_count: data.failed_count,
          skipped_item_count: data.skipped_location_count,
          email_id: null,
          skipped_items: [],
          skipped_items_loaded: false,
          skipped_items_loading: false,
          skipped_items_error: null,
        },
        ...prev.filter((item) => item.run_key !== data.run_key),
      ].slice(0, RECENT_RUNS_LIMIT))
      await refreshVisibleCandidates(settings.mail_date ?? null, { activeOnly: false })
      await loadPremiumNumbers()
      await loadRecentRuns()
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

  const regenerateCandidate = async (candidateId: number) => {
    setRegeneratingId(candidateId)
    setError('')
    try {
      const res = await fetch(`${apiBase}/candidates/${candidateId}/regenerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          preserve_manual_routing: true,
          preserve_review_visibility: true,
        }),
      })
      if (!res.ok) {
        const details = await res.json().catch(() => null)
        throw new Error(details?.detail ?? 'Regenerate failed')
      }
      const updated = (await res.json()) as Candidate
      setDraftEdits((prev) => ({ ...prev, [updated.id]: updated.draft_reply ?? '' }))
      schedulePostMutationRefresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRegeneratingId(null)
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

  const canTrustRouting = (candidate: Candidate) =>
    candidate.routing_confirmed ||
    (['safe', 'confirmed'].includes(candidate.routing_status) && candidate.routing_confidence >= 0.8)

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
  const groqLastDuration = aiStatus?.groq_last_duration_ms
    ? `${(aiStatus.groq_last_duration_ms / 1000).toFixed(1)}s`
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
                  <div className="row"><span className="label">Model</span><span className="tag">{aiStatus?.model ?? 'deepseek-chat'}</span></div>
                  <div className="row"><span className="label">Connection</span><span className="dotOk">{aiStatus?.connected ? 'Healthy' : 'Disconnected'}</span></div>
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
                    <span>Enable Groq Smart Job Parser</span>
                    <span className="toggleSwitch">
                      <input
                        type="checkbox"
                        checked={settings.feature_groq_job_parser_enabled}
                        onChange={(e) => setSettings({ ...settings, feature_groq_job_parser_enabled: e.target.checked })}
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
                      onChange={(e) => updateRuleValue('score_threshold', e.target.value)}
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

              <SkillUpgradeSection
                pendingSkills={pendingSkills}
                loading={skillsLoading}
                busySkillKey={skillActionKey}
                approveAllSkills={approveAllPendingSkills}
                approveSkill={approvePendingSkill}
                dismissSkill={dismissPendingSkill}
              />

              <JobIntentLearningSection
                pendingSignals={pendingJobIntentSignals}
                approvedSignals={approvedJobIntentSignals}
                loading={jobIntentLoading}
                busySignalKey={jobIntentActionKey}
                approveSignal={approvePendingJobIntentSignal}
                dismissSignal={dismissPendingJobIntentSignal}
              />

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
                    <input
                      value={(draftRules.accepted_location.locations ?? settings.accepted_locations).join(', ')}
                      onChange={(e) => updateRuleValue('accepted_location', e.target.value)}
                      placeholder="texas, remote"
                    />
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
                    <input
                      value={(draftRules.must_have_skills.skills ?? settings.must_have_skills).join(', ')}
                      onChange={(e) => updateRuleValue('must_have_skills', e.target.value)}
                      placeholder="java, spring"
                    />
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
                    F2F non-Texas rule
                    <select
                      value={draftRules.f2f_non_texas.mode}
                      onChange={(e) => updateRuleMode('f2f_non_texas', e.target.value as RuleMode)}
                    >
                      <option value="ignore">Ignore</option>
                      <option value="warn">Warn Only</option>
                      <option value="block">Block Draft</option>
                    </select>
                  </label>
                  <p className="subtle">Controls how face-to-face roles outside Texas are handled.</p>

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
                  <label>
                    Preferred Employer CC
                    <input
                      type="email"
                      value={settings.preferred_employer_cc_email}
                      onChange={(e) => setSettings({ ...settings, preferred_employer_cc_email: e.target.value })}
                      placeholder="sheshwika@horizonsoftech.net"
                    />
                  </label>
                  <p className="subtle">Used as the employer CC for Nvoids/external-feed drafts. Manual per-candidate recipient fixes still win.</p>
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
                  <div className="stack">
                    <strong>Attachment files</strong>
                    <p className="subtle">Upload global reusable files that will be sent alongside the active resume.</p>
                    <input
                      type="file"
                      multiple
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

              <ResumeDatabaseSection
                activeResume={activeResume}
                resumeFile={resumeFile}
                resumeSkillsInput={resumeSkillsInput}
                resumeSkillEdits={resumeSkillEdits}
                resumeAssets={resumeAssets}
                setResumeFile={setResumeFile}
                setResumeSkillsInput={setResumeSkillsInput}
                setResumeSkillEdits={setResumeSkillEdits}
                uploadResume={uploadResume}
                saveResumeSkills={saveResumeSkills}
                toggleResumeAsset={toggleResumeAsset}
                deleteResumeAsset={deleteResumeAsset}
              />

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
                    Nvoids sync is isolated from Gmail run queue, filters by the saved Nvoids locations, and is serialized to avoid concurrent DB load.
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
            const parserDetails = normalizeParserDetails(item.parser_details)
            const parserExpanded = Boolean(expandedParserDetailIds[item.id])
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
                <p><strong>ATS Score:</strong> {formatAtsScore(item.ats_score)} {item.ats_score != null ? `(${getAtsStrengthLabel(item.ats_score)})` : ''}</p>
                {renderRoutingPanel(item)}
                <p><strong>Resume:</strong> {item.resume_file_name ?? '-'}</p>
                <ResumePickerPanel candidate={item} />
                <p><strong>Attachment files:</strong> {(enabledAttachmentNames.length > 0 ? enabledAttachmentNames : item.attachment_file_names ?? []).join(', ') || '-'}</p>
                <p>
                  <strong>Draft source:</strong> {getDraftSourceLabel(item.draft_source)}
                  {item.draft_model ? ` (${item.draft_model})` : ''}
                </p>
                <p><strong>Resume Context:</strong> {getResumeContextLabel(item.draft_resume_context_status)}</p>
                <ParserDetailsPanel
                  candidateId={item.id}
                  source={item.source}
                  parserDetails={parserDetails}
                  atsScore={item.ats_score}
                  atsSource={item.ats_score_source}
                  atsSummary={item.ats_summary}
                  atsBreakdown={item.ats_breakdown}
                  expanded={parserExpanded}
                  onToggle={(candidateId) =>
                    setExpandedParserDetailIds((prev) => ({
                      ...prev,
                      [candidateId]: !prev[candidateId],
                    }))
                  }
                />
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
                    style={draftTextSizeToPreviewStyle(settings.draft_text_size)}
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
                    onClick={() => regenerateCandidate(item.id)}
                    disabled={regeneratingId === item.id || sendingId === item.id || rejectingId === item.id || movingToFailedId === item.id}
                    title="Re-run parser, resume match, ATS and semantic scoring, routing, and draft generation with current settings"
                  >
                    {regeneratingId === item.id ? 'Regenerating...' : 'Regenerate'}
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
                <div className="rowBtns">
                  <button
                    type="button"
                    onClick={() => saveRoutingAndRequeue(item.id)}
                    disabled={fixingId === item.id || deletingFailedId === item.id || !fix.to || !fix.cc}
                  >
                    {fixingId === item.id ? 'Saving...' : 'Save Mapping & Move to Review'}
                  </button>
                  <button
                    type="button"
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
            <article key={item.run_key ?? `${item.email_id ?? 'none'}-${index}`} className="emailItem">
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
              {((item.skipped_item_count ?? 0) > 0 || (item.skipped_items?.length ?? 0) > 0) ? (
                <div className="stack">
                  <button type="button" onClick={() => void toggleRecentRunItems(item.run_key)}>
                    {item.skipped_items_loaded ? `Hide Skipped Items (${item.skipped_item_count ?? item.skipped_items?.length ?? 0})` : `Skipped Items (${item.skipped_item_count ?? 0})`}
                  </button>
                  {item.skipped_items_loading ? <p className="subtle">Loading skipped items...</p> : null}
                  {item.skipped_items_error ? <p className="errorMessage">{item.skipped_items_error}</p> : null}
                  {item.skipped_items_loaded ? (
                    item.skipped_items && item.skipped_items.length > 0 ? (
                      <div className="stack">
                        {item.skipped_items.map((skipped) => (
                          <article key={`${item.run_key}-${skipped.id}`} className="emailItem">
                            <p><strong>Source:</strong> {getSourceLabel(skipped.source_type)}</p>
                            <p><strong>Title:</strong> {renderTextOrDash(skipped.title_or_subject)}</p>
                            <p><strong>Why:</strong> {renderTextOrDash(skipped.reason_detail || skipped.reason_code)}</p>
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
                            {skipped.intent_evidence.length > 0 ? (
                              <div className="automationMetrics">
                                <strong>Evidence:</strong>
                                {skipped.intent_evidence.map((entry) => (
                                  <span key={`${skipped.id}-${entry}`} className="tag">{entry}</span>
                                ))}
                              </div>
                            ) : null}
                            {skipped.intent_negative_evidence.length > 0 ? (
                              <div className="automationMetrics">
                                <strong>Negative Evidence:</strong>
                                {skipped.intent_negative_evidence.map((entry) => (
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
                          </article>
                        ))}
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
            <section className="card pageSection">
              <h2>Premium Numbers</h2>
              <div className="actionBar">
                <select
                  value={premiumScopeFilter}
                  onChange={(e) => setPremiumScopeFilter(e.target.value as PremiumScope)}
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
              {premiumPageMeta[premiumScopeFilter].hasNext ? (
                <button
                  type="button"
                  onClick={() => {
                    const nextCursor = premiumPageMeta[premiumScopeFilter].nextCursor
                    if (nextCursor == null) return
                    loadPremiumNumbers({ append: true, cursor: nextCursor }).catch((e) =>
                      setPremiumError((e as Error).message),
                    )
                  }}
                  disabled={premiumLoading || premiumPageMeta[premiumScopeFilter].nextCursor == null}
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
          {sentQueue.map((item) => {
            const isExpanded = Boolean(expandedSentDetailIds[item.id])
            const sentDetails = sentDetailsById[item.id]
            const sentDetailError = sentDetailErrors[item.id]
            const sentDetailLoading = Boolean(sentDetailLoadingIds[item.id])
            const parserExpanded = Boolean(expandedParserDetailIds[item.id])
            const listingUrl = sourceListingUrl(item)
            return (
              <article key={`sent-${item.id}`} className="emailItem sentItemCard">
                <div className="sentItemHeader">
                  <div className="sentItemHeaderText">
                    <p><strong>Email ID:</strong> {item.id}</p>
                    <p><strong>From:</strong> {item.sender}</p>
                    <p><strong>Subject:</strong> {item.subject}</p>
                    <p><strong>Sent at:</strong> {item.sent_at ? new Date(item.sent_at).toLocaleString() : '-'}</p>
                  </div>
                  <div className="sentItemHeaderActions">
                    <span className="sourceBadge">{getSourceLabel(item.source)}</span>
                    <button type="button" onClick={() => void toggleSentDetails(item.id)}>
                      {isExpanded ? 'Hide Details' : 'View Details'}
                    </button>
                  </div>
                </div>
                {isExpanded ? (
                  <div className="parserDetailsPanel sentItemDetailsPanel">
                    {sentDetailLoading ? <p className="subtle">Loading sent item details...</p> : null}
                    {sentDetailError ? <p className="errorMessage">{sentDetailError}</p> : null}
                    {sentDetails ? (
                      <>
                        <div className="parserDetailsSummaryGrid">
                          <section className="parserDetailsBlock parserDetailsSummaryBlock">
                            <h3>Source</h3>
                            <div className="sentItemLinkList">
                              <p><strong>Source:</strong> {renderTextOrDash(sentDetails.source_label)}</p>
                              <p>
                                <strong>Requirement Link:</strong>{' '}
                                {sentDetails.requirement_received_link ? (
                                  <a href={sentDetails.requirement_received_link} target="_blank" rel="noreferrer">
                                    Open requirement
                                  </a>
                                ) : '-'}
                              </p>
                              <p>
                                <strong>Original Gmail Link:</strong>{' '}
                                {item.gmail_message_url ? (
                                  <a href={item.gmail_message_url} target="_blank" rel="noreferrer">
                                    Open original email
                                  </a>
                                ) : '-'}
                              </p>
                              <p>
                                <strong>Source Listing Link:</strong>{' '}
                                {listingUrl ? (
                                  <a href={listingUrl} target="_blank" rel="noreferrer">
                                    Open source listing
                                  </a>
                                ) : '-'}
                              </p>
                              <p>
                                <strong>Sent Gmail Link:</strong>{' '}
                                {sentDetails.sent_gmail_message_link ? (
                                  <a href={sentDetails.sent_gmail_message_link} target="_blank" rel="noreferrer">
                                    Open sent message
                                  </a>
                                ) : '-'}
                              </p>
                            </div>
                          </section>
                          <section className="parserDetailsBlock parserDetailsSummaryBlock">
                            <h3>Requirement</h3>
                            <pre>{[
                              `Role: ${renderTextOrDash(item.role)}`,
                              `Location: ${renderTextOrDash(item.location)}`,
                              `Salary: ${renderTextOrDash(item.salary_text)}`,
                              `Skills: ${renderTextOrDash(item.skills_text)}`,
                              `Company: ${renderTextOrDash(sentDetails.company)}`,
                              `End Client: ${renderTextOrDash(sentDetails.end_client)}`,
                              `Implementation Partner: ${renderTextOrDash(sentDetails.implementation_partner)}`,
                              `Vendor: ${renderTextOrDash(sentDetails.vendor)}`,
                              `Domain Mentioned: ${renderTextOrDash(sentDetails.domain_mentioned)}`,
                              `Experience Required: ${renderTextOrDash(sentDetails.experience_required)}`,
                              `Mandatory Skills: ${renderListOrDash(sentDetails.mandatory_skills)}`,
                              `Missing Skills: ${renderListOrDash(sentDetails.missing_skills)}`,
                            ].join('\n')}</pre>
                          </section>
                          <section className="parserDetailsBlock parserDetailsSummaryBlock">
                            <h3>Resume / Send Audit</h3>
                            <pre>{[
                              `Resume Variant Sent: ${renderTextOrDash(sentDetails.resume_variant_sent ?? item.resume_file_name)}`,
                              `Attached Files: ${renderListOrDash(sentDetails.attached_files)}`,
                              `To: ${renderTextOrDash(sentDetails.to_email ?? item.recipient_email)}`,
                              `CC: ${renderTextOrDash(sentDetails.cc_email ?? item.cc_email)}`,
                              `ATS Score: ${formatAtsScore(sentDetails.ats_score ?? item.ats_score)}${(sentDetails.ats_score ?? item.ats_score) != null ? ` (${getAtsStrengthLabel(sentDetails.ats_score ?? item.ats_score)})` : ''}`,
                              `ATS Summary: ${renderTextOrDash(sentDetails.ats_summary ?? item.ats_summary)}`,
                            ].join('\n')}</pre>
                          </section>
                          <section className="parserDetailsBlock parserDetailsSummaryBlock">
                            <h3>Recruiter</h3>
                            <pre>{[
                              `Recruiter Name: ${renderTextOrDash(sentDetails.recruiter_name)}`,
                              `Recruiter Email: ${renderTextOrDash(sentDetails.recruiter_email)}`,
                              `Recruiter Phone: ${renderTextOrDash(sentDetails.recruiter_phone)}`,
                            ].join('\n')}</pre>
                          </section>
                        </div>
                        <ParserDetailsPanel
                          candidateId={item.id}
                          source={item.source}
                          parserDetails={item.parser_details}
                          atsScore={item.ats_score}
                          atsSource={item.ats_score_source}
                          atsSummary={item.ats_summary}
                          atsBreakdown={item.ats_breakdown}
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
