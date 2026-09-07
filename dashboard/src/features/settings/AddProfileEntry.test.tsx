// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AddProfileEntry from './AddProfileEntry'
import { composeAppend, composeEntry, entryField, planAppend } from './profileEntry'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

// The save awaits crypto.subtle.digest before it ever reaches fetch, and that
// resolves on a macrotask - microtask flushing is not enough. How many macrotasks
// it takes is not fixed either: under a loaded suite one tick is sometimes short,
// which failed this file only when run alongside every other one. Waiting on the
// outcome rather than on a tick count is stable at any load. The cap only bounds
// a genuine hang - a real failure still reads as the assertion that follows.
const flushUntil = async (done: () => boolean) => {
  for (let tick = 0; tick < 50 && !done(); tick += 1) {
    await new Promise((resolve) => { setTimeout(resolve, 0) })
  }
}

const PROFILE = '# Chaithanya Dheeraj\n- Work Authorization: H1B\n'

describe('AddProfileEntry', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null
  let fetchMock: ReturnType<typeof vi.fn>

  const render = (node: React.ReactNode) => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(node))
    return container
  }

  const buttonLabelled = (text: string) => Array.from(
    container?.querySelectorAll<HTMLButtonElement>('button') ?? [],
  ).find((button) => button.textContent?.includes(text))

  const typeValue = (value: string) => {
    const input = container?.querySelector<HTMLInputElement>('input[aria-label="Profile entry value"]')
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set
      setter?.call(input, value)
      input?.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }

  const chooseField = (value: string) => {
    const select = container?.querySelector<HTMLSelectElement>('select[aria-label="Profile field"]')
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')?.set
      setter?.call(select, value)
      select?.dispatchEvent(new Event('change', { bubbles: true }))
    })
  }

  beforeEach(() => {
    fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ characters: 90 }) }))
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
    vi.unstubAllGlobals()
  })

  it('is absent when there is no profile to add to', () => {
    // R8: appending to nothing is creating, and creating is an upload. The panel
    // already prompts for that in this state.
    render(<AddProfileEntry apiBase="/api" profile="" onSaved={vi.fn()} />)

    expect(container?.textContent).toBe('')
  })

  it('previews the entry and the complete resulting document before sending anything', () => {
    render(<AddProfileEntry apiBase="/api" profile={PROFILE} onSaved={vi.fn()} />)
    typeValue('2 weeks')
    act(() => buttonLabelled('Preview')?.click())

    const shown = container?.querySelector('pre')?.textContent ?? ''
    expect(container?.textContent).toContain('- Work Authorization: 2 weeks')
    expect(shown).toContain('# Chaithanya Dheeraj')
    expect(shown).toContain('- Work Authorization: H1B')
    // This path has no proposal card, so the preview is what satisfies R5.
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sends nothing until Save, and nothing at all on Cancel', () => {
    render(<AddProfileEntry apiBase="/api" profile={PROFILE} onSaved={vi.fn()} />)
    typeValue('2 weeks')
    act(() => buttonLabelled('Preview')?.click())
    act(() => buttonLabelled('Cancel')?.click())

    expect(fetchMock).not.toHaveBeenCalled()
    expect(container?.querySelector('pre')).toBeNull()
  })

  it('posts the composed entry and the profile fingerprint on Save', async () => {
    const onSaved = vi.fn()
    render(<AddProfileEntry apiBase="/api" profile={PROFILE} onSaved={onSaved} />)
    typeValue('2 weeks')
    act(() => buttonLabelled('Preview')?.click())
    await act(async () => {
      buttonLabelled('Save to Profile')?.click()
      await flushUntil(() => onSaved.mock.calls.length > 0)
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/settings/candidate-profile/entries')
    const body = JSON.parse(String(options.body)) as Record<string, unknown>
    expect(body.entry).toBe('- Work Authorization: 2 weeks')
    expect(String(body.base_sha256)).toHaveLength(64)
    expect(onSaved).toHaveBeenCalled()
  })

  it('surfaces a 409 as the profile having changed', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ detail: 'The profile changed since this was prepared.' }),
    })
    const onSaved = vi.fn()
    render(<AddProfileEntry apiBase="/api" profile={PROFILE} onSaved={onSaved} />)
    typeValue('2 weeks')
    act(() => buttonLabelled('Preview')?.click())
    await act(async () => {
      buttonLabelled('Save to Profile')?.click()
      await flushUntil(() => (container?.textContent ?? '').includes('profile changed'))
    })

    expect(container?.textContent).toContain('profile changed')
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('offers a verbatim mode that keeps the user wording intact', () => {
    render(<AddProfileEntry apiBase="/api" profile={PROFILE} onSaved={vi.fn()} />)
    chooseField('__verbatim__')
    typeValue('I can join after two weeks')
    act(() => buttonLabelled('Preview')?.click())

    expect(container?.textContent).toContain(
      '- Work Authorization (in the user\'s words): "I can join after two weeks"',
    )
  })
})

// The duplication guard. Composing client-side avoids a preview endpoint whose
// only caller would be one panel, at the cost of compose_entry existing in two
// languages. These fixtures are the Python ones character for character; if
// they ever drift, the Settings path and the chat path would write differently
// shaped lines into the same document.
describe('composition parity with candidate_profile_service.py', () => {
  it('matches compose_entry in both modes', () => {
    expect(composeEntry('Notice period', '2 weeks', false)).toBe('- Notice period: 2 weeks')
    expect(composeEntry('Notice period', 'I can join after two weeks', true))
      .toBe('- Notice period (in the user\'s words): "I can join after two weeks"')
  })

  it('matches compose_append - the heading is added once and only once', () => {
    const first = composeAppend(PROFILE, '- Notice period: 2 weeks')
    expect(first).toBe(
      '# Chaithanya Dheeraj\n- Work Authorization: H1B\n\n## Saved from chat\n- Notice period: 2 weeks\n',
    )

    const second = composeAppend(first, '- Rate: $75/hr')
    expect(second.match(/## Saved from chat/g)).toHaveLength(1)
    expect(second).toBe(
      '# Chaithanya Dheeraj\n- Work Authorization: H1B\n\n## Saved from chat\n'
      + '- Notice period: 2 weeks\n- Rate: $75/hr\n',
    )
  })

  it('matches plan_append - one field holds one line, so a repeat is an update', () => {
    // The case that made this rule necessary: correcting a value already saved.
    // Two "Work Authorization" lines in the block the assistant is told to
    // believe leave it no way to tell which half is current.
    const saved = '# Me\n\n## Saved from chat\n- Notice period: 2 weeks\n- Work Authorization: H1B\n'
    const { combined, replaces } = planAppend(saved, '- Work Authorization: GC')

    expect(replaces).toEqual(['- Work Authorization: H1B'])
    expect(combined).toBe(
      '# Me\n\n## Saved from chat\n- Notice period: 2 weeks\n- Work Authorization: GC\n',
    )
    expect(combined.match(/Work Authorization/g)).toHaveLength(1)
  })

  it('never rewrites a line outside the Saved from chat section', () => {
    // `- Work Authorization: H1B` in the user's own uploaded text is their
    // document, not ours. It is left exactly as written and the new value is
    // added below; the card is what tells them the profile now says both.
    const { combined, replaces } = planAppend(PROFILE, '- Work Authorization: GC')

    expect(replaces).toEqual([])
    expect(combined).toBe(
      '# Chaithanya Dheeraj\n- Work Authorization: H1B\n\n## Saved from chat\n- Work Authorization: GC\n',
    )
  })

  it('matches entry_field across both composed shapes', () => {
    expect(entryField('- Work Authorization: GC')).toBe('Work Authorization')
    expect(entryField('- Notice period (in the user\'s words): "after two weeks"')).toBe('Notice period')
    expect(entryField('- Something we do not write: x')).toBeNull()
  })

  it('matches compose_append - a section below the saved heading survives', () => {
    const existing = '# Me\n\n## Saved from chat\n- Rate: $75/hr\n\n## My own notes\n- Prefer remote\n'

    const combined = composeAppend(existing, '- Notice period: 2 weeks')

    expect(combined.indexOf('- Notice period: 2 weeks')).toBeLessThan(combined.indexOf('## My own notes'))
    expect(combined).toContain('- Prefer remote')
  })
})
