import { useEffect, useId, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react'

import { useFilterOptions } from '../filterOptions'
import type { FilterFieldConfig, FilterValue } from './FilterSortBar'

type Props = {
  field: Extract<FilterFieldConfig, { type: 'combobox' }>
  value?: string
  apiBase: string
  onChange: (key: string, value: FilterValue) => void
}

export default function ComboboxField({ field, value, apiBase, onChange }: Props) {
  const [local, setLocal] = useState(value ?? '')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const root = useRef<HTMLLabelElement>(null)
  const listId = useId()
  const { values, loading, error } = useFilterOptions(apiBase, field.bucket, field.key, local, open)

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    setLocal(value ?? '')
  }, [value])
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  useEffect(() => { setActiveIndex(-1) }, [values])
  useEffect(() => {
    if (!open) return
    const close = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [open])

  const commit = (next: string) => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    setLocal(next)
    setOpen(false)
    setActiveIndex(-1)
    onChange(field.key, next)
  }
  const type = (event: ChangeEvent<HTMLInputElement>) => {
    const next = event.target.value
    setLocal(next)
    setOpen(true)
    setActiveIndex(-1)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => onChange(field.key, next), 300)
  }
  const keyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      setOpen(false)
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setOpen(true)
      if (!values.length) return
      setActiveIndex((current) => event.key === 'ArrowDown'
        ? (current + 1 + values.length) % values.length
        : (current - 1 + values.length) % values.length)
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      commit(activeIndex >= 0 ? values[activeIndex] : local)
    }
  }

  return <label ref={root} className="inventorySearchField filterComboboxField">
    <span>{field.label}</span>
    <div className="filterComboboxShell">
      <input
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        autoComplete="off"
        value={local}
        placeholder={field.placeholder}
        onFocus={() => setOpen(true)}
        onChange={type}
        onKeyDown={keyDown}
        onBlur={() => { if (timer.current) commit(local); else setOpen(false) }}
      />
      {open ? <div className="filterComboboxPanel" id={listId} role="listbox" aria-label={field.label}>
        {loading ? <p className="filterComboboxNote">Loading...</p> : null}
        {!loading && error ? <p className="filterComboboxNote">Couldn&apos;t load suggestions</p> : null}
        {!loading && !error && values.length === 0 ? <p className="filterComboboxNote">No matches - press Enter to use what you typed</p> : null}
        {values.map((option, index) => <button
          type="button"
          key={option}
          id={`${listId}-${index}`}
          role="option"
          aria-selected={index === activeIndex}
          className={`filterComboboxOption${index === activeIndex ? ' is-active' : ''}`}
          onMouseDown={(event) => event.preventDefault()}
          onMouseEnter={() => setActiveIndex(index)}
          onClick={() => commit(option)}
        >{option}</button>)}
      </div> : null}
    </div>
  </label>
}
