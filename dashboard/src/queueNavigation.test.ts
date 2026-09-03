import { describe, expect, it } from 'vitest'

import { resolveQueueTarget } from './queueNavigation'
import { filterSortRegistry, resolveRegistryEntry } from './filterSortRegistry'
import type { FilterSortPageConfig } from './filterSortRegistry'
import type { DateRangeValue, RangeValue } from './components/FilterSortBar'

const needsReview = () => resolveRegistryEntry(filterSortRegistry.needs_review, { resumeAssets: [] }) as FilterSortPageConfig

describe('resolveQueueTarget', () => {
  it('applies a known text field', () => {
    const resolved = resolveQueueTarget({ page: 'needs_review', filters: { role: 'Java Developer' } }, needsReview())

    expect(resolved?.registryKey).toBe('needs_review')
    expect(resolved?.values.role).toBe('Java Developer')
    expect(resolved?.dropped).toEqual([])
  })

  // Dropped *and reported*: a filter the app silently ignores is worse than one
  // it refuses, because the user reads the resulting queue as filtered.
  it('drops an unknown field and reports it', () => {
    const resolved = resolveQueueTarget({ page: 'needs_review', filters: { unreplied: 'true' } }, needsReview())

    expect(resolved?.dropped).toEqual([{ key: 'unreplied', reason: 'unknown_field' }])
    expect(resolved?.values.unreplied).toBeUndefined()
  })

  it('drops a select value outside the field options', () => {
    const config = needsReview()
    expect(config.fields.find((item) => item.key === 'source')?.type).toBe('select')

    const resolved = resolveQueueTarget({ page: 'needs_review', filters: { source: 'carrier_pigeon' } }, config)

    expect(resolved?.dropped).toEqual([{ key: 'source', reason: 'invalid_value' }])
    expect(resolved?.values.source).toBe('all')
  })

  it('keeps the valid members of a multiselect and drops one with none', () => {
    const kept = resolveQueueTarget(
      { page: 'needs_review', filters: { sendability: 'sendable,not_a_status' } },
      needsReview(),
    )
    expect(kept?.values.sendability).toEqual(['sendable'])
    expect(kept?.dropped).toEqual([])

    const none = resolveQueueTarget({ page: 'needs_review', filters: { sendability: 'not_a_status' } }, needsReview())
    expect(none?.dropped).toEqual([{ key: 'sendability', reason: 'invalid_value' }])
  })

  it('parses a range through its min_/max_ parameters', () => {
    const resolved = resolveQueueTarget(
      { page: 'needs_review', filters: { min_ats_score: '60', max_ats_score: '90' } },
      needsReview(),
    )

    expect(resolved?.values.ats_score as RangeValue).toEqual({ min: 60, max: 90 })
    expect(resolved?.dropped).toEqual([])
  })

  it('rejects a non-numeric range bound', () => {
    const resolved = resolveQueueTarget(
      { page: 'needs_review', filters: { min_ats_score: 'high' } },
      needsReview(),
    )

    expect(resolved?.dropped).toEqual([{ key: 'min_ats_score', reason: 'invalid_value' }])
  })

  it('parses a daterange preset', () => {
    const resolved = resolveQueueTarget(
      { page: 'needs_review', filters: { date_filter: 'last_7_days' } },
      needsReview(),
    )

    expect((resolved?.values.date as DateRangeValue).preset).toBe('last_7_days')
  })

  it('rejects a daterange preset that is not one of the known ones', () => {
    const resolved = resolveQueueTarget(
      { page: 'needs_review', filters: { date_filter: 'last_fortnight' } },
      needsReview(),
    )

    expect(resolved?.dropped).toEqual([{ key: 'date_filter', reason: 'invalid_value' }])
    expect((resolved?.values.date as DateRangeValue).preset).toBe('all')
  })

  it('builds the tabbed registry key', () => {
    const config = resolveRegistryEntry(filterSortRegistry['premium_numbers:opportunities'], { resumeAssets: [] })
    const resolved = resolveQueueTarget({ page: 'premium_numbers', tab: 'opportunities' }, config)

    expect(resolved?.registryKey).toBe('premium_numbers:opportunities')
  })

  it('returns null for a page with no registry entry', () => {
    expect(resolveQueueTarget({ page: 'atlantis' }, resolveRegistryEntry(filterSortRegistry.atlantis, { resumeAssets: [] }))).toBeNull()
  })
})
