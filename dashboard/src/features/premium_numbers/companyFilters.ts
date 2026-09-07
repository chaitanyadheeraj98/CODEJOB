import type { FilterFieldConfig, FilterValues, SortOption } from '../../components/FilterSortBar'
import { dateParams } from './inventoryFilters'

export const companyFilterFields: FilterFieldConfig[] = [{ key: 'q', label: 'Search', type: 'text' }]

export const companySortOptions: SortOption[] = [
  { value: 'newest', label: 'Recently active' },
  { value: 'oldest', label: 'Least recently active' },
  { value: 'most_contacts', label: 'Most contacts' },
  { value: 'fewest_contacts', label: 'Fewest contacts' },
  { value: 'name', label: 'Domain A-Z' },
]

export const companyDefaultFilterValues: FilterValues = { q: '' }

export function companyFiltersToParams(values: FilterValues) {
  const params: Record<string, string> = {}
  const q = values.q as string
  if (q?.trim()) params.q = q.trim()
  return { ...params, ...dateParams(values) }
}
