// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

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

describe('ATS review UI', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
  })

  it('renders the ATS score in Needs Review without changing the real resume variant name', async () => {
    const candidate = {
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
      ats_breakdown: { raw_overlap: 1, selected_resume_file_name: 'JavaVariant_ARPPSOPP.docx' },
      semantic_input_source: null,
      semantic_input_chars: null,
      semantic_chunks: null,
      semantic_fallback_reason: null,
      keyword_source: null,
      thread_snapshot_used: null,
      thread_snapshot_email_id: null,
      skip_reason: null,
      sync_batch_id: null,
      draft_reply: 'Draft body',
      draft_source: 'rules_only',
      draft_model: null,
      draft_ai_error: null,
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
      resume_file_name: 'JavaVariant_ARPPSOPP.docx',
      parser_details: { parser_version: 'ai_parser_modes_v1', merged_result: { role: 'Java Developer', skills_text: 'Java, Spring Boot' } },
      attachment_file_names: [],
      sent_at: null,
      gmail_sent_id: null,
      last_error: null,
      created_at: '2026-06-24T00:00:00Z',
      updated_at: '2026-06-24T00:00:00Z',
    }

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/gmail/status')) return makeResponse({ configured: true, authenticated: true, token_path: 'token.json', last_sync_at: null, detail: 'ok' })
        if (url.endsWith('/ai/status')) return makeResponse({ configured: true, connected: true, running: false, provider: 'mock', model: 'mock', detail: 'ok', last_error: null, last_started_at: null, last_finished_at: null, last_duration_ms: null, last_draft_source: null })
        if (url.endsWith('/telegram/status')) return makeResponse({ enabled: false, polling: false, alerts_enabled: false, authorized_chats: 0, detail: 'off' })
        if (url.endsWith('/gmail/oauth/url')) return makeResponse({ authorization_url: null })
        if (url.endsWith('/settings')) {
          return makeResponse({
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
            resume_display_name: '',
            policy: null,
          })
        }
        if (url.endsWith('/settings/resumes')) return makeResponse([])
        if (url.endsWith('/settings/attachments')) return makeResponse([])
        if (url.endsWith('/settings/skills/pending')) return makeResponse([])
        if (url.includes('/candidates?')) {
          if (url.includes('state=needs_review')) return makeResponse({ items: [candidate], next_cursor: null, has_next: false })
          return makeResponse({ items: [], next_cursor: null, has_next: false })
        }
        if (url.includes('/analytics/events?')) return makeResponse([])
        if (url.includes('/analytics/trend?')) return makeResponse({ range: '7d', bucket: 'day', trend_direction: 'flat', trend_delta_pct: 0, kpi_total_sent: 0, previous_period_total_sent: 0, bars: [] })
        if (url.includes('/analytics/events/view')) return makeResponse({ ok: true })
        if (url.includes('/number-review')) return makeResponse({ items: [], next_cursor: null, has_next: false })
        if (url.includes('/recruiter-numbers')) return makeResponse({ items: [], next_cursor: null, has_next: false })
        if (url.includes('/employer-numbers')) return makeResponse({ items: [], next_cursor: null, has_next: false })
        if (url.includes('/recruiter-opportunities')) return makeResponse({ items: [], next_cursor: null, has_next: false })
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
    })

    expect(container.textContent ?? '').toContain('ATS Score:')
    expect(container.textContent ?? '').toContain('84')
    expect(container.textContent ?? '').toContain('Strong')
    expect(container.textContent ?? '').toContain('Resume:')
    expect(container.textContent ?? '').toContain('JavaVariant_ARPPSOPP.docx')
  })
})
