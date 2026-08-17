// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type MockResponse = {
  ok: boolean
  json: () => Promise<unknown>
}

function makeResponse(payload: unknown): MockResponse {
  return {
    ok: true,
    json: async () => payload,
  }
}

function makeBootstrapPayload() {
  return {
    settings: {
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
      draft_text_size: 'normal',
      fallback_draft_template: '',
      signature_name: '',
      signature_phone: '',
      signature_email: '',
      preferred_employer_cc_email: '',
      resume_display_name: '',
      policy: null,
    },
    resumes: [],
    attachments: [],
    pending_skills: [],
    pending_job_intent_signals: [],
    approved_job_intent_signals: [],
    loaded_at: '2026-07-09T00:00:00Z',
    owner_id: 'default-owner',
  }
}

describe('Needs Review regenerate flow', () => {
  const cleanups: Array<() => void> = []

  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('renders Regenerate, posts to the endpoint, shows the pending label, and refreshes the draft', async () => {
    const initialCandidate: any = {
      id: 42,
      owner_id: 'default-owner',
      sender: 'Recruiter <recruiter@example.com>',
      subject: 'Java Developer',
      body: 'Need Java and Spring Boot',
      role: 'Java Developer',
      location: 'Remote',
      salary_text: '',
      skills_text: 'Java, Spring Boot',
      score: 82,
      decision: 'Qualified',
      state: 'needs_review',
      decision_reason: 'Qualified by hard filters + AI score',
      hard_filter_result: null,
      auto_reject_reason: null,
      ai_score: 0.82,
      ai_score_source: 'v1_rules_plus_ai',
      ai_summary: 'AI fit score computed from role keywords and skill overlap (0.82)',
      ats_score: 84,
      ats_score_source: 'hybrid_structured_only',
      ats_summary: 'ATS hybrid score 84/100',
      ats_breakdown: { raw_overlap: 1 },
      semantic_input_source: null,
      semantic_input_chars: null,
      semantic_chunks: null,
      semantic_fallback_reason: null,
      keyword_source: null,
      thread_snapshot_used: null,
      thread_snapshot_email_id: null,
      skip_reason: null,
      sync_batch_id: null,
      draft_reply: 'Old draft body',
      draft_source: 'rules_only',
      draft_model: null,
      draft_ai_error: 'old ai error',
      draft_resume_context_status: 'limited',
      draft_quality: null,
      approval_status: 'pending',
      sent_status: 'not_sent',
      source: 'gmail',
      external_message_id: 'msg-1',
      external_thread_id: 'thread-1',
      external_rfc_message_id: null,
      gmail_received_at: null,
      applied_gmail_label: null,
      applied_gmail_label_id: null,
      applied_gmail_label_at: null,
      gmail_message_url: null,
      recipient_email: 'recruiter@example.com',
      cc_email: 'manager@example.com',
      routing_status: 'safe',
      routing_confidence: 0.92,
      routing_reason: 'safe route',
      routing_evidence: [],
      routing_candidates: [],
      routing_confirmed: true,
      resume_asset_id: 7,
      resume_file_name: 'JavaVariant.docx',
      parser_details: { parser_version: 'ai_parser_modes_v1' },
      attachment_file_names: [],
      sent_at: null,
      gmail_sent_id: null,
      last_error: null,
      created_at: '2026-06-24T00:00:00Z',
      updated_at: '2026-06-24T00:00:00Z',
    }
    const updatedCandidate = {
      ...initialCandidate,
      role: 'Senior Java Developer',
      draft_reply: 'Fresh AI draft',
      draft_source: 'ai_primary',
      draft_ai_error: null as string | null,
      parser_details: { parser_version: 'regen-v1' },
      updated_at: '2026-07-03T10:00:00Z',
    }

    let needsReviewItems = [initialCandidate]
    let resolveRegenerate: (() => void) | null = null
    const regenerateRequestBodies: string[] = []

    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/gmail/status')) return Promise.resolve(makeResponse({ configured: true, authenticated: true, token_path: 'token.json', last_sync_at: null, detail: 'ok' }))
        if (url.endsWith('/ai/status')) return Promise.resolve(makeResponse({ configured: true, connected: true, running: false, provider: 'mock', model: 'mock', detail: 'ok', last_error: null, last_started_at: null, last_finished_at: null, last_duration_ms: null, last_draft_source: null }))
        if (url.endsWith('/telegram/status')) return Promise.resolve(makeResponse({ enabled: false, polling: false, alerts_enabled: false, authorized_chats: 0, detail: 'off' }))
        if (url.endsWith('/gmail/oauth/url')) return Promise.resolve(makeResponse({ authorization_url: null }))
        if (url.includes('/settings/bootstrap')) return Promise.resolve(makeResponse(makeBootstrapPayload()))
        if (url.includes('/recent-runs/')) return Promise.resolve(makeResponse({ items: [], next_cursor: null, has_next: false }))
        if (url.includes('/recent-runs')) return Promise.resolve(makeResponse({ items: [], next_cursor: null, has_next: false }))
        if (url.includes('/candidates?')) {
          if (url.includes('state=needs_review')) return Promise.resolve(makeResponse({ items: needsReviewItems, next_cursor: null, has_next: false }))
          return Promise.resolve(makeResponse({ items: [], next_cursor: null, has_next: false }))
        }
        if (url.endsWith('/candidates/42/regenerate') && init?.method === 'POST') {
          regenerateRequestBodies.push(String(init.body ?? ''))
          return new Promise((resolve) => {
            resolveRegenerate = () => {
              needsReviewItems = [updatedCandidate]
              resolve(makeResponse(updatedCandidate))
            }
          })
        }
        if (url.includes('/analytics/events?')) return Promise.resolve(makeResponse([]))
        if (url.includes('/analytics/trend?')) return Promise.resolve(makeResponse({ range: '7d', bucket: 'day', trend_direction: 'flat', trend_delta_pct: 0, kpi_total_sent: 0, previous_period_total_sent: 0, bars: [] }))
        if (url.includes('/analytics/events/view')) return Promise.resolve(makeResponse({ ok: true }))
        if (url.includes('/number-review')) return Promise.resolve(makeResponse({ items: [], next_cursor: null, has_next: false }))
        if (url.includes('/recruiter-numbers')) return Promise.resolve(makeResponse({ items: [], next_cursor: null, has_next: false }))
        if (url.includes('/employer-numbers')) return Promise.resolve(makeResponse({ items: [], next_cursor: null, has_next: false }))
        if (url.includes('/recruiter-opportunities')) return Promise.resolve(makeResponse({ items: [], next_cursor: null, has_next: false }))
        throw new Error(`Unhandled fetch: ${url}`)
      }),
    )

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    await act(async () => {
      root.render(<App />)
    })
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    const needsReviewButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Needs Review'),
    ) as HTMLButtonElement | undefined
    expect(needsReviewButton).toBeDefined()

    await act(async () => {
      needsReviewButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
      await Promise.resolve()
    })

    const regenerateButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Regenerate'),
    ) as HTMLButtonElement | undefined
    expect(regenerateButton).toBeDefined()

    await act(async () => {
      regenerateButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })

    expect(container.textContent ?? '').toContain('Regenerating...')

    await act(async () => {
      resolveRegenerate?.()
      await Promise.resolve()
    })

    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(regenerateRequestBodies).toHaveLength(1)
    expect(regenerateRequestBodies[0]).toContain('"preserve_manual_routing":true')
    expect(regenerateRequestBodies[0]).toContain('"preserve_review_visibility":true')
    expect(container.textContent ?? '').toContain('Fresh AI draft')
    expect(container.textContent ?? '').toContain('ai_primary')
  })
})
