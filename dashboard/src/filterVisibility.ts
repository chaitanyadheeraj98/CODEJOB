import type { FilterFieldConfig, FilterValues } from './components/FilterSortBar'

export const LOCKED_FIELD_KEYS = new Set(['date'])

export function visibleFieldsFor(
  fields: FilterFieldConfig[],
  preference: string[] | undefined,
): FilterFieldConfig[] {
  if (preference === undefined) return fields
  const allowed = new Set(preference)
  return fields.filter((field) => LOCKED_FIELD_KEYS.has(field.key) || allowed.has(field.key))
}

export function narrowValuesToVisible(
  fields: FilterFieldConfig[],
  values: FilterValues,
  defaults: FilterValues,
): FilterValues {
  const visible = new Set(fields.map((field) => field.key))
  let next = values
  for (const key of Object.keys(defaults)) {
    if (!visible.has(key) && values[key] !== defaults[key]) {
      if (next === values) next = { ...values }
      next[key] = defaults[key]
    }
  }
  return next
}

/** True when a free-text or picker filter is set.
 *
 * Mirrors `_text_search_active` in backend/app/main.py: the backend drops the
 * implicit one-day `mail_date` scope for exactly these fields, so the bar can
 * say so. Derived from the field *type* rather than a key list, so it stays
 * correct for every dashboard and for fields added later.
 */
export function hasActiveTextSearch(fields: FilterFieldConfig[], values: FilterValues): boolean {
  return fields.some((field) => {
    if (field.type !== 'text' && field.type !== 'combobox') return false
    const value = values[field.key]
    return typeof value === 'string' && value.trim() !== ''
  })
}
