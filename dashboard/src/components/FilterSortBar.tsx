import { useEffect, useRef, useState, type ChangeEvent } from 'react'

export type FilterFieldConfig =
  | { key: string; label: string; type: 'text'; placeholder?: string; helpText?: string }
  | { key: string; label: string; type: 'select' | 'multiselect'; options: Array<{ value: string; label: string }>; helpText?: string }
  | { key: string; label: string; type: 'boolean'; helpText?: string }
  | { key: string; label: string; type: 'range'; min?: number; max?: number; step?: number; helpText?: string }
  | { key: string; label: string; type: 'daterange'; presets?: Array<{ value: string; label: string }>; helpText?: string }
export type RangeValue = { min: number | null; max: number | null }
export type DateRangeValue = { preset: string; from: string | null; to: string | null }
export type FilterValue = string | string[] | boolean | null | RangeValue | DateRangeValue
export type FilterValues = Record<string, FilterValue>
export type SortOption = { value: string; label: string }

type Props = {
  fields: FilterFieldConfig[]; values: FilterValues
  onFieldChange: (key: string, value: FilterValue) => void; onClear: () => void
  sortOptions: SortOption[]; sortValue: string; onSortChange: (value: string) => void
  disabled?: boolean; disabledMessage?: string; primaryFieldCount?: number; loading?: boolean
}

function active(field: FilterFieldConfig, value: FilterValue) {
  if (field.type === 'text') return typeof value === 'string' && value.trim() !== ''
  if (field.type === 'select') return typeof value === 'string' && value !== '' && value !== 'all'
  if (field.type === 'multiselect') return Array.isArray(value) && value.length > 0
  if (field.type === 'boolean') return value === true || value === false
  if (field.type === 'daterange') return !!value && (value as DateRangeValue).preset !== 'all'
  const range = value as RangeValue | undefined
  return !!range && (range.min != null || range.max != null)
}

function allLabel(label: string) {
  const normalized = label.toLowerCase()
  if (normalized.endsWith('status')) return `All ${normalized}es`
  if (normalized.endsWith('category')) return 'All categories'
  return `All ${normalized}`
}

export default function FilterSortBar({ fields, values, onFieldChange, onClear, sortOptions, sortValue, onSortChange, disabled = false, disabledMessage = 'No filters available for this page.', primaryFieldCount = 5, loading = false }: Props) {
  if (disabled) return <div className="inventoryToolbar filterSortBar filterSortBar--disabled" aria-disabled="true"><span>{disabledMessage}</span></div>
  const render = (field: FilterFieldConfig) => <span key={field.key} className={field.helpText ? 'filterFieldWithHint' : undefined} title={field.helpText}><FilterField field={field} value={values[field.key]} onChange={onFieldChange} /></span>
  const advanced = fields.slice(primaryFieldCount)
  return <div className={`inventoryToolbar filterSortBar ${fields.some((field) => active(field, values[field.key])) ? 'filterSortBar--active' : ''}`}>
    {fields.slice(0, primaryFieldCount).map(render)}
    {advanced.length ? <details className="filterSortMoreToggle"><summary>More filters ({advanced.length})</summary><div className="filterSortMoreFields">{advanced.map(render)}</div></details> : null}
    <label><span>Sort</span><select value={sortValue} onChange={(event) => onSortChange(event.target.value)}>{sortOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
    {loading ? <span className="filterSortStatus" role="status"><span className="filterSortSpinner" aria-hidden="true" />Updating…</span> : null}
    <button type="button" className="filterSortClearButton" onClick={onClear}>Clear filters</button>
  </div>
}

function FilterField({ field, value, onChange }: { field: FilterFieldConfig; value: FilterValue; onChange: (key: string, value: FilterValue) => void }) {
  if (field.type === 'text') return <TextField field={field} value={value as string} onChange={onChange} />
  if (field.type === 'select') return <label><span>{field.label}</span><select value={(value as string) ?? 'all'} onChange={(event) => onChange(field.key, event.target.value)}>{field.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
  if (field.type === 'boolean') {
    const current = value === true ? 'yes' : value === false ? 'no' : 'all'
    return <label><span>{field.label}</span><select value={current} onChange={(event) => onChange(field.key, event.target.value === 'all' ? null : event.target.value === 'yes')}><option value="all">All</option><option value="yes">Yes</option><option value="no">No</option></select></label>
  }
  if (field.type === 'range') {
    const current = (value as RangeValue) ?? { min: null, max: null }
    const parse = (raw: string) => raw.trim() === '' ? null : Number(raw)
    return <label className="filterRangeField"><span>{field.label}</span><div className="filterRangeInputs"><input type="number" min={field.min} max={field.max} step={field.step ?? 1} placeholder="Min" value={current.min ?? ''} onChange={(event) => onChange(field.key, { ...current, min: parse(event.target.value) })} /><span>–</span><input type="number" min={field.min} max={field.max} step={field.step ?? 1} placeholder="Max" value={current.max ?? ''} onChange={(event) => onChange(field.key, { ...current, max: parse(event.target.value) })} /></div></label>
  }
  if (field.type === 'daterange') {
    const current = (value as DateRangeValue) ?? { preset: 'all', from: null, to: null }
    const presets = field.presets ?? [{ value: 'all', label: 'Default' }, { value: 'today', label: 'Today' }, { value: 'yesterday', label: 'Yesterday' }, { value: 'last_7_days', label: 'Last 7 days' }, { value: 'custom', label: 'Custom range' }]
    return <label><span>{field.label}</span><select value={current.preset} onChange={(event) => onChange(field.key, { ...current, preset: event.target.value })}>{presets.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>{current.preset === 'custom' ? <span className="filterRangeInputs"><input type="date" aria-label={`${field.label} from`} value={current.from ?? ''} onChange={(event) => onChange(field.key, { ...current, from: event.target.value || null })} /><span>–</span><input type="date" aria-label={`${field.label} to`} value={current.to ?? ''} onChange={(event) => onChange(field.key, { ...current, to: event.target.value || null })} /></span> : null}</label>
  }
  const selected = new Set((value as string[]) ?? [])
  return <label className="filterMultiselectField"><span>{field.label}</span><details className="filterMultiselectPopover"><summary><span>{selected.size ? `${selected.size} selected` : allLabel(field.label)}</span></summary><div className="filterMultiselectOptions" role="group" aria-label={field.label}>{field.options.map((option) => <label key={option.value} className="filterMultiselectOption"><input type="checkbox" checked={selected.has(option.value)} onChange={() => { const next = new Set(selected); if (next.has(option.value)) next.delete(option.value); else next.add(option.value); onChange(field.key, [...next]) }} />{option.label}</label>)}</div></details></label>
}

function TextField({ field, value, onChange }: { field: Extract<FilterFieldConfig, { type: 'text' }>; value?: string; onChange: (key: string, value: FilterValue) => void }) {
  const [local, setLocal] = useState(value ?? '')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => { if (timer.current) clearTimeout(timer.current); const sync = setTimeout(() => setLocal(value ?? ''), 0); return () => clearTimeout(sync) }, [value])
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  return <label className="inventorySearchField"><span>{field.label}</span><input value={local} placeholder={field.placeholder} onChange={(event: ChangeEvent<HTMLInputElement>) => { const next = event.target.value; setLocal(next); if (timer.current) clearTimeout(timer.current); timer.current = setTimeout(() => onChange(field.key, next), 300) }} /></label>
}
