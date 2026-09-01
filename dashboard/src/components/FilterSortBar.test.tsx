// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import FilterSortBar, { type FilterFieldConfig, type FilterValues, type SortOption } from './FilterSortBar'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  setter?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

const sortOptions: SortOption[] = [{ value: 'newest', label: 'Newest' }, { value: 'oldest', label: 'Oldest' }]

const fiveFields: FilterFieldConfig[] = [
  { key: 'q', label: 'Search', type: 'text' },
  { key: 'role', label: 'Job title', type: 'combobox', bucket: 'needs_review' },
  { key: 'source', label: 'Source', type: 'select', options: [{ value: 'all', label: 'All' }, { value: 'gmail', label: 'Gmail' }] },
  { key: 'status', label: 'Status', type: 'multiselect', options: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }] },
  { key: 'flag', label: 'Flag', type: 'boolean' },
  { key: 'score', label: 'Score', type: 'range', min: 0, max: 100 },
]

const defaultValues: FilterValues = { q: '', role: '', source: 'all', status: [], flag: null, score: { min: null, max: null } }

describe('FilterSortBar', () => {
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

  function renderBar(props: Partial<React.ComponentProps<typeof FilterSortBar>> = {}) {
    const onFieldChange = props.onFieldChange ?? vi.fn()
    const onClear = props.onClear ?? vi.fn()
    const onSortChange = props.onSortChange ?? vi.fn()
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => {
      root?.render(
        <FilterSortBar
          fields={fiveFields}
          values={defaultValues}
          onFieldChange={onFieldChange}
          onClear={onClear}
          sortOptions={sortOptions}
          sortValue="newest"
          onSortChange={onSortChange}
          {...props}
        />,
      )
    })
    return { onFieldChange, onClear, onSortChange }
  }

  it('renders each non-text field type and calls onFieldChange immediately with the right value shape', () => {
    const { onFieldChange } = renderBar()
    if (!container) throw new Error('not rendered')

    const sourceSelect = Array.from(container.querySelectorAll('select')).find((el) => el.previousElementSibling?.textContent === 'Source') as HTMLSelectElement
    act(() => {
      sourceSelect.value = 'gmail'
      sourceSelect.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(onFieldChange).toHaveBeenLastCalledWith('source', 'gmail')

    const flagSelect = Array.from(container.querySelectorAll('select')).find((el) => el.previousElementSibling?.textContent === 'Flag') as HTMLSelectElement
    act(() => {
      flagSelect.value = 'yes'
      flagSelect.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(onFieldChange).toHaveBeenLastCalledWith('flag', true)
    act(() => {
      flagSelect.value = 'no'
      flagSelect.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(onFieldChange).toHaveBeenLastCalledWith('flag', false)

    const [minInput, maxInput] = container.querySelectorAll<HTMLInputElement>('.filterRangeInputs input')
    act(() => { setInputValue(minInput, '10') })
    expect(onFieldChange).toHaveBeenLastCalledWith('score', { min: 10, max: null })
    act(() => { setInputValue(maxInput, '90') })
    expect(onFieldChange).toHaveBeenLastCalledWith('score', { min: null, max: 90 })

    const statusCheckbox = container.querySelector<HTMLInputElement>('.filterMultiselectOption input')!
    act(() => {
      statusCheckbox.click()
    })
    expect(onFieldChange).toHaveBeenLastCalledWith('status', ['a'])
  })

  it('debounces the text field: three keystrokes within 300ms call onFieldChange once with the final value', () => {
    vi.useFakeTimers()
    const { onFieldChange } = renderBar()
    if (!container) throw new Error('not rendered')
    const input = container.querySelector<HTMLInputElement>('.inventorySearchField input')!

    act(() => { setInputValue(input, 'J'); vi.advanceTimersByTime(100) })
    act(() => { setInputValue(input, 'Ja'); vi.advanceTimersByTime(100) })
    act(() => { setInputValue(input, 'Jav'); vi.advanceTimersByTime(100) })
    expect(onFieldChange).not.toHaveBeenCalled()

    act(() => { vi.advanceTimersByTime(300) })
    expect(onFieldChange).toHaveBeenCalledTimes(1)
    expect(onFieldChange).toHaveBeenCalledWith('q', 'Jav')
  })

  it('resyncs a debounced text field to an external value change and drops the pending write', () => {
    vi.useFakeTimers()
    const onFieldChange = vi.fn()
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)

    const renderWith = (values: FilterValues) => act(() => {
      root?.render(
        <FilterSortBar fields={fiveFields} values={values} onFieldChange={onFieldChange} onClear={vi.fn()} sortOptions={sortOptions} sortValue="newest" onSortChange={vi.fn()} />,
      )
    })
    // Start from a genuinely non-default external value so the later reset to '' is
    // an actual prop change the field's resync effect can react to.
    renderWith({ ...defaultValues, q: 'initial' })
    const input = container.querySelector<HTMLInputElement>('.inventorySearchField input')!
    // Flush the harmless resync timer the field's mount effect schedules, so it
    // doesn't fire mid-keystroke below and overwrite what we're about to type.
    act(() => { vi.advanceTimersByTime(0) })

    act(() => { setInputValue(input, 'stale'); vi.advanceTimersByTime(100) })
    expect(input.value).toBe('stale')

    // External reset (e.g. Clear filters) changes the `value` prop before the 300ms debounce fires.
    renderWith(defaultValues)
    act(() => { vi.advanceTimersByTime(0) })
    expect(input.value).toBe('')

    act(() => { vi.advanceTimersByTime(300) })
    expect(onFieldChange).not.toHaveBeenCalled()
  })

  it('calls onClear when Clear filters is clicked', () => {
    const { onClear } = renderBar()
    if (!container) throw new Error('not rendered')
    const button = Array.from(container.querySelectorAll('button')).find((el) => el.textContent === 'Clear filters')!
    act(() => button.click())
    expect(onClear).toHaveBeenCalledTimes(1)
  })

  it('renders a muted disabled shell with no interactive fields when disabled', () => {
    renderBar({ disabled: true, disabledMessage: 'No filters here.' })
    if (!container) throw new Error('not rendered')
    expect(container.querySelector('.filterSortBar--disabled')).not.toBeNull()
    expect(container.querySelector('[aria-disabled="true"]')).not.toBeNull()
    expect(container.textContent).toContain('No filters here.')
    expect(container.querySelectorAll('input, select, button').length).toBe(0)
  })

  it('renders fields beyond primaryFieldCount only inside a closed "More filters" disclosure', () => {
    const sixFields: FilterFieldConfig[] = [...fiveFields, { key: 'extra', label: 'Extra', type: 'text' }]
    renderBar({ fields: sixFields, values: { ...defaultValues, extra: '' } })
    if (!container) throw new Error('not rendered')

    const details = container.querySelector<HTMLDetailsElement>('.filterSortMoreToggle')
    expect(details).not.toBeNull()
    expect(details?.open).toBe(false)
    expect(details?.querySelector('summary')?.textContent).toBe('More filters (2)')
    expect(container.textContent).toContain('Extra')

    act(() => { details!.open = true; details!.dispatchEvent(new Event('toggle', { bubbles: true })) })
    expect(details?.open).toBe(true)
  })

  it('toggles filterSortBar--active based on whether any field is non-default', () => {
    renderBar({ values: defaultValues })
    if (!container) throw new Error('not rendered')
    expect(container.querySelector('.filterSortBar--active')).toBeNull()

    root && act(() => {
      root?.render(
        <FilterSortBar fields={fiveFields} values={{ ...defaultValues, source: 'gmail' }} onFieldChange={vi.fn()} onClear={vi.fn()} sortOptions={sortOptions} sortValue="newest" onSortChange={vi.fn()} />,
      )
    })
    expect(container.querySelector('.filterSortBar--active')).not.toBeNull()
  })

  it('renders combobox fields as inputs and marks their values active', () => {
    renderBar({ values: { ...defaultValues, role: 'Java' } })
    if (!container) throw new Error('not rendered')
    expect(container.querySelector<HTMLInputElement>('input[role="combobox"]')?.value).toBe('Java')
    expect(container.querySelector('.filterMultiselectPopover [role="combobox"]')).toBeNull()
    expect(container.querySelector('.filterSortBar--active')).not.toBeNull()
  })

  it('renders the spinner only when loading is true, without disabling any field', () => {
    renderBar({ loading: false })
    if (!container) throw new Error('not rendered')
    expect(container.querySelector('[role="status"]')).toBeNull()

    root && act(() => {
      root?.render(
        <FilterSortBar fields={fiveFields} values={defaultValues} onFieldChange={vi.fn()} onClear={vi.fn()} sortOptions={sortOptions} sortValue="newest" onSortChange={vi.fn()} loading />,
      )
    })
    expect(container.querySelector('[role="status"]')).not.toBeNull()
    expect(container.querySelectorAll('input:disabled, select:disabled, button:disabled').length).toBe(0)
  })
})
