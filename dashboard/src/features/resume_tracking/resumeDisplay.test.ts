import { describe, expect, it } from 'vitest'

import { displaySkills, formatVariantLabel } from './resumeDisplay'

describe('formatVariantLabel', () => {
  it('gives the extractor’s domain list one consistent casing', () => {
    // Both of these are real labels from the live library, on resumes that are
    // otherwise the same. Only the casing differed.
    expect(formatVariantLabel('banking, healthcare, telecom, EdTech')).toBe('Banking, Healthcare, Telecom, EdTech')
    expect(formatVariantLabel('Banking, Healthcare, Telecom, EdTech')).toBe('Banking, Healthcare, Telecom, EdTech')
  })

  it('keeps a word that already carries an inner capital', () => {
    // Not in the canonical map, so these survive on the inner-capital rule alone:
    // title-casing them would be a downgrade, not a fix.
    expect(formatVariantLabel('J2EE, PostgreSQL, macOS')).toBe('J2EE, PostgreSQL, macOS')
  })

  it('gives a domain word its accepted spelling, matching the server', () => {
    expect(formatVariantLabel('banking, edtech, saas, iot')).toBe('Banking, EdTech, SaaS, IoT')
  })

  it('capitalises across separators inside a segment', () => {
    expect(formatVariantLabel('healthcare/insurance claims')).toBe('Healthcare/Insurance Claims')
    expect(formatVariantLabel('banking and financial services')).toBe('Banking and Financial Services')
  })

  it('normalises spacing and drops empty segments', () => {
    expect(formatVariantLabel('  banking ,, telecom  ')).toBe('Banking, Telecom')
  })

  it('is safe on nothing at all', () => {
    expect(formatVariantLabel('')).toBe('')
    expect(formatVariantLabel(null)).toBe('')
    expect(formatVariantLabel(undefined)).toBe('')
  })
})

describe('displaySkills', () => {
  it('prefers the curated list', () => {
    expect(displaySkills({ structured_skills: ['Java', 'AWS'], skills_text: 'Python' })).toEqual(['Java', 'AWS'])
  })

  it('falls back to the extracted text, which is the only field the live library has', () => {
    expect(displaySkills({ structured_skills: [], skills_text: 'Java, Spring Boot,  , Microservices' }))
      .toEqual(['Java', 'Spring Boot', 'Microservices'])
  })

  it('returns nothing when the resume has neither', () => {
    expect(displaySkills({ structured_skills: [], skills_text: '' })).toEqual([])
  })
})
