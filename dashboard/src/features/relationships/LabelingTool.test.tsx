// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import LabelingTool from './LabelingTool'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const candidate = {
  left_id: 11,
  right_id: 12,
  sampler: 'same_recruiter',
  left: {
    job_title: 'Java Developer',
    extracted_skills: 'java, spring',
    location: 'Austin, TX',
    email_sender: 'sarah@acme.com',
    email_subject: 'Java Developer needed',
    end_client: '',
    implementation_partner: '',
    domain: '',
  },
  right: {
    job_title: 'Java Backend Engineer',
    extracted_skills: 'java, kafka',
    location: 'Dallas, TX',
    email_sender: 'sarah@acme.com',
    email_subject: 'Backend role',
    end_client: '',
    implementation_partner: '',
    domain: '',
  },
  score: 0.813,
  confidence: 'likely',
  fields: [
    { key: 'job_title', label: 'Job title', coverage: '100%' },
    { key: 'end_client', label: 'End client', coverage: '6.7%' },
  ],
}

const queue = {
  verdicts: ['same_program', 'related_distinct', 'unrelated', 'unsure'],
  remaining: 40,
  candidates: [candidate],
}

const summary = {
  total: 12,
  by_verdict: { same_program: 6 },
  by_split: { train: 8, test: 4 },
  by_sampler: { same_recruiter: 12 },
  hard_negative_share: 0.1,
  target_total: 150,
  target_hard_negative_share: 0.4,
}

function stubFetch(overrides: { queueStatus?: number } = {}) {
  const posted: Array<{ url: string; body: unknown }> = []
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      posted.push({ url, body: JSON.parse(String(init.body)) })
      return { ok: true, status: 200, json: async () => ({ id: 1, verdict: 'same_program', split: 'train' }) }
    }
    if (url.includes('label-queue')) {
      const status = overrides.queueStatus ?? 200
      return { ok: status === 200, status, json: async () => queue }
    }
    return { ok: true, status: 200, json: async () => summary }
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, posted }
}

describe('LabelingTool', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
    vi.unstubAllGlobals()
  })

  const render = async () => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => { root?.render(<LabelingTool apiBase="http://api.test" />) })
    return container
  }

  const buttonSaying = (el: HTMLElement, text: string) =>
    Array.from(el.querySelectorAll('button')).find((button) => button.textContent?.includes(text)) as HTMLButtonElement

  // A labeler who sees the score first calibrates to the scorer instead of
  // labelling the truth, and the set then measures agreement with itself.
  it('hides the score until a verdict is recorded, then shows it', async () => {
    stubFetch()
    const el = await render()

    expect(el.textContent).not.toContain('0.813')
    expect(el.textContent).toContain('hidden until you record yours')

    await act(async () => { buttonSaying(el, 'Same programme').click() })

    expect(el.textContent).toContain('0.813')
    expect(el.textContent).toContain('likely')
  })

  it('posts the verdict with the block that produced the pair', async () => {
    const { posted } = stubFetch()
    const el = await render()

    await act(async () => { buttonSaying(el, 'Unrelated').click() })

    expect(posted).toHaveLength(1)
    expect(posted[0].body).toMatchObject({
      left_opportunity_id: 11,
      right_opportunity_id: 12,
      verdict: 'unrelated',
      sampler: 'same_recruiter',
    })
  })

  it('offers all four verdicts including unsure', async () => {
    stubFetch()
    const el = await render()

    const labels = Array.from(el.querySelectorAll('.labelingVerdicts button')).map((button) => button.textContent)
    expect(labels.some((label) => label?.includes('Same programme'))).toBe(true)
    expect(labels.some((label) => label?.includes('Related but distinct'))).toBe(true)
    expect(labels.some((label) => label?.includes('Unrelated'))).toBe(true)
    expect(labels.some((label) => label?.includes('Unsure'))).toBe(true)
  })

  // A labeler shown a blank field must know it is a gap in the data, not a
  // gap in this particular record.
  it('renders a blank field as "not available" with its coverage', async () => {
    stubFetch()
    const el = await render()

    const rows = Array.from(el.querySelectorAll('.labelingTable tbody tr'))
    const endClient = rows.find((row) => row.textContent?.includes('End client'))
    expect(endClient?.textContent).toContain('6.7% filled')
    expect(endClient?.textContent).toContain('not available')
    expect(rows[0].textContent).toContain('Java Developer')
  })

  it('records a verdict from a keyboard shortcut', async () => {
    const { posted } = stubFetch()
    await render()

    await act(async () => { window.dispatchEvent(new KeyboardEvent('keydown', { key: '1' })) })

    expect(posted[0].body).toMatchObject({ verdict: 'same_program' })
  })

  it('shows progress against the target set size and hard-negative share', async () => {
    stubFetch()
    const el = await render()

    expect(el.textContent).toContain('12 labelled')
    expect(el.textContent).toContain('target 150')
    expect(el.textContent).toContain('10% hard negatives')
  })

  it('says so when the feature is switched off rather than showing an empty queue', async () => {
    stubFetch({ queueStatus: 404 })
    const el = await render()

    expect(el.textContent).toContain('switched off')
    expect(el.querySelector('.labelingTable')).toBeNull()
  })
})
