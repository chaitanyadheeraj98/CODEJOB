// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { formatRelativeInboxTime, getInitials } from './inboxFormat'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type MockResponse = {
  ok: boolean
  json: () => Promise<unknown>
}

function makeResponse(payload: unknown): MockResponse {
  return { ok: true, json: async () => payload }
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
      feature_reply_inbox_enabled: true,
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
    loaded_at: '2026-08-05T12:00:00Z',
    owner_id: 'default-owner',
  }
}

async function flushPromises(iterations = 6): Promise<void> {
  for (let index = 0; index < iterations; index += 1) await Promise.resolve()
}

describe('Inbox presentation helpers', () => {
  it('formats inbox timestamps and sender initials without a date dependency', () => {
    const now = new Date(2026, 7, 5, 12)

    expect(formatRelativeInboxTime(new Date(2026, 7, 5, 8, 13).toISOString(), now)).toBe('8:13 AM')
    expect(formatRelativeInboxTime(new Date(2026, 7, 4, 20, 0).toISOString(), now)).toBe('Yesterday')
    expect(formatRelativeInboxTime(new Date(2026, 6, 20, 12, 0).toISOString(), now)).toBe('Jul 20')
    expect(formatRelativeInboxTime(new Date(2025, 7, 4, 12, 0).toISOString(), now)).toBe('Aug 4, 2025')
    expect(getInitials('Sunitha Sanu')).toBe('SS')
    expect(getInitials('You')).toBe('Y')
  })
})

describe('Inbox dashboard', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
  })

  it('renders unread, refresh, thread, toolbar, and unchanged reply states accessibly', async () => {
    const conversations = [
      {
        id: 1,
        root_recruiter_email_id: 10,
        recruiter: 'Sunitha Sanu',
        recruiter_email: 'sunitha@example.com',
        subject: 'Lead Data Engineer',
        status: 'sent',
        last_message_preview: 'The role is still available.',
        last_message_at: '2026-08-05T13:13:00Z',
        unread_reply_count: 0,
      },
      {
        id: 2,
        root_recruiter_email_id: 11,
        recruiter: 'Kim Lee',
        recruiter_email: 'kim@example.com',
        subject: 'Platform Engineer',
        status: 'replied',
        last_message_preview: 'Could you talk tomorrow?',
        last_message_at: '2026-08-04T18:00:00Z',
        unread_reply_count: 2,
      },
    ]
    const detail = {
      ...conversations[0],
      to_email: 'sunitha@example.com',
      cc_email: null,
      messages: [
        {
          id: 100,
          direction: 'inbound',
          sender: 'Sunitha Sanu',
          body: 'The role is still available.',
          snippet: 'The role is still available.',
          occurred_at: '2026-08-05T13:13:00Z',
          read_at: '2026-08-05T13:15:00Z',
        },
        {
          id: 101,
          direction: 'outbound',
          sender: 'me',
          body: 'Thanks for the update.',
          snippet: 'Thanks for the update.',
          occurred_at: '2026-08-05T13:20:00Z',
          read_at: '2026-08-05T13:20:00Z',
        },
      ],
    }

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/gmail/status')) return makeResponse({ configured: true, authenticated: true, token_path: 'token.json', last_sync_at: null, detail: 'ok' })
      if (url.endsWith('/ai/status')) return makeResponse({ configured: true, connected: true, running: false, provider: 'mock', model: 'mock', detail: 'ok', last_error: null, last_started_at: null, last_finished_at: null, last_duration_ms: null, last_draft_source: null })
      if (url.endsWith('/telegram/status')) return makeResponse({ enabled: false, polling: false, alerts_enabled: false, authorized_chats: 0, detail: 'off' })
      if (url.endsWith('/gmail/oauth/url')) return makeResponse({ authorization_url: null })
      if (url.includes('/settings/bootstrap')) return makeResponse(makeBootstrapPayload())
      if (url.includes('/recent-runs')) return makeResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/candidates?')) return makeResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/analytics/events?')) return makeResponse([])
      if (url.includes('/analytics/trend?')) return makeResponse({ range: '7d', bucket: 'day', trend_direction: 'flat', trend_delta_pct: 0, kpi_total_sent: 0, previous_period_total_sent: 0, bars: [] })
      if (url.includes('/number-review')) return makeResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/recruiter-numbers')) return makeResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/employer-numbers')) return makeResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/recruiter-opportunities')) return makeResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/filter-options?')) return makeResponse({ values: ['RTR Requested'] })
      if (url.includes('/inbox/conversations?')) return makeResponse(conversations)
      if (url.endsWith('/inbox/conversations/1')) return makeResponse(detail)
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
      await flushPromises()
    })

    const inboxButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.startsWith('Inbox'),
    )
    await act(async () => {
      inboxButton?.click()
      await flushPromises(10)
    })

    const refreshButton = container.querySelector<HTMLButtonElement>('button[aria-label="Refresh conversations"]')
    expect(refreshButton?.title).toBe('Refresh')
    expect(container.querySelectorAll('.filterSortBar input[role="combobox"]')).toHaveLength(5)

    const unreadRow = container.querySelector<HTMLButtonElement>('.conversationListItem.unread')
    expect(unreadRow?.querySelector('.unreadDot')).not.toBeNull()
    expect(unreadRow?.getAttribute('aria-label')).toBeNull()
    expect(unreadRow?.querySelector('.visuallyHidden')?.textContent).toBe('Unread. ')
    expect(unreadRow?.textContent).toContain('Unread. ')
    expect(unreadRow?.textContent).toContain('Kim Lee')
    expect(unreadRow?.textContent).toContain('Platform Engineer')
    expect(unreadRow?.title).not.toBe('')

    expect(container.querySelector('.conversationStatusBadge')?.textContent).toBe('sent')
    expect(Array.from(container.querySelectorAll('.conversationAvatar')).map((avatar) => avatar.textContent)).toEqual(['SS', 'Y'])

    const toolButtons = Array.from(container.querySelectorAll<HTMLButtonElement>('.composerToolBtn'))
    expect(toolButtons.map((button) => button.getAttribute('aria-label'))).toEqual(['Bold', 'Italic', 'Attach file', 'Insert emoji'])
    expect(toolButtons.every((button) => button.disabled && button.getAttribute('aria-disabled') === 'true')).toBe(true)

    const sendButton = container.querySelector<HTMLButtonElement>('.composerSendBtn')
    expect(sendButton?.disabled).toBe(true)
    const textarea = container.querySelector<HTMLTextAreaElement>('.inboxComposer textarea')
    await act(async () => {
      if (textarea) {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
        setter?.call(textarea, 'Thanks!')
        textarea.dispatchEvent(new Event('input', { bubbles: true }))
      }
    })
    expect(sendButton?.disabled).toBe(false)
    const labelInput = Array.from(container.querySelectorAll<HTMLInputElement>('input[role="combobox"]')).find((input) => input.closest('label')?.textContent?.includes('Gmail label'))!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(labelInput, 'RTR Requested')
      labelInput.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      labelInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      await flushPromises(10)
    })
    const unread = Array.from(container.querySelectorAll<HTMLSelectElement>('select')).find((select) => select.closest('label')?.textContent?.includes('Unread only'))!
    await act(async () => {
      unread.value = 'yes'
      unread.dispatchEvent(new Event('change', { bubbles: true }))
      await flushPromises(10)
    })
    const requests = vi.mocked(fetch).mock.calls.map(([url]) => new URL(String(url)))
    expect(requests.some((url) => url.pathname === '/inbox/conversations' && url.searchParams.get('label') === 'RTR Requested' && url.searchParams.get('unread_only') === 'true')).toBe(true)

  })
})
