// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App, { JobIntentLearningSection, buildDefaultPolicy, defaultDraftRules, normalizeDynamicPolicy } from './App'

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

function setSelectValue(element: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set
  setter?.call(element, value)
  element.dispatchEvent(new Event('change', { bubbles: true }))
}

function setInputValue(element: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  setter?.call(element, value)
  element.dispatchEvent(new Event('input', { bubbles: true }))
}

async function clickButton(button: HTMLButtonElement | undefined) {
  await act(async () => {
    button?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await Promise.resolve()
  })
}

function findSelect(container: HTMLDivElement, labelText: string): HTMLSelectElement | undefined {
  return Array.from(container.querySelectorAll('select')).find(
    (element) => (element.parentElement?.textContent ?? '').includes(labelText),
  ) as HTMLSelectElement | undefined
}

function findInput(container: HTMLDivElement, labelText: string): HTMLInputElement | undefined {
  return Array.from(container.querySelectorAll('input')).find(
    (element) => (element.parentElement?.textContent ?? '').includes(labelText),
  ) as HTMLInputElement | undefined
}

function findChipInput(container: HTMLDivElement, labelText: string): HTMLInputElement | undefined {
  const label = Array.from(container.querySelectorAll('label')).find((element) =>
    (element.textContent ?? '').trim().startsWith(labelText),
  )
  return label?.querySelector('input.skillInput') as HTMLInputElement | undefined
}

describe('draft rule policy helpers', () => {
  it('normalizes missing and legacy draft rules into the current shape', () => {
    const normalized = normalizeDynamicPolicy(
      {
        ...buildDefaultPolicy(),
        qualification: {
          location_strictness: 'strict',
          score_threshold_override_enabled: true,
          score_threshold_override_value: 0.75,
          draft_filters: {
            accepted_location_filter_enabled: false,
            require_to_and_cc_before_draft_enabled: false,
          } as unknown as never,
        },
      } as unknown as Parameters<typeof normalizeDynamicPolicy>[0],
      {
        accepted_locations: ['texas'],
        min_salary: 70,
        must_have_skills: ['java', 'spring'],
        qualification_threshold: 0.75,
      },
    )

    expect(normalized.qualification.location_strictness).toBe('strict')
    expect(normalized.qualification.draft_rules.accepted_location.mode).toBe('warn')
    expect(normalized.qualification.draft_rules.recipient_mapping.mode).toBe('warn')
    expect(normalized.qualification.draft_rules.minimum_salary.mode).toBe(defaultDraftRules().minimum_salary.mode)
    expect(normalized.qualification.draft_rules.accepted_location.locations).toEqual(['texas'])
    expect(normalized.qualification.draft_rules.minimum_salary.value).toBe(70)
  })
})

describe('Draft Qualification Rules settings UI', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
  })

  function mockFetchWithSettings(policyOverride?: Record<string, unknown>) {
    const putBodies: unknown[] = []

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/gmail/status')) return makeResponse({ configured: true, authenticated: true, token_path: 'token.json', last_sync_at: null, detail: 'ok' })
        if (url.endsWith('/ai/status')) return makeResponse({ configured: true, connected: true, running: false, provider: 'mock', model: 'mock', detail: 'ok', last_error: null, last_started_at: null, last_finished_at: null, last_duration_ms: null, last_draft_source: null })
        if (url.endsWith('/telegram/status')) return makeResponse({ enabled: false, polling: false, alerts_enabled: false, authorized_chats: 0, detail: 'off' })
        if (url.endsWith('/gmail/oauth/url')) return makeResponse({ authorization_url: null })
        if (url.includes('/settings/bootstrap')) {
          return makeResponse({
            settings: {
              enabled: true,
              gmail_query: 'is:unread',
              default_gmail_query: 'is:unread',
              saved_gmail_queries: [],
              mail_date: null,
              default_date_mode: 'today',
              min_salary: 60,
              accepted_locations: ['texas', 'remote'],
              visa_required_allowed: false,
              remote_preference: 'any',
              role_keywords: [],
              must_have_skills: ['java', 'spring'],
              employer_domains: [],
              free_text_guidance: '',
              qualification_threshold: 0.6,
              feature_auto_polling: false,
              feature_auto_poll_interval_minutes: 10,
              feature_nvoids_enabled: true,
              feature_nvoids_auto_sync: false,
              feature_nvoids_poll_interval_minutes: 30,
              nvoids_batch_limit: 10,
              nvoids_detail_title_mode: 'job_details',
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
              policy: policyOverride ?? buildDefaultPolicy(),
            },
            resumes: [],
            attachments: [],
            pending_skills: [],
            pending_job_intent_signals: [],
            approved_job_intent_signals: [],
            loaded_at: '2026-07-09T00:00:00Z',
            owner_id: 'default-owner',
          })
        }
        if (url.endsWith('/settings') && (!init || init.method === undefined)) {
          return makeResponse({
            enabled: true,
            gmail_query: 'is:unread',
            default_gmail_query: 'is:unread',
            saved_gmail_queries: [],
            mail_date: null,
            default_date_mode: 'today',
            min_salary: 60,
            accepted_locations: ['texas', 'remote'],
            visa_required_allowed: false,
            remote_preference: 'any',
            role_keywords: [],
            must_have_skills: ['java', 'spring'],
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
            policy: policyOverride ?? buildDefaultPolicy(),
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

    return putBodies
  }

  async function renderApp(policyOverride?: Record<string, unknown>) {
    const putBodies = mockFetchWithSettings(policyOverride)
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

    await clickButton(settingsButton)

    return { container, putBodies }
  }

  it('saves updated draft rule modes and values', async () => {
    const { container, putBodies } = await renderApp({
      ...buildDefaultPolicy(),
      qualification: {
        ...buildDefaultPolicy().qualification,
        draft_filters: {
          accepted_location_filter_enabled: false,
        },
      },
    })

    expect(container.textContent ?? '').toContain('Draft Qualification Rules')
    expect(container.textContent ?? '').toContain('Recipient mapping rule')

    const acceptedLocationSelect = findSelect(container, 'Accepted location rule')
    const recipientMappingSelect = findSelect(container, 'Recipient mapping rule')
    const acceptedLocationsInput = findChipInput(container, 'Accepted locations')
    expect(acceptedLocationSelect).toBeDefined()
    expect(recipientMappingSelect).toBeDefined()
    expect(acceptedLocationsInput).toBeDefined()

    await act(async () => {
      if (acceptedLocationSelect) setSelectValue(acceptedLocationSelect, 'warn')
      if (recipientMappingSelect) setSelectValue(recipientMappingSelect, 'ignore')
      if (acceptedLocationsInput) {
        for (const location of ['texas', 'remote', 'ohio']) {
          setInputValue(acceptedLocationsInput, location)
          acceptedLocationsInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
        }
      }
      await Promise.resolve()
    })

    const saveButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Save Settings'),
    ) as HTMLButtonElement | undefined

    await act(async () => {
      saveButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(putBodies).toHaveLength(1)
    const savedBody = putBodies[0] as {
      accepted_locations: string[]
      policy: { qualification: { draft_rules: { accepted_location: { mode: string; locations: string[] }; recipient_mapping: { mode: string } } } }
    }
    expect(savedBody.policy.qualification.draft_rules.accepted_location.mode).toBe('warn')
    expect(savedBody.policy.qualification.draft_rules.accepted_location.locations).toEqual(['texas', 'remote', 'ohio'])
    expect(savedBody.policy.qualification.draft_rules.recipient_mapping.mode).toBe('ignore')
    expect(savedBody.accepted_locations).toEqual(['texas', 'remote', 'ohio'])
  })

  it('returns to Run Queue with the server-confirmed configuration summary after save', async () => {
    const { container } = await renderApp({
      ...buildDefaultPolicy(),
      qualification: {
        ...buildDefaultPolicy().qualification,
        draft_rules: {
          ...defaultDraftRules(),
          accepted_location: { mode: 'block', locations: ['texas', 'remote'] },
          must_have_skills: { mode: 'block', skills: ['java', 'spring'] },
        },
      },
    })
    const qualificationThreshold = findInput(container, 'Score threshold value')
    expect(qualificationThreshold).toBeDefined()

    await act(async () => {
      if (qualificationThreshold) setInputValue(qualificationThreshold, '0.91')
      await Promise.resolve()
    })

    const saveButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Save Settings'),
    ) as HTMLButtonElement | undefined

    await clickButton(saveButton)
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    const summaryIntro = container.querySelector('[aria-label="Active Configuration Summary"]')
    expect(summaryIntro).not.toBeNull()
    expect(summaryIntro?.textContent).toContain('Last saved:')
    expect(container.querySelector('form')).toBeNull()

    const summaryGrid = container.querySelector('.configSummaryGrid')
    expect(summaryGrid).not.toBeNull()
    expect(summaryGrid?.textContent).toContain('Score Threshold Value:0.6')
    expect(summaryGrid?.textContent).not.toContain('0.91')
    // Literal values, not just booleans/counts: accepted_locations and must_have_skills
    // are non-empty in the fixture, employer_domains is empty.
    expect(summaryGrid?.textContent).toContain('texas, remote')
    expect(summaryGrid?.textContent).toContain('java, spring')
    expect(summaryGrid?.textContent).toContain('Employer Domains:(none)')
  })

  it('applies profile presets using draft rules and seeded values', async () => {
    const { container, putBodies } = await renderApp()

    const profileSelect = findSelect(container, 'Policy Profile')
    const applyProfileButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Apply Profile'),
    ) as HTMLButtonElement | undefined
    expect(profileSelect).toBeDefined()
    expect(applyProfileButton).toBeDefined()

    await act(async () => {
      if (profileSelect) setSelectValue(profileSelect, 'Flexible Drafting')
      applyProfileButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })

    const saveButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Save Settings'),
    ) as HTMLButtonElement | undefined

    await act(async () => {
      saveButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(putBodies).toHaveLength(1)
    const savedBody = putBodies[0] as {
      qualification_threshold: number
      policy: { qualification: { draft_rules: { minimum_salary: { mode: string }; recipient_mapping: { mode: string }; score_threshold: { mode: string; value: number | null } } } }
    }
    expect(savedBody.policy.qualification.draft_rules.minimum_salary.mode).toBe('ignore')
    expect(savedBody.policy.qualification.draft_rules.recipient_mapping.mode).toBe('warn')
    expect(savedBody.policy.qualification.draft_rules.score_threshold.mode).toBe('warn')
    expect(savedBody.policy.qualification.draft_rules.score_threshold.value).toBe(0.5)
    expect(savedBody.qualification_threshold).toBe(0.5)
  })

  it('bulk-approves pending job intent learning signals from the settings card', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    await act(async () => {
      root.render(
        <JobIntentLearningSection
          pendingSignals={[
            {
              id: 101,
              owner_id: 'default-owner',
              phrase: 'share updated resume',
              normalized_phrase: 'share updated resume',
              polarity: 'positive_recruiter_jd',
              source_examples_count: 2,
              sample_evidence: ['share updated resume'],
              confidence_aggregate: 0.77,
              last_intent_type: 'recruiter_job_requirement',
              status: 'pending',
              created_at: '2026-07-06T00:00:00Z',
              updated_at: '2026-07-06T00:00:00Z',
            },
          ]}
          approvedSignals={[]}
          loading={false}
          busySignalKey={null}
          approveAllSignals={vi.fn()}
          approveSignal={vi.fn()}
          dismissSignal={vi.fn()}
          togglePolarity={vi.fn()}
        />,
      )
    })

    const approveAllButton = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Approve all'),
    ) as HTMLButtonElement | undefined

    expect(approveAllButton).toBeDefined()
    expect(approveAllButton?.textContent).toContain('Approve all')

    const approveAllSignals = vi.fn()
    await act(async () => {
      root.render(
        <JobIntentLearningSection
          pendingSignals={[
            {
              id: 101,
              owner_id: 'default-owner',
              phrase: 'share updated resume',
              normalized_phrase: 'share updated resume',
              polarity: 'positive_recruiter_jd',
              source_examples_count: 2,
              sample_evidence: ['share updated resume'],
              confidence_aggregate: 0.77,
              last_intent_type: 'recruiter_job_requirement',
              status: 'pending',
              created_at: '2026-07-06T00:00:00Z',
              updated_at: '2026-07-06T00:00:00Z',
            },
          ]}
          approvedSignals={[]}
          loading={false}
          busySignalKey={null}
          approveAllSignals={approveAllSignals}
          approveSignal={vi.fn()}
          dismissSignal={vi.fn()}
          togglePolarity={vi.fn()}
        />,
      )
    })
    await clickButton(Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Approve all')) as HTMLButtonElement | undefined)
    expect(approveAllSignals).toHaveBeenCalledTimes(1)

    await act(async () => {
      root.render(
        <JobIntentLearningSection
          pendingSignals={[
            {
              id: 101,
              owner_id: 'default-owner',
              phrase: 'share updated resume',
              normalized_phrase: 'share updated resume',
              polarity: 'positive_recruiter_jd',
              source_examples_count: 2,
              sample_evidence: ['share updated resume'],
              confidence_aggregate: 0.77,
              last_intent_type: 'recruiter_job_requirement',
              status: 'pending',
              created_at: '2026-07-06T00:00:00Z',
              updated_at: '2026-07-06T00:00:00Z',
            },
          ]}
          approvedSignals={[]}
          loading={false}
          busySignalKey="approve-all-intents"
          approveAllSignals={vi.fn()}
          approveSignal={vi.fn()}
          dismissSignal={vi.fn()}
          togglePolarity={vi.fn()}
        />,
      )
    })
    expect(container.textContent ?? '').toContain('Approving all...')
  })
})
