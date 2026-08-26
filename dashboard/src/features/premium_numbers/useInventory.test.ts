import { describe, expect, it } from 'vitest'

import type { EmployerNumberCard, NumberReviewCard, RecruiterNumberCard } from './types'
import { mergeContacts, reviewRow } from './useInventory'

const baseContact = {
  id: 7,
  normalized_phone_number: '12145550101',
  display_phone_number: '+1 (214) 555-0101',
  company: 'Shared Co',
  source_type: 'gmail' as const,
  source_id: 10,
  source_link_url: null,
  active_lead_id: 20,
  version_count: 2,
  seen_count: 3,
  recruiter_relevance_score: 92,
  created_at: '2026-08-18T10:00:00Z',
  updated_at: '2026-08-18T12:00:00Z',
}

describe('inventory normalization', () => {
  it('deduplicates a shared contact and preserves both role badges and flagged state', () => {
    const recruiter: RecruiterNumberCard = {
      ...baseContact,
      recruiter_name: 'Rita',
      designation: 'Recruiter',
      recruiter_email: 'rita@example.com',
      first_detected_email_id: 10,
      linkedin_url: '',
      recruiter_verification_level: 'unverified',
      do_not_work_again: false,
      do_not_work_again_reason: '',
      total_opportunity_count: 3,
      last_email_received_at: null,
      is_recruiter: true,
      is_employer: true,
      status: 'Active',
      flagged: false,
    }
    const employer: EmployerNumberCard = {
      ...baseContact,
      owner_name: 'Hiring Desk',
      employer_email: '',
      source_email_id: 10,
      is_recruiter: true,
      is_employer: true,
      status: 'Flagged',
      flagged: true,
    }

    const rows = mergeContacts([recruiter], [employer])
    expect(rows).toHaveLength(1)
    expect(rows[0].key).toBe('contact:7')
    expect(rows[0].categories).toEqual(['Recruiter', 'Employer'])
    expect(rows[0].status).toBe('Flagged')
  })

  it('normalizes a pending Nvoids review with a composite selection key', () => {
    const review = {
      id: 9,
      source_email_id: null,
      source_external_opportunity_id: 44,
      source_lead_id: 2,
      normalized_phone_number: '12145550202',
      display_phone_number: '+1 (214) 555-0202',
      owner_name: 'Unknown',
      company: 'Unknown',
      designation: 'Unknown',
      confidence: 'low',
      purpose: 'Unknown',
      evidence_snippet: '',
      email_subject: '',
      email_sender: '',
      contact_email: '',
      contact_type: 'unknown',
      recruiter_relevance_score: 0,
      relevance_reason: '',
      extraction_source: 'rules',
      scored_with: 'current',
      gmail_open_url: '',
      state: 'pending',
      role: null,
      reason_code: 'new_number',
      occurrence_count: 1,
      created_at: '2026-08-18T10:00:00Z',
      updated_at: '2026-08-18T12:00:00Z',
    } satisfies NumberReviewCard

    expect(reviewRow(review)).toMatchObject({ key: 'review:9', status: 'Pending', sourceType: 'nvoids', score: null })
  })
})
