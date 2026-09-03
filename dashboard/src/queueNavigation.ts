import type { FilterFieldConfig, FilterValues } from './components/FilterSortBar'
import type { FilterSortPageConfig } from './filterSortRegistry'
import { parseFilterValuesFromParams } from './useUrlSync'

// What the model asked for. `page` and `tab` name a filterSortRegistry entry;
// `filters` are URL parameter names, not field keys - a range field contributes
// min_/max_ and a daterange contributes date_filter/date_from/date_to.
export type QueueTarget = { page: string; tab?: string | null; filters?: Record<string, string> }

export type DroppedFilter = { key: string; reason: 'unknown_field' | 'invalid_value' }

export type ResolvedQueueTarget = {
  registryKey: string
  values: FilterValues
  dropped: DroppedFilter[]
}

const DATE_PRESETS = ['all', 'today', 'yesterday', 'last_7_days', 'custom']

export function registryKeyFor(target: QueueTarget): string {
  return target.tab ? `${target.page}:${target.tab}` : target.page
}

// Every URL parameter this page legitimately accepts, mapped to the field that
// owns it. config.fields is the allowlist here, exactly as COLUMN_READERS is
// for render_candidate_table: the model picks from it, it never extends it.
function acceptedParams(fields: FilterFieldConfig[]): Map<string, FilterFieldConfig> {
  const accepted = new Map<string, FilterFieldConfig>()
  for (const field of fields) {
    if (field.type === 'range') {
      accepted.set(`min_${field.key}`, field)
      accepted.set(`max_${field.key}`, field)
    } else if (field.type === 'daterange') {
      accepted.set('date_filter', field)
      accepted.set('date_from', field)
      accepted.set('date_to', field)
    } else {
      accepted.set(field.key, field)
    }
  }
  return accepted
}

function valid(field: FilterFieldConfig, paramKey: string, value: string): boolean {
  switch (field.type) {
    case 'text':
    case 'combobox':
      return true
    case 'select':
      return field.options.some((option) => option.value === value)
    case 'multiselect': {
      const options = new Set(field.options.map((option) => option.value))
      return value.split(',').some((item) => options.has(item))
    }
    case 'boolean':
      return value === 'true' || value === 'false'
    case 'range':
      return value !== '' && Number.isFinite(Number(value))
    case 'daterange':
      return paramKey === 'date_filter' ? DATE_PRESETS.includes(value) : value !== ''
    default:
      return false
  }
}

/**
 * Turn a model-supplied queue target into filter state the app can set.
 *
 * Unknown and invalid parameters are dropped *and reported* rather than passed
 * through or silently ignored - the same contract render_candidate_table has
 * for unknown columns. Returns null for a page with no registry entry, so the
 * caller renders nothing rather than navigating somewhere arbitrary.
 */
export function resolveQueueTarget(
  target: QueueTarget,
  config: FilterSortPageConfig | null | undefined,
): ResolvedQueueTarget | null {
  if (!config) return null

  const accepted = acceptedParams(config.fields)
  const dropped: DroppedFilter[] = []
  const params = new URLSearchParams()

  for (const [key, rawValue] of Object.entries(target.filters ?? {})) {
    const field = accepted.get(key)
    if (!field) {
      dropped.push({ key, reason: 'unknown_field' })
      continue
    }
    const value = String(rawValue)
    if (!valid(field, key, value)) {
      dropped.push({ key, reason: 'invalid_value' })
      continue
    }
    params.set(key, value)
  }

  // The coercion itself is parseFilterValuesFromParams', not a second copy of
  // it: a queue opened from chat and the same queue opened from a pasted URL
  // must resolve to identical state or the reload check in the manual pass is
  // meaningless.
  return {
    registryKey: registryKeyFor(target),
    values: parseFilterValuesFromParams(config.fields, config.defaultFilterValues, params),
    dropped,
  }
}
