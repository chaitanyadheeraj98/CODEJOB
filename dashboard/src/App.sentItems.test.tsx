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

function stubAppFetch(candidate: Record<string, unknown>, sentDetails: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/gmail/status')) return makeResponse({ configured: true, authenticated: true, token_path: 'token.json', last_sync_at: null, detail: 'ok' })
      if (url.endsWith('/ai/status')) return makeResponse({ configured: true, connected: true, running: false, provider: 'mock', model: 'mock', detail: 'ok', last_error: null, last_started_at: null, last_finished_at: null, last_duration_ms: null, last_draft_source: null })
      if (url.endsWith('/telegram/status')) return makeResponse({ enabled: false, polling: false, alerts_enabled: false, authorized_chats: 0, detail: 'off' })
      if (url.endsWith('/gmail/oauth/url')) return makeResponse({ authorization_url: null })
      if (url.includes('/settings/bootstrap')) return makeResponse(makeBootstrapPayload())
      if (url.includes('/recent-runs/')) return makeResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/recent-runs')) return makeResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/candidates/99/sent-details')) return makeResponse(sentDetails)
      if (url.includes('/candidates?')) {
        if (url.includes('state=approved_sent')) return makeResponse({ items: [candidate], next_cursor: null, has_next: false })
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
}

describe('Sent Items audit view', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
  })

  it('expands a Gmail sent item and renders clickable audit links with missing fields as dashes', async () => {
    stubAppFetch(
      {
        id: 99,
        record_id: 'record-sent-99',
        owner_id: 'default-owner',
        sender: 'Recruiter <recruiter@example.com>',
        subject: 'Java Developer',
        body: 'Need Java',
        role: 'Java Developer',
        location: 'Remote',
        salary_text: '',
        skills_text: 'Java, Spring Boot',
        score: 82,
        decision: 'Qualified',
        state: 'approved_sent',
        decision_reason: 'sent',
        hard_filter_result: null,
        auto_reject_reason: null,
        ai_score: 0.82,
        ai_score_source: 'v1_rules_plus_ai',
        ai_summary: 'summary',
        ats_score: 84,
        ats_score_source: 'hybrid_structured_only',
        ats_summary: 'ATS hybrid score 84/100',
        ats_breakdown: { raw_overlap: 1, selected_resume_file_name: 'resume.pdf' },
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
        approval_status: 'approved',
        sent_status: 'sent',
        source: 'gmail',
        external_message_id: 'msg-1',
        external_thread_id: 'thread-1',
        external_rfc_message_id: null,
        gmail_received_at: null,
        applied_gmail_label: null,
        applied_gmail_label_id: null,
        applied_gmail_label_at: null,
        gmail_message_url: 'https://mail.google.com/mail/u/0/#all/msg-1',
        recipient_email: 'recruiter@example.com',
        cc_email: 'manager@example.com',
        routing_status: 'safe',
        routing_confidence: 0.92,
        routing_reason: 'safe route',
        routing_evidence: [],
        routing_candidates: [],
        routing_confirmed: true,
        resume_asset_id: 7,
        resume_file_name: 'resume.pdf',
        parser_details: { parser_version: 'ai_parser_modes_v1', merged_result: { role: 'Java Developer', skills_text: 'Java, Spring Boot' } },
        attachment_file_names: [],
        sent_at: '2026-06-27T18:00:00Z',
        gmail_sent_id: 'sent-1',
        last_error: null,
        created_at: '2026-06-27T17:00:00Z',
        updated_at: '2026-06-27T18:00:00Z',
      },
      {
        email_id: 99,
        source_type: 'gmail',
        source_label: 'Gmail',
        requirement_received_link: 'https://mail.google.com/mail/u/0/#all/msg-1',
        sent_gmail_message_link: 'https://mail.google.com/mail/u/0/#all/sent-1',
        resume_variant_sent: 'resume.pdf',
        attached_files: [],
        company: null,
        recruiter_name: 'Recruiter',
        recruiter_email: 'recruiter@example.com',
        recruiter_phone: null,
        end_client: null,
        implementation_partner: null,
        vendor: null,
        domain_mentioned: null,
        experience_required: null,
        mandatory_skills: ['Java', 'Spring Boot'],
        missing_skills: [],
        ats_score: 84,
        ats_summary: 'ATS hybrid score 84/100',
        to_email: 'recruiter@example.com',
        cc_email: 'manager@example.com',
        sent_at: '2026-06-27T18:00:00Z',
      },
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

    const sentItemsButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Sent Items'),
    ) as HTMLButtonElement | undefined
    expect(sentItemsButton).toBeDefined()

    await act(async () => {
      sentItemsButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })

    expect(container.textContent ?? '').toContain('Record ID:')
    expect(container.textContent ?? '').toContain('record-sent-99')

    const viewDetailsButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent === 'View Details',
    ) as HTMLButtonElement | undefined
    expect(viewDetailsButton).toBeDefined()

    await act(async () => {
      viewDetailsButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(container.textContent ?? '').toContain('Resume / Send Audit')
    expect(container.textContent ?? '').toContain('Company: -')
    expect(container.textContent ?? '').toContain('Mandatory Skills: Java, Spring Boot')
    expect(container.textContent ?? '').toContain('Attached Files: -')

    const links = Array.from(container.querySelectorAll('a')).map((link) => ({
      text: link.textContent,
      href: link.getAttribute('href'),
    }))
    expect(links).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ text: 'Open requirement', href: 'https://mail.google.com/mail/u/0/#all/msg-1' }),
        expect.objectContaining({ text: 'Open original email', href: 'https://mail.google.com/mail/u/0/#all/msg-1' }),
        expect.objectContaining({ text: 'Open sent message', href: 'https://mail.google.com/mail/u/0/#all/sent-1' }),
      ]),
    )
  })
})
