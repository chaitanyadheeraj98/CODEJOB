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

describe('Recent Runs skipped-item drill-down', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
  })

  it('loads persisted recent runs and expands skipped Gmail and Nvoids items with source links', async () => {
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
        if (url.includes('/recent-runs/gmail_sync%3Abatch-1/items')) {
          return makeResponse({
            items: [
              {
                id: 11,
                run_key: 'gmail_sync:batch-1',
                run_source: 'gmail_sync',
                source_type: 'gmail',
                outcome: 'skipped',
                reason_code: 'duplicate_existing_email',
                reason_detail: 'Skipped because this Gmail message already exists in the candidate database.',
                title_or_subject: 'Java role',
                sender: 'Recruiter <r@example.com>',
                gmail_message_url: 'https://mail.google.com/mail/u/0/#all/msg-1',
                source_url: null,
                created_at: '2026-06-30T20:00:00Z',
              },
            ],
            next_cursor: null,
            has_next: false,
          })
        }
        if (url.includes('/recent-runs/nvoids_sync%3A77/items')) {
          return makeResponse({
            items: [
              {
                id: 22,
                run_key: 'nvoids_sync:77',
                run_source: 'nvoids_sync',
                source_type: 'nvoids',
                outcome: 'skipped',
                reason_code: 'skipped_location',
                reason_detail: "Skipped because listing location 'Dallas, Texas, USA' did not match the saved Nvoids location filters.",
                title_or_subject: 'Senior Python Developer',
                sender: 'Nvoids',
                source_url: 'https://www.nvoids.com/job1.jsp?id=1',
                gmail_message_url: null,
                created_at: '2026-06-30T20:01:00Z',
              },
            ],
            next_cursor: null,
            has_next: false,
          })
        }
        if (url.includes('/recent-runs')) {
          return makeResponse({
            items: [
              {
                run_key: 'gmail_sync:batch-1',
                run_source: 'gmail_sync',
                status: 'skipped',
                detail: 'Processed 1 unread matching emails: queued=0, skipped=1, failed=0.',
                matched_count: 1,
                queued_count: 0,
                skipped_count: 1,
                failed_count: 0,
                skipped_item_count: 1,
                created_at: '2026-06-30T20:00:00Z',
              },
              {
                run_key: 'nvoids_sync:77',
                run_source: 'nvoids_sync',
                status: 'ok',
                detail: 'nvoids sync complete: fetched=2 created=1 deduped=0 skipped_location=1 failed=0',
                matched_count: null,
                queued_count: null,
                skipped_count: 1,
                failed_count: 0,
                skipped_item_count: 1,
                created_at: '2026-06-30T20:01:00Z',
              },
            ],
            next_cursor: null,
            has_next: false,
          })
        }
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

    const recentRunsButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Recent Runs'),
    ) as HTMLButtonElement | undefined
    expect(recentRunsButton).toBeDefined()

    await act(async () => {
      recentRunsButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })

    expect(container.textContent ?? '').toContain('Run Source:')
    expect(container.textContent ?? '').toContain('gmail_sync')
    expect(container.textContent ?? '').toContain('nvoids_sync')

    const skippedButtons = Array.from(container.querySelectorAll('button')).filter((button) =>
      button.textContent?.includes('Skipped Items (1)'),
    )
    expect(skippedButtons).toHaveLength(2)

    await act(async () => {
      skippedButtons[0]?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      skippedButtons[1]?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(container.textContent ?? '').toContain('Skipped because this Gmail message already exists in the candidate database.')
    expect(container.textContent ?? '').toContain("Skipped because listing location 'Dallas, Texas, USA' did not match the saved Nvoids location filters.")

    const links = Array.from(container.querySelectorAll('a')).map((link) => ({
      text: link.textContent,
      href: link.getAttribute('href'),
    }))
    expect(links).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ text: 'Open exact email in Gmail', href: 'https://mail.google.com/mail/u/0/#all/msg-1' }),
        expect.objectContaining({ text: 'Open Original Post', href: 'https://www.nvoids.com/job1.jsp?id=1' }),
      ]),
    )
  })
})
