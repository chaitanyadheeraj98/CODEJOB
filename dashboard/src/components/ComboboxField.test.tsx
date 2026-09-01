// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ComboboxField from './ComboboxField'
import type { FilterFieldConfig } from './FilterSortBar'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const field = { key: 'role', label: 'Job title', type: 'combobox', bucket: 'needs_review' } satisfies FilterFieldConfig

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  setter?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('ComboboxField', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  function renderField(onChange = vi.fn()) {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<ComboboxField field={field} apiBase="http://api.test" onChange={onChange} />))
    return { input: container.querySelector('input')!, onChange }
  }

  function respond(values = ['Alpha', 'Beta']) {
    return vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ values }),
    } as Response)
  }

  async function advance(milliseconds: number) {
    await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds) })
  }

  it('loads and renders options immediately when focused empty', async () => {
    vi.useFakeTimers()
    const fetchMock = respond()
    const { input } = renderField()
    act(() => input.focus())
    await advance(0)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain('bucket=needs_review&field=role&limit=20')
    expect(String(fetchMock.mock.calls[0][0])).not.toContain('q=')
    expect(container?.querySelectorAll('[role="option"]')).toHaveLength(2)
  })

  it('debounces typed option fetches and filter changes', async () => {
    vi.useFakeTimers()
    const fetchMock = respond()
    const { input, onChange } = renderField()
    act(() => input.focus())
    await advance(0)
    fetchMock.mockClear()

    act(() => setInputValue(input, 'J'))
    await advance(100)
    act(() => setInputValue(input, 'Ja'))
    await advance(100)
    act(() => setInputValue(input, 'Java'))
    await advance(299)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(onChange).not.toHaveBeenCalled()
    await advance(1)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain('q=Java')
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith('role', 'Java')
  })

  it('commits a clicked option immediately without a stale debounced write', async () => {
    vi.useFakeTimers()
    respond()
    const { input, onChange } = renderField()
    act(() => input.focus())
    await advance(0)
    act(() => setInputValue(input, 'A'))
    const option = container?.querySelector<HTMLButtonElement>('[role="option"]')
    act(() => option?.click())

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith('role', 'Alpha')
    await advance(600)
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('keeps free text and commits it after blur', async () => {
    vi.useFakeTimers()
    respond([])
    const { input, onChange } = renderField()
    act(() => input.focus())
    await advance(0)
    act(() => setInputValue(input, 'Custom role'))
    act(() => input.blur())
    await advance(300)
    expect(onChange).toHaveBeenCalledWith('role', 'Custom role')
  })

  it('selects the second option with ArrowDown and Enter', async () => {
    vi.useFakeTimers()
    respond()
    const { input, onChange } = renderField()
    act(() => input.focus())
    await advance(0)
    act(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })))
    act(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })))
    act(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
    expect(onChange).toHaveBeenCalledWith('role', 'Beta')
  })

  it('closes on Escape without reverting typed text', async () => {
    vi.useFakeTimers()
    respond()
    const { input } = renderField()
    act(() => input.focus())
    await advance(0)
    act(() => setInputValue(input, 'Typed'))
    act(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    expect(input.value).toBe('Typed')
    expect(input.getAttribute('aria-expanded')).toBe('false')
  })

  it('keeps free text usable when suggestions fail', async () => {
    vi.useFakeTimers()
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    const { input, onChange } = renderField()
    act(() => input.focus())
    await advance(0)
    expect(container?.textContent).toContain("Couldn't load suggestions")
    act(() => setInputValue(input, 'Manual'))
    await advance(300)
    expect(onChange).toHaveBeenCalledWith('role', 'Manual')
  })
})
