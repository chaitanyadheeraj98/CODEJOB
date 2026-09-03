import { describe, expect, it } from 'vitest'

import { needsReviewDefaultFilterValues, needsReviewFilterFields, needsReviewFiltersToParams } from './needsReviewFilters'

describe('needs review filters', () => {
  it('offers a To field and one field per card badge', () => {
    const keys = needsReviewFilterFields().map((field) => field.key)
    expect(keys).toContain('recipient')
    expect(keys).toEqual(expect.arrayContaining(['ats_strength', 'contact_status', 'verification', 'following']))
    // The pre-existing fields keep their identity and order.
    expect(keys.slice(0, 2)).toEqual(['sender', 'recipient'])
    expect(keys).toContain('ats_score')
  })

  it('sends the new filters as params and stays silent when unset', () => {
    expect(needsReviewFiltersToParams(needsReviewDefaultFilterValues)).toEqual({})
    expect(
      needsReviewFiltersToParams({
        ...needsReviewDefaultFilterValues,
        recipient: ' recruiter@agency.example ',
        ats_strength: ['strong', 'unknown'],
        contact_status: ['flagged'],
        verification: ['trusted'],
        following: ['bookmarked', 'active'],
      }),
    ).toEqual({
      recipient: 'recruiter@agency.example',
      ats_strength: 'strong,unknown',
      contact_status: 'flagged',
      verification: 'trusted',
      following: 'bookmarked,active',
    })
  })
})
