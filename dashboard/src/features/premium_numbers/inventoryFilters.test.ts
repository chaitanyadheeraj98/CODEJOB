import { describe, expect, it } from 'vitest'

import { companyFiltersToParams } from './companyFilters'
import { inventoryDefaultFilterValues, inventoryFiltersToParams } from './inventoryFilters'
import { filterSortRegistry, resolveRegistryEntry } from '../../filterSortRegistry'

describe('inventory filters', () => {
  it('stays silent when nothing is set', () => {
    expect(inventoryFiltersToParams(inventoryDefaultFilterValues)).toEqual({})
  })

  // useInventory fetches through inventoryFiltersToParams rather than through the
  // registry entry, so a field the registry declares but this function drops is a
  // control that quietly does nothing - which is what domain, favorite and date
  // did before. A Company Inventory card's jump depends on the domain half.
  it('sends every field the Number Inventory registry entry declares', () => {
    const params = inventoryFiltersToParams({
      ...inventoryDefaultFilterValues,
      q: '  fusion  ',
      status: ['active'],
      category: ['recruiter'],
      source_type: 'gmail',
      score: { min: 10, max: 90 },
      domain: '  fusiongts.com  ',
      favorite: 'favorites_only',
      date: { preset: 'custom', from: '2026-09-01', to: '2026-09-05' },
    })
    expect(params).toEqual({
      q: 'fusion',
      status: 'active',
      category: 'recruiter',
      source_type: 'gmail',
      min_score: '10',
      max_score: '90',
      domain: 'fusiongts.com',
      favorite: 'favorites_only',
      date_filter: 'custom',
      date_from: '2026-09-01',
      date_to: '2026-09-05',
    })

    const entry = resolveRegistryEntry(filterSortRegistry['premium_numbers:inventory'], { resumeAssets: [] })
    const declared = new Set(entry?.fields.map((field) => field.key))
    expect(declared).toEqual(new Set(['q', 'status', 'category', 'source_type', 'score', 'domain', 'favorite', 'date']))
  })

  it('carries the date range on the Company Inventory tab too', () => {
    expect(companyFiltersToParams({ q: '', date: { preset: 'all', from: null, to: null } })).toEqual({})
    expect(companyFiltersToParams({ q: 'acme', date: { preset: 'today', from: null, to: null } })).toEqual({
      q: 'acme',
      date_filter: 'today',
    })
  })
})
