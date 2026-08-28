import type { FilterFieldConfig, FilterValues, SortOption } from './components/FilterSortBar'
import { needsReviewFilterFields as nFields, needsReviewSortOptions as nSort, needsReviewDefaultFilterValues as nDefaults, needsReviewFiltersToParams as nParams } from './needsReviewFilters'
import { failedMappingFilterFields as fFields, failedMappingSortOptions as fSort, failedMappingDefaultFilterValues as fDefaults, failedMappingFiltersToParams as fParams } from './failedMappingFilters'
import { sentItemsFilterFields as sFields, sentItemsSortOptions as sSort, sentItemsDefaultFilterValues as sDefaults, sentItemsFiltersToParams as sParams } from './sentItemsFilters'
import { inboxFilterFields as iFields, inboxSortOptions as iSort, inboxDefaultFilterValues as iDefaults, inboxFiltersToParams as iParams } from './inboxFilters'
import { inventoryDefaultFilterValues, inventoryFilterFields, inventoryFiltersToParams, inventorySortOptions } from './features/premium_numbers/inventoryFilters'
import { opportunityDefaultFilterValues, opportunityFilterFields, opportunityFiltersToParams, opportunitySortOptions } from './features/premium_numbers/opportunityFilters'
import { submissionDefaultFilterValues, submissionFilterFields, submissionFiltersToParams, submissionSortOptions } from './features/resume_tracking/submissionFilters'
import type { ResumeAssetOption } from './features/premium_numbers/types'

const dateField: FilterFieldConfig = { key: 'date', label: 'Date', type: 'daterange' }
const dateDefault = { preset: 'all', from: null, to: null }
const withDate = (params: Record<string, string>, values: FilterValues) => {
  const value = values.date as typeof dateDefault | undefined
  if (value && value.preset !== 'all') {
    params.date_filter = value.preset
    if (value.preset === 'custom') {
      if (value.from) params.date_from = value.from
      if (value.to) params.date_to = value.to
    }
  }
  return params
}
const config = (bucket: string, fields: FilterFieldConfig[], sortOptions: SortOption[], defaults: FilterValues, toParams: (values: FilterValues) => Record<string, string>, paginationParamName: 'cursor' | 'page' = 'cursor'): FilterSortPageConfig => ({ bucket, fields: [...fields, dateField], sortOptions, defaultFilterValues: { ...defaults, date: dateDefault }, toParams: (values) => withDate(toParams(values), values), paginationParamName })

export type FilterSortPageConfig = { bucket: string; fields: FilterFieldConfig[]; sortOptions: SortOption[]; defaultFilterValues: FilterValues; toParams: (values: FilterValues) => Record<string, string>; paginationParamName?: 'cursor' | 'page'; fromParams?: (params: URLSearchParams) => FilterValues }
export type RegistryContext = { resumeAssets: ResumeAssetOption[] }
export type FilterSortRegistryEntry = FilterSortPageConfig | ((context: RegistryContext) => FilterSortPageConfig)
export const resolveRegistryEntry = (entry: FilterSortRegistryEntry | undefined, context: RegistryContext) => typeof entry === 'function' ? entry(context) : entry

export const filterSortRegistry: Partial<Record<string, FilterSortRegistryEntry>> = {
  needs_review: config('needs_review', nFields, nSort, nDefaults, nParams),
  failed_mapping: config('failed', fFields, fSort, fDefaults, fParams),
  sent_items: config('approved_sent', sFields, sSort, sDefaults, sParams),
  inbox: config('inbox_conversations', iFields, iSort, iDefaults, iParams),
  'premium_numbers:inventory': config('premium_inventory', [...inventoryFilterFields, { key: 'domain', label: 'Domain', type: 'text' }, { key: 'favorite', label: 'Favorite', type: 'select', options: [{ value: 'all', label: 'All' }, { value: 'favorites_only', label: 'Favorites only' }, { value: 'non_favorites_only', label: 'Non-favorites only' }] }], inventorySortOptions, { ...inventoryDefaultFilterValues, domain: '', favorite: 'all' }, (values) => { const params = inventoryFiltersToParams(values); if (String(values.domain || '').trim()) params.domain = String(values.domain).trim(); if (values.favorite !== 'all') params.favorite = String(values.favorite); return params }, 'page'),
  'premium_numbers:opportunities': (context) => config(
    'recruiter_opportunities',
    [
      ...opportunityFilterFields,
      { key: 'q', label: 'Search', type: 'text' },
      { key: 'status', label: 'Status', type: 'text' },
      { key: 'source_type', label: 'Source', type: 'select', options: [{ value: 'all', label: 'All' }, { value: 'gmail', label: 'Gmail' }, { value: 'nvoids', label: 'Nvoids' }] },
      { key: 'resume_fit', label: 'Resume fit', type: 'select', options: [{ value: 'all', label: 'All resumes' }, ...context.resumeAssets.map((resume) => ({ value: String(resume.id), label: resume.file_name }))] },
    ],
    [...opportunitySortOptions, { value: 'resume_fit', label: 'Best match for resume' }],
    { ...opportunityDefaultFilterValues, q: '', status: '', source_type: 'all', resume_fit: 'all' },
    (values) => {
      const params = opportunityFiltersToParams(values)
      if (String(values.q || '').trim()) params.q = String(values.q).trim()
      if (values.status && values.status !== 'all') params.status = String(values.status)
      if (values.source_type && values.source_type !== 'all') params.source_type = String(values.source_type)
      if (values.resume_fit && values.resume_fit !== 'all') params.resume_asset_id = String(values.resume_fit)
      return params
    },
    'page',
  ),
  'resume_tracking:resumes': config('resume_assets', submissionFilterFields, submissionSortOptions, submissionDefaultFilterValues, submissionFiltersToParams, 'page'),
  'resume_tracking:submissions': config('applications', submissionFilterFields, submissionSortOptions, submissionDefaultFilterValues, submissionFiltersToParams, 'page'),
  'application_tracking:bookmarked': config('appts_bookmarked', nFields, nSort, nDefaults, nParams, 'page'),
  'application_tracking:tracked': config('appts_applications', submissionFilterFields, submissionSortOptions, submissionDefaultFilterValues, submissionFiltersToParams, 'page'),
}
