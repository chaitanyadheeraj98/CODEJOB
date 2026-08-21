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

function makeResponse(payload: unknown, ok = true): MockResponse {
  return {
    ok,
    json: async () => payload,
  }
}

function makeSettings(overrides?: Record<string, unknown>) {
  return {
    enabled: true,
    gmail_query: 'label:jobs',
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
    feature_gmail_requirement_groups_enabled: false,
    feature_role_manifest_enabled: false,
    feature_strict_candidate_screening_enabled: false,
    candidate_work_authorizations: [],
    candidate_total_experience_years: null,
    candidate_us_experience_years: null,
    candidate_current_location: '',
    draft_text_size: 'normal',
    fallback_draft_template: '',
    signature_name: '',
    signature_phone: '',
    signature_email: '',
    preferred_employer_cc_email: '',
    resume_display_name: '',
    policy: null,
    ...overrides,
  }
}

function makeBootstrapPayload(overrides?: {
  settings?: Record<string, unknown>
  resumes?: Array<Record<string, unknown>>
  attachments?: Array<Record<string, unknown>>
  pending_skills?: Array<Record<string, unknown>>
  pending_job_intent_signals?: Array<Record<string, unknown>>
  approved_job_intent_signals?: Array<Record<string, unknown>>
}) {
  return {
    settings: makeSettings(overrides?.settings),
    role_manifest_child_creation_enabled: false,
    resumes: overrides?.resumes ?? [],
    attachments: overrides?.attachments ?? [],
    pending_skills: overrides?.pending_skills ?? [],
    pending_job_intent_signals: overrides?.pending_job_intent_signals ?? [],
    approved_job_intent_signals: overrides?.approved_job_intent_signals ?? [],
    loaded_at: '2026-07-09T00:00:00Z',
    owner_id: 'default-owner',
  }
}

async function flushPromises(iterations = 4): Promise<void> {
  for (let index = 0; index < iterations; index += 1) {
    await Promise.resolve()
  }
}

async function openSettings(container: HTMLElement): Promise<void> {
  const settingsButton = Array.from(container.querySelectorAll('button')).find(
    (button) => button.textContent === 'Settings',
  )
  await act(async () => {
    settingsButton?.click()
    await flushPromises(6)
  })
}

function makeAppFetch(options?: {
  bootstrapOk?: boolean
  bootstrapPayload?: ReturnType<typeof makeBootstrapPayload>
  onBootstrapCall?: () => void
  onSaveSettings?: () => void
}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/gmail/status')) return makeResponse({ configured: true, authenticated: true, token_path: 'token.json', last_sync_at: null, detail: 'ok' })
    if (url.endsWith('/ai/status')) return makeResponse({ configured: true, connected: true, running: false, provider: 'mock', model: 'mock', detail: 'ok', last_error: null, last_started_at: null, last_finished_at: null, last_duration_ms: null, last_draft_source: null })
    if (url.endsWith('/telegram/status')) return makeResponse({ enabled: false, polling: false, alerts_enabled: false, authorized_chats: 0, detail: 'off' })
    if (url.endsWith('/gmail/oauth/url')) return makeResponse({ authorization_url: null })
    if (url.includes('/settings/bootstrap')) {
      options?.onBootstrapCall?.()
      if (options?.bootstrapOk === false) return makeResponse({ detail: 'failed' }, false)
      return makeResponse(options?.bootstrapPayload ?? makeBootstrapPayload())
    }
    if (url.endsWith('/settings/skills/pending')) {
      return makeResponse(options?.bootstrapPayload?.pending_skills ?? [])
    }
    if (url.endsWith('/settings/job-intent-learning/pending')) {
      return makeResponse(options?.bootstrapPayload?.pending_job_intent_signals ?? [])
    }
    if (url.endsWith('/settings/job-intent-learning/approved')) {
      return makeResponse(options?.bootstrapPayload?.approved_job_intent_signals ?? [])
    }
    if (url.endsWith('/settings') && init?.method === 'PUT') {
      options?.onSaveSettings?.()
      return makeResponse(makeSettings())
    }
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
  })
}

describe('Settings bootstrap flow', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    vi.useRealTimers()
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
  })

  it('enqueues automation, renders color-coded progress, and polls to completion', async () => {
    vi.useFakeTimers()
    let jobPolls = 0
    const baseFetch = makeAppFetch()
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/jobs/automation-run') && init?.method === 'POST') {
        return makeResponse({ run_key: 'automation_run:test-job', job_id: 'job-1', status: 'queued' })
      }
      if (url.includes('/jobs/automation_run%3Atest-job')) {
        jobPolls += 1
        const complete = jobPolls > 1
        return makeResponse({
          run_key: 'automation_run:test-job',
          job_id: 'job-1',
          status: complete ? 'ok' : 'running',
          detail: complete ? 'Automation complete.' : 'Processing candidates.',
          processed_items: complete ? 1 : 0,
          total_items: 1,
          progress_pct: complete ? 100 : 25,
          queue_name: 'automation_run',
        })
      }
      return baseFetch(input, init)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    await act(async () => {
      root.render(<App />)
      await flushPromises(8)
    })
    const runButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Sync + Queue')
    expect(runButton).toBeTruthy()

    await act(async () => {
      runButton?.click()
      await flushPromises(10)
    })
    const progressBar = container.querySelector('[role="progressbar"]') as HTMLElement | null
    expect(progressBar?.getAttribute('aria-valuenow')).toBe('25')
    expect(container.textContent ?? '').toContain('Processing candidates.')
    expect(container.textContent ?? '').toContain('Running')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
      await flushPromises(10)
    })
    expect((container.querySelector('[role="progressbar"]') as HTMLElement | null)?.getAttribute('aria-valuenow')).toBe('100')
    expect(container.textContent ?? '').toContain('Automation complete.')
    expect(container.textContent ?? '').toContain('Completed')
    expect(runButton?.hasAttribute('disabled')).toBe(false)
  })

  it('hydrates settings-domain data together from the bootstrap payload', async () => {
    vi.stubGlobal(
      'fetch',
      makeAppFetch({
        bootstrapPayload: makeBootstrapPayload({
          settings: { gmail_query: 'from:recruiters newer_than:7d' },
          resumes: [
            {
              id: 7,
              file_name: 'Primary Resume.docx',
              display_name: 'Primary Resume',
              is_current: true,
              is_enabled: true,
              created_at: '2026-07-09T00:00:00Z',
              updated_at: '2026-07-09T00:00:00Z',
              skills_text: 'Java, Spring Boot',
            },
          ],
          attachments: [
            {
              id: 5,
              file_name: 'cover-letter.pdf',
              file_size: 1024,
              is_enabled: true,
              created_at: '2026-07-09T00:00:00Z',
              updated_at: '2026-07-09T00:00:00Z',
            },
          ],
          pending_skills: [
            {
              skill_name: 'Twistlock',
              normalized_name: 'twistlock',
              occurrence_count: 1,
              candidate_ids: [42],
              suspicious: false,
              recoverable_skills: [],
              source_tags: ['ai'],
            },
          ],
          pending_job_intent_signals: [
            {
              id: 1,
              owner_id: 'default-owner',
              phrase: 'java developer',
              normalized_phrase: 'java developer',
              polarity: 'positive',
              source_examples_count: 2,
              sample_evidence: ['Subject: Java Developer'],
              confidence_aggregate: 0.9,
              last_intent_type: 'job_title',
              status: 'pending',
              created_at: '2026-07-09T00:00:00Z',
              updated_at: '2026-07-09T00:00:00Z',
            },
          ],
        }),
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
      await flushPromises(6)
    })

    const searchInput = container.querySelector('input.search') as HTMLInputElement | null
    expect(searchInput?.value).toBe('from:recruiters newer_than:7d')
    await openSettings(container)
    expect(container.textContent ?? '').toContain('cover-letter.pdf')
    expect(container.querySelector('input[aria-label="Upload attachment files"]')).not.toBeNull()
    expect(container.textContent ?? '').toContain('Primary Resume.docx')
    expect(container.textContent ?? '').toContain('Twistlock')
    expect(container.textContent ?? '').toContain('java developer')
    expect(container.querySelector('input[aria-label="Upload resume file"]')).not.toBeNull()
    expect(container.textContent ?? '').not.toContain('Settings Bootstrap')
  })

  it('keeps the settings UI unloaded on bootstrap failure and recovers on retry', async () => {
    let shouldFailBootstrap = true
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const bootstrapOk = !shouldFailBootstrap
        return makeAppFetch({
          bootstrapOk,
          bootstrapPayload: makeBootstrapPayload({ settings: { gmail_query: 'is:starred' } }),
        })(input, init)
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
      await flushPromises(6)
    })

    const initialSearchInput = container.querySelector('input.search') as HTMLInputElement | null
    expect(initialSearchInput?.value).toBe('')
    expect(initialSearchInput?.disabled).toBe(true)
    expect(initialSearchInput?.getAttribute('placeholder')).toBe('Loading saved settings...')
    expect(container.textContent ?? '').toContain('Failed to load saved settings')

    shouldFailBootstrap = false
    const retryButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Retry Loading Settings'),
    ) as HTMLButtonElement | undefined
    expect(retryButton).toBeDefined()

    await act(async () => {
      retryButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await flushPromises(6)
    })

    const searchInputAfterRetry = container.querySelector('input.search') as HTMLInputElement | null
    expect(searchInputAfterRetry?.value).toBe('is:starred')
    expect(searchInputAfterRetry?.disabled).toBe(false)
    expect(container.textContent ?? '').not.toContain('Failed to load saved settings')
  })

  it('reloads bootstrap data after remount instead of using a module-level latch', async () => {
    let bootstrapCalls = 0
    vi.stubGlobal(
      'fetch',
      makeAppFetch({
        onBootstrapCall: () => {
          bootstrapCalls += 1
        },
      }),
    )

    const firstContainer = document.createElement('div')
    document.body.appendChild(firstContainer)
    const firstRoot: Root = createRoot(firstContainer)
    cleanups.push(() => {
      act(() => firstRoot.unmount())
      firstContainer.remove()
    })

    await act(async () => {
      firstRoot.render(<App />)
      await flushPromises(6)
    })

    act(() => {
      firstRoot.unmount()
    })
    firstContainer.remove()

    const secondContainer = document.createElement('div')
    document.body.appendChild(secondContainer)
    const secondRoot: Root = createRoot(secondContainer)
    cleanups.push(() => {
      act(() => secondRoot.unmount())
      secondContainer.remove()
    })

    await act(async () => {
      secondRoot.render(<App />)
      await flushPromises(6)
    })

    expect(bootstrapCalls).toBe(2)
  })

  it('refreshes settings-domain state from bootstrap after saving settings', async () => {
    let bootstrapCalls = 0
    let saveCalls = 0
    vi.stubGlobal(
      'fetch',
      makeAppFetch({
        onBootstrapCall: () => {
          bootstrapCalls += 1
        },
        onSaveSettings: () => {
          saveCalls += 1
        },
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
      await flushPromises(6)
    })

    expect(bootstrapCalls).toBe(1)
    await openSettings(container)

    const saveButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent === 'Save Settings',
    ) as HTMLButtonElement | undefined
    expect(saveButton).toBeDefined()

    await act(async () => {
      saveButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await flushPromises(6)
    })

    expect(saveCalls).toBe(1)
    expect(bootstrapCalls).toBe(2)
  })

  it('places the editable eligibility profile and strict toggle under Profile Settings', async () => {
    vi.stubGlobal('fetch', makeAppFetch())
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    await act(async () => {
      root.render(<App />)
      await flushPromises(6)
    })
    await openSettings(container)

    const sections = Array.from(container.querySelectorAll('section'))
    const profile = sections.find((section) => section.querySelector('h2')?.textContent === 'Profile Settings')
    const automation = sections.find((section) => section.querySelector('h2')?.textContent === 'AI Automation Access')
    expect(profile?.textContent).toContain('Candidate Eligibility Profile')
    expect(profile?.textContent).toContain('Enforce Strict Candidate Screening')
    expect(profile?.textContent).toContain('Candidate Work Authorizations')
    expect(profile?.textContent).toContain('Total Experience Years')
    expect(profile?.textContent).toContain('U.S. Experience Years')
    expect(profile?.textContent).toContain('Current Location')
    expect(automation?.textContent).toContain('Enable Role Manifest Detection')
    expect(automation?.textContent).not.toContain('Candidate Work Authorizations')
    const profileInputs = Array.from(profile?.querySelectorAll('input') ?? [])
    expect(profileInputs.every((input) => !input.disabled)).toBe(true)
    expect(container.textContent).toContain('Save Settings')
  })

  it.each([
    [false, 'Detection only (dark-run). Child drafts are disabled at the deployment level.'],
    [true, 'Detection and child drafting are active at the deployment level.'],
  ])('shows the read-only role manifest deployment state when child creation is %s', async (enabled, expected) => {
    vi.stubGlobal(
      'fetch',
      makeAppFetch({
        bootstrapPayload: {
          ...makeBootstrapPayload(),
          role_manifest_child_creation_enabled: enabled,
        },
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
      await flushPromises(6)
    })
    await openSettings(container)

    expect(container.textContent).toContain(expected)
  })
})
