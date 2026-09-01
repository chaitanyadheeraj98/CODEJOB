import type { FilterFieldConfig, FilterValues } from './components/FilterSortBar'
import type { FilterSortPageConfig } from './filterSortRegistry'

export function parseFilterValuesFromParams(fields: FilterFieldConfig[], defaults: FilterValues, params: URLSearchParams): FilterValues {
  const values = { ...defaults }
  for (const field of fields) {
    const value = params.get(field.key)
    if ((field.type === 'text' || field.type === 'combobox') && value != null) values[field.key] = value
    else if (field.type === 'select' && value != null && field.options.some((option) => option.value === value)) values[field.key] = value
    else if (field.type === 'multiselect' && value != null) { const valid = new Set(field.options.map((option) => option.value)); values[field.key] = value.split(',').filter((item) => valid.has(item)) }
    else if (field.type === 'boolean' && (value === 'true' || value === 'false')) values[field.key] = value === 'true'
    else if (field.type === 'range') { const min = params.get(`min_${field.key}`); const max = params.get(`max_${field.key}`); values[field.key] = { min: min != null && Number.isFinite(Number(min)) ? Number(min) : null, max: max != null && Number.isFinite(Number(max)) ? Number(max) : null } }
    else if (field.type === 'daterange') { const preset = params.get('date_filter') ?? 'all'; values[field.key] = { preset: ['all', 'today', 'yesterday', 'last_7_days', 'custom'].includes(preset) ? preset : 'all', from: params.get('date_from'), to: params.get('date_to') } }
  }
  return values
}

export function buildUrlSearch(page: string, tab: string | null, sort: string, values: FilterValues, config: FilterSortPageConfig, pagination: number): string {
  const params = new URLSearchParams({ page })
  if (tab) params.set('tab', tab)
  if (sort) params.set('sort', sort)
  if (pagination) params.set(config.paginationParamName ?? 'cursor', String(pagination))
  Object.entries(config.toParams(values)).forEach(([key, value]) => params.set(key, value))
  return params.toString()
}
