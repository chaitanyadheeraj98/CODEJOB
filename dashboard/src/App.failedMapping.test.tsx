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

describe('Failed Mapping delete flow', () => {
  const cleanups: Array<() => void> = []

  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('renders Delete and removes a failed card after confirmation', async () => {
    let failedItems = [
      {
        id: 101,
        owner_id: 'default-owner',
        sender: 'Recruiter <recruiter@example.com>',
        subject: 'Needs routing help',
        body: 'Body with recruiter@example.com only',
        role: 'Java Developer',
        location: 'Remote',
        salary_text: '',
        skills_text: 'Java, Spring',
        score: 80,
        decision: 'Qualified',
        state: 'failed',
        decision_reason: 'Recipient routing unresolved',
        hard_filter_result: null,
        auto_reject_reason: null,
        ai_score: 0.8,
        ai_score_source: 'v1',
        ai_summary: 'summary',
        ats_score: null,
        ats_score_source: null,
        ats_summary: null,
        ats_breakdown: null,
        semantic_input_source: null,
        semantic_input_chars: null,
        semantic_chunks: null,
        semantic_fallback_reason: null,
        keyword_source: null,
        thread_snapshot_used: null,
        thread_snapshot_email_id: null,
        skip_reason: 'missing_to_or_cc',
        sync_batch_id: null,
        draft_reply: 'Draft body',
        draft_source: 'rules_only',
        draft_model: null,
        draft_ai_error: null,
        draft_resume_context_status: 'rules_only',
        draft_quality: null,
        approval_status: 'pending',
        sent_status: 'not_sent',
        source: 'gmail',
        external_message_id: 'msg-101',
        external_thread_id: 'thread-101',
        external_rfc_message_id: null,
        gmail_received_at: null,
        applied_gmail_label: null,
        applied_gmail_label_id: null,
        applied_gmail_label_at: null,
        gmail_message_url: 'https://mail.google.com/mail/u/0/#all/msg-101',
        recipient_email: 'recruiter@example.com',
        cc_email: null,
        routing_status: 'ambiguous',
        routing_confidence: 0.45,
        routing_reason: 'Only one recipient side could be resolved.',
        routing_evidence: [
          { role: 'to', email: 'recruiter@example.com', source: 'Sender Header', detail: 'TO from Sender Header' },
        ],
        routing_candidates: [
          { role: 'to', email: 'recruiter@example.com', source: 'Sender Header', detail: 'TO from Sender Header' },
        ],
        routing_confirmed: false,
        resume_asset_id: null,
        resume_file_name: null,
        parser_details: null,
        attachment_file_names: [],
        sent_at: null,
        gmail_sent_id: null,
        last_error: 'Could not resolve recruiter To and employer CC',
        created_at: '2026-06-28T10:00:00Z',
        updated_at: '2026-06-28T10:00:00Z',
      },
    ]

    vi.stubGlobal('confirm', vi.fn(() => true))

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/gmail/status')) return makeResponse({ configured: true, authenticated: true, token_path: 'token.json', last_sync_at: null, detail: 'ok' })
        if (url.endsWith('/ai/status')) return makeResponse({ configured: true, connected: true, running: false, provider: 'mock', model: 'mock', detail: 'ok', last_error: null, last_started_at: null, last_finished_at: null, last_duration_ms: null, last_draft_source: null })
        if (url.endsWith('/telegram/status')) return makeResponse({ enabled: false, polling: false, alerts_enabled: false, authorized_chats: 0, detail: 'off' })
        if (url.endsWith('/gmail/oauth/url')) return makeResponse({ authorization_url: null })
        if (url.endsWith('/settings') && (!init || init.method === undefined)) {
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
            preferred_employer_cc_email: '',
            resume_display_name: '',
            policy: null,
          })
        }
        if (url.endsWith('/settings/resumes')) return makeResponse([])
        if (url.endsWith('/settings/attachments')) return makeResponse([])
        if (url.endsWith('/settings/skills/pending')) return makeResponse([])
        if (url.endsWith('/settings/job-intent-learning/pending')) return makeResponse([])
        if (url.endsWith('/settings/job-intent-learning/approved')) return makeResponse([])
        if (url.includes('/recent-runs/')) return makeResponse({ items: [], next_cursor: null, has_next: false })
        if (url.includes('/recent-runs')) return makeResponse({ items: [], next_cursor: null, has_next: false })
        if (url.includes('/candidates?')) {
          if (url.includes('state=failed')) return makeResponse({ items: failedItems, next_cursor: null, has_next: false })
          return makeResponse({ items: [], next_cursor: null, has_next: false })
        }
        if (url.endsWith('/candidates/101') && init?.method === 'DELETE') {
          failedItems = []
          return makeResponse({ id: 101, deleted: true, state: 'dismissed' })
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

    const failedButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Failed Mapping'),
    ) as HTMLButtonElement | undefined
    expect(failedButton).toBeDefined()

    await act(async () => {
      failedButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })

    expect(container.textContent ?? '').toContain('Failed Recipient Mapping (Teach the model)')
    expect(container.textContent ?? '').toContain('Delete')

    const deleteButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Delete'),
    ) as HTMLButtonElement | undefined
    expect(deleteButton).toBeDefined()

    await act(async () => {
      deleteButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })

    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(container.textContent ?? '').toContain('No failed emails.')
    expect(globalThis.confirm).toHaveBeenCalled()
  })
})
