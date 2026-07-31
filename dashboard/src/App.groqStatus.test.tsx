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

function makeSettings() {
  return {
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
    feature_groq_job_parser_enabled: true,
    draft_text_size: 'normal',
    fallback_draft_template: '',
    signature_name: '',
    signature_phone: '',
    signature_email: '',
    preferred_employer_cc_email: '',
    resume_display_name: '',
    policy: null,
  }
}

function makeBootstrapPayload() {
  return {
    settings: makeSettings(),
    resumes: [],
    attachments: [],
    pending_skills: [],
    pending_job_intent_signals: [],
    approved_job_intent_signals: [],
    loaded_at: '2026-07-09T00:00:00Z',
    owner_id: 'default-owner',
  }
}

describe('Groq status UI', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
  })

  async function renderWithAiStatus(aiStatus: unknown) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/gmail/status')) return makeResponse({ configured: true, authenticated: true, token_path: 'token.json', last_sync_at: null, detail: 'ok' })
        if (url.endsWith('/ai/status')) return makeResponse(aiStatus)
        if (url.endsWith('/telegram/status')) return makeResponse({ enabled: false, polling: false, alerts_enabled: false, authorized_chats: 0, detail: 'off' })
        if (url.endsWith('/gmail/oauth/url')) return makeResponse({ authorization_url: null })
        if (url.includes('/settings/bootstrap')) return makeResponse(makeBootstrapPayload())
        if (url.includes('/recent-runs')) return makeResponse({ items: [], next_cursor: null, has_next: false })
        if (url.includes('/analytics/trend')) return makeResponse({ range: 'current_day', bucket: 'hour', trend_direction: 'flat', trend_delta_pct: 0, kpi_total_sent: 0, previous_period_total_sent: 0, bars: [] })
        if (url.includes('/analytics/events')) return makeResponse([])
        if (url.includes('/candidates?state=needs_review')) return makeResponse({ items: [], next_cursor: null, has_next: false })
        if (url.includes('/candidates?state=failed')) return makeResponse({ items: [], next_cursor: null, has_next: false })
        if (url.includes('/candidates?state=approved_sent')) return makeResponse({ items: [], next_cursor: null, has_next: false })
        if (url.includes('/number-review')) return makeResponse({ items: [], next_cursor: null, has_next: false })
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
      await new Promise((resolve) => window.setTimeout(resolve, 50))
    })
    const settingsButton = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent === 'Settings',
    )
    await act(async () => {
      settingsButton?.click()
      await Promise.resolve()
    })

    return {
      container,
      text: container.textContent ?? '',
    }
  }

  it('renders groq fallback status in the AI Access card', async () => {
    const { text } = await renderWithAiStatus({
      configured: true,
      connected: true,
      running: false,
      provider: 'deepseek',
      model: 'deepseek-chat',
      detail: 'ok',
      groq_configured: true,
      groq_enabled_in_settings: true,
      groq_model: 'llama-3.1-8b-instant',
      groq_base_url_present: true,
      groq_request_mode: 'json_object',
      groq_runtime_healthy: false,
      groq_last_error: 'missing_groq_api_key',
      groq_detail: 'Groq fallback active due to recent runtime failure: missing_groq_api_key.',
      groq_last_attempted_at: null,
      groq_last_success_at: null,
      groq_last_duration_ms: 250,
      last_error: null,
      last_started_at: null,
      last_finished_at: null,
      last_duration_ms: null,
      last_draft_source: null,
    })

    expect(text).toContain('Groq Enabled')
    expect(text).toContain('On')
    expect(text).toContain('Groq Config')
    expect(text).toContain('Configured')
    expect(text).toContain('Groq Request Mode')
    expect(text).toContain('json_object')
    expect(text).toContain('Groq Runtime')
    expect(text).toContain('Fallback')
    expect(text).toContain('missing_groq_api_key')
  })

  it('applies the standardized run queue grid layout hook', async () => {
    const { container } = await renderWithAiStatus({
      configured: true,
      connected: true,
      running: false,
      provider: 'deepseek',
      model: 'deepseek-chat',
      detail: 'ok',
      groq_configured: true,
      groq_enabled_in_settings: true,
      groq_model: 'llama-3.1-8b-instant',
      groq_base_url_present: true,
      groq_request_mode: 'json_object',
      groq_runtime_healthy: true,
      groq_last_error: null,
      groq_detail: 'Groq runtime healthy.',
      groq_last_attempted_at: null,
      groq_last_success_at: null,
      groq_last_duration_ms: 250,
      last_error: null,
      last_started_at: null,
      last_finished_at: null,
      last_duration_ms: null,
      last_draft_source: null,
    })

    const runQueueGrid = container.querySelector('form.configGrid.runQueueGrid')
    expect(runQueueGrid).not.toBeNull()
  })

})
