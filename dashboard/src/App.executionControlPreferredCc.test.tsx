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

describe('Execution Control preferred employer CC', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
  })

  it('loads and saves the preferred employer CC setting from Execution Control', async () => {
    const putBodies: unknown[] = []

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
            preferred_employer_cc_email: 'sheshwika@horizonsoftech.net',
            resume_display_name: '',
            policy: null,
          })
        }
        if (url.endsWith('/settings') && init?.method === 'PUT') {
          putBodies.push(JSON.parse(String(init.body)))
          return makeResponse(JSON.parse(String(init.body)))
        }
        if (url.endsWith('/settings/resumes')) return makeResponse([])
        if (url.endsWith('/settings/attachments')) return makeResponse([])
        if (url.endsWith('/settings/skills/pending')) return makeResponse([])
        if (url.endsWith('/settings/job-intent-learning/pending')) return makeResponse([])
        if (url.endsWith('/settings/job-intent-learning/approved')) return makeResponse([])
        if (url.includes('/recent-runs/')) return makeResponse({ items: [], next_cursor: null, has_next: false })
        if (url.includes('/recent-runs')) return makeResponse({ items: [], next_cursor: null, has_next: false })
        if (url.includes('/candidates?')) return makeResponse({ items: [], next_cursor: null, has_next: false })
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

    const settingsButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Settings'),
    ) as HTMLButtonElement | undefined
    expect(settingsButton).toBeDefined()

    await act(async () => {
      settingsButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })

    const input = Array.from(container.querySelectorAll('input')).find(
      (element) => (element as HTMLInputElement).placeholder === 'sheshwika@horizonsoftech.net',
    ) as HTMLInputElement | undefined
    expect(input?.value).toBe('sheshwika@horizonsoftech.net')
    expect(container.textContent ?? '').toContain('Used as the employer CC for Nvoids/external-feed drafts.')

    await act(async () => {
      const setNativeValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      setNativeValue?.call(input, 'ops@horizonsoftech.net')
      input!.dispatchEvent(new Event('input', { bubbles: true }))
      input!.dispatchEvent(new Event('change', { bubbles: true }))
    })

    const saveButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Save Filters'),
    ) as HTMLButtonElement | undefined
    expect(saveButton).toBeDefined()

    await act(async () => {
      saveButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(putBodies).toHaveLength(1)
    expect((putBodies[0] as { preferred_employer_cc_email?: string }).preferred_employer_cc_email).toBe('ops@horizonsoftech.net')
  })
})
