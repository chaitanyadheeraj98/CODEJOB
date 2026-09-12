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

  async function renderWithAiStatus(aiStatus: unknown, options?: {
    chatStatus?: unknown
    onOllamaSave?: (body: Record<string, unknown>) => void
  }) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/gmail/status')) return makeResponse({ configured: true, authenticated: true, token_path: 'token.json', last_sync_at: null, detail: 'ok' })
        if (url.endsWith('/ai/status')) return makeResponse(aiStatus)
        if (url.endsWith('/chat/status')) return makeResponse(options?.chatStatus ?? { enabled: false, ollama_running: false, available_models: [], model: '' })
        if (url.endsWith('/chat/credentials/ollama') && init?.method === 'PUT') {
          options?.onOllamaSave?.(JSON.parse(String(init.body)) as Record<string, unknown>)
          return makeResponse({ provider: 'ollama', configured: true, masked_api_key: 'sk-…7890', base_url: 'https://ollama.example' })
        }
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

  it('renders provider-neutral intent gate status alongside the Groq rows', async () => {
    // The gate can run on a provider that is not Groq, so the card has to say which
    // one answered. Reading DeepSeek's health off a row labelled "Groq Runtime" is
    // exactly the misreport this pair of rows exists to prevent.
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
      groq_runtime_healthy: null,
      groq_last_error: null,
      groq_detail: 'Groq is idle: the intent gate is running on deepseek.',
      groq_last_attempted_at: null,
      groq_last_success_at: null,
      groq_last_duration_ms: null,
      intent_gate_provider: 'deepseek',
      intent_gate_model: 'deepseek-v4-flash',
      intent_gate_configured: true,
      intent_gate_enabled_in_settings: true,
      intent_gate_runtime_healthy: true,
      intent_gate_last_error: null,
      intent_gate_detail: 'Intent gate healthy on deepseek (deepseek-v4-flash).',
      intent_gate_effort_ladder: 'disabled',
      intent_gate_last_rung: 'disabled',
      intent_gate_min_taxonomy_confidence: 0,
      intent_gate_last_attempted_at: null,
      intent_gate_last_success_at: null,
      intent_gate_last_duration_ms: 1200,
      last_error: null,
      last_started_at: null,
      last_finished_at: null,
      last_duration_ms: null,
      last_draft_source: null,
    })

    expect(text).toContain('Intent Gate Provider')
    expect(text).toContain('deepseek-v4-flash')
    expect(text).toContain('Intent Gate Runtime')
    expect(text).toContain('Intent Gate Duration')
    expect(text).toContain('1.2s')
    expect(text).toContain('Groq is idle: the intent gate is running on deepseek.')
  })

  it('validates and replaces an Ollama key without rendering the secret', async () => {
    let savedBody: Record<string, unknown> | null = null
    const { container } = await renderWithAiStatus({
      configured: true,
      connected: true,
      running: false,
      provider: 'deepseek',
      model: 'deepseek-chat',
      detail: 'ok',
      last_error: null,
      last_started_at: null,
      last_finished_at: null,
      last_duration_ms: null,
      last_draft_source: null,
    }, {
      chatStatus: {
        enabled: true,
        ollama_running: true,
        ollama_configured: true,
        ollama_masked_api_key: 'sk-…1234',
        ollama_base_url: 'https://ollama.example',
        ollama_last_error: null,
        ollama_last_success_at: null,
        available_models: [],
        model: 'test',
        mcp_status: 'connected',
      },
      onOllamaSave: (body) => { savedBody = body },
    })

    const section = Array.from(container.querySelectorAll('section')).find(
      (candidate) => candidate.querySelector('h2')?.textContent === 'Ollama Access',
    )
    const keyInput = section?.querySelector<HTMLInputElement>('input[type="password"]')
    const baseUrlInput = section?.querySelector<HTMLInputElement>('input[type="url"]')
    expect(section?.textContent).toContain('sk-…1234')
    expect(keyInput?.value).toBe('')
    expect(baseUrlInput?.value).toBe('https://ollama.example')

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      setter?.call(keyInput, 'never-render-this-secret')
      keyInput?.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const saveButton = Array.from(section?.querySelectorAll('button') ?? []).find(
      (button) => button.textContent === 'Validate and save',
    )
    await act(async () => {
      saveButton?.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(savedBody).toEqual({
      api_key: 'never-render-this-secret',
      base_url: 'https://ollama.example',
    })
    expect(keyInput?.value).toBe('')
    expect(section?.textContent).toContain('Validated and saved sk-…7890.')
    expect(section?.textContent).not.toContain('never-render-this-secret')
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
