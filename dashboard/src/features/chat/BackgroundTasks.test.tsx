// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BackgroundTasks } from './BackgroundTasks'
import { jobLabel, statusLabel, type BackgroundJob } from './backgroundJobs'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const job = (overrides: Partial<BackgroundJob> = {}): BackgroundJob => ({
  run_key: 'nvoids_client_search:abc123',
  run_source: 'nvoids_sync',
  job_id: 'job-1',
  status: 'running',
  detail: 'Crawling page 2.',
  processed_items: 4,
  total_items: 10,
  progress_pct: 40,
  queue_name: 'nvoids_sync',
  skipped_item_count: 0,
  failed_count: 0,
  created_at: new Date(Date.now() - 30_000).toISOString(),
  ...overrides,
})

describe('BackgroundTasks', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
    vi.restoreAllMocks()
  })

  const stubJobs = (items: BackgroundJob[], onCancel?: () => void) => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if (url.includes('/cancel')) {
        onCancel?.()
        return new Response(JSON.stringify(items[0]), { status: 200 })
      }
      return new Response(
        JSON.stringify({ items, active_count: items.filter((item) => item.status === 'running' || item.status === 'queued').length }),
        { status: 200 },
      )
    }))
    return calls
  }

  const mount = async () => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => { root?.render(<BackgroundTasks apiBase="http://localhost:8000" />) })
    return container
  }

  const click = async (element: Element | null | undefined) => {
    await act(async () => { (element as HTMLElement).click() })
  }

  it('counts the running tasks on the trigger before anything is opened', async () => {
    stubJobs([job(), job({ run_key: 'gmail_sync:done', status: 'ok' })])
    const view = await mount()
    expect(view.querySelector('.bgTasksBadge')?.textContent).toBe('1')
    // The list itself stays closed - the badge is the whole point of polling
    // while shut.
    expect(view.querySelector('.bgTasksPanel')).toBeNull()
  })

  it('opens to the running task and offers to stop it', async () => {
    const cancelled = vi.fn()
    const calls = stubJobs([job()], cancelled)
    const view = await mount()

    await click(view.querySelector('.bgTasksTrigger'))
    expect(view.querySelector('.bgTaskName')?.textContent).toBe('Nvoids search')
    expect(view.querySelector('.bgTaskStatus')?.textContent).toBe('Running')

    // Collapsed, a row is a name and a bar. The detail and the stop control are
    // one click in, which is the interaction the panel exists for.
    expect(view.querySelector('.bgTaskStop')).toBeNull()
    await click(view.querySelector('.bgTaskHead'))
    expect(view.querySelector('.bgTaskDetail')?.textContent).toBe('Crawling page 2.')
    expect(view.querySelector('.bgTaskFacts dd')?.textContent).toBe('4 of 10')

    await click(view.querySelector('.bgTaskStop'))
    expect(cancelled).toHaveBeenCalledOnce()
    expect(calls).toContain('POST http://localhost:8000/jobs/nvoids_client_search%3Aabc123/cancel')
  })

  it('offers no stop button for a task that has already finished', async () => {
    stubJobs([job({ status: 'ok', progress_pct: 100, processed_items: 10 })])
    const view = await mount()
    await click(view.querySelector('.bgTasksTrigger'))
    await click(view.querySelector('.bgTaskHead'))

    expect(view.querySelector('.bgTaskStatus')?.textContent).toBe('Completed')
    expect(view.querySelector('.bgTaskStop')).toBeNull()
    // No bar either: a finished run has nothing left to track.
    expect(view.querySelector('.bgTaskBar')).toBeNull()
  })

  it('says so rather than showing an empty list when the jobs route fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 500 })))
    const view = await mount()
    await click(view.querySelector('.bgTasksTrigger'))
    expect(view.querySelector('.bgTaskError')?.textContent).toBe('Could not read background tasks')
    expect(view.querySelector('.bgTasksEmpty')).toBeNull()
  })
})

describe('background job naming', () => {
  it('separates the assistant-started search from the scheduled feed sync', () => {
    // Both are run_source `nvoids_sync`. Only the run-key prefix tells the user
    // which one is theirs.
    expect(jobLabel(job())).toBe('Nvoids search')
    expect(jobLabel(job({ run_key: 'nvoids_sync:nightly' }))).toBe('Nvoids feed sync')
  })

  it('reads the stored "ok" status as a result rather than a shrug', () => {
    expect(statusLabel('ok')).toBe('Completed')
    expect(statusLabel('canceled')).toBe('Stopped')
  })

  it('names the automation-run outcomes that share the status column', () => {
    // Found against the live database, not imagined: 32 of the 50 most recent
    // runs were `oauth_required` and 10 were `skipped`, so shipping only the
    // queue's own vocabulary would have put raw tokens on screen immediately.
    expect(statusLabel('oauth_required')).toBe('Needs Gmail sign-in')
    expect(statusLabel('skipped')).toBe('Nothing to do')
    expect(statusLabel('ready')).toBe('Completed')
  })

  it('humanises a status from a backend newer than this dashboard', () => {
    expect(statusLabel('some_new_state')).toBe('Some new state')
  })
})
