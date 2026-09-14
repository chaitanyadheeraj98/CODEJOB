// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function response(payload: unknown) {
  return { ok: true, json: async () => payload }
}

function settings() {
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
    feature_groq_job_parser_enabled: false,
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

describe('Telegram integration settings', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
  })

  async function render(link: Record<string, unknown>) {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/gmail/status')) return response({ configured: true, authenticated: true, token_path: 'owner@example.com', last_sync_at: null })
      if (url.endsWith('/ai/status')) return response({ connected: false, provider: 'deepseek', model: 'deepseek-chat' })
      if (url.endsWith('/chat/status')) return response({ enabled: false, ollama_running: false, available_models: [], model: '' })
      if (url.endsWith('/telegram/status')) return response({ enabled: true, polling: true, alerts_enabled: true, authorized_chats: link.linked ? 1 : 0, detail: 'polling' })
      if (url.endsWith('/telegram/link/code') && init?.method === 'POST') return response({ deep_link: 'https://t.me/codejob_bot?start=abc_123', expires_at: new Date(Date.now() + 600000).toISOString() })
      if (url.endsWith('/telegram/link')) return response(link)
      if (url.endsWith('/gmail/oauth/url')) return response({ authorization_url: null })
      if (url.includes('/settings/bootstrap')) return response({ settings: settings(), resumes: [], attachments: [], pending_skills: [], pending_job_intent_signals: [], approved_job_intent_signals: [], loaded_at: '2026-01-01T00:00:00Z', owner_id: 'owner' })
      if (url.includes('/recent-runs')) return response({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/analytics/trend')) return response({ range: 'current_day', bucket: 'hour', trend_direction: 'flat', trend_delta_pct: 0, kpi_total_sent: 0, previous_period_total_sent: 0, bars: [] })
      if (url.includes('/analytics/events')) return response([])
      if (url.includes('/candidates?')) return response({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/number-review') || url.includes('/recruiter-opportunities')) return response({ items: [], next_cursor: null, has_next: false })
      throw new Error(`Unhandled fetch: ${url}`)
    }))

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })
    await act(async () => {
      root.render(<App />)
      await new Promise((resolve) => window.setTimeout(resolve, 50))
    })
    const settingsButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Settings')
    await act(async () => {
      settingsButton?.click()
      await Promise.resolve()
    })
    return container
  }

  it('shows the renamed panel and link action while unlinked', async () => {
    const container = await render({ linked: false, chat_masked: null, telegram_username: '', linked_at: null, alerts_enabled: true, pin_set: false, bot_username: 'codejob_bot', pending_code_expires_at: null })
    const panel = Array.from(container.querySelectorAll('section')).find((item) => item.querySelector('h2')?.textContent === 'Integrations')
    expect(panel?.textContent).toContain('Link Telegram')
    expect(panel?.textContent).not.toContain('4821')
  })

  it('shows masked chat identity and unlink action while linked', async () => {
    const container = await render({ linked: true, chat_masked: '…4821', telegram_username: 'alice', linked_at: '2026-01-01T00:00:00Z', alerts_enabled: true, pin_set: false, bot_username: 'codejob_bot', pending_code_expires_at: null })
    const panel = Array.from(container.querySelectorAll('section')).find((item) => item.querySelector('h2')?.textContent === 'Integrations')
    expect(panel?.textContent).toContain('@alice · chat …4821')
    expect(panel?.textContent).toContain('Unlink')
  })

  it('renders the minted deep link as a Telegram href', async () => {
    const container = await render({ linked: false, chat_masked: null, telegram_username: '', linked_at: null, alerts_enabled: true, pin_set: false, bot_username: 'codejob_bot', pending_code_expires_at: null })
    const linkButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Link Telegram')
    await act(async () => {
      linkButton?.click()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(container.querySelector<HTMLAnchorElement>('a[href^="https://t.me/"]')?.href).toBe('https://t.me/codejob_bot?start=abc_123')
  })
})
