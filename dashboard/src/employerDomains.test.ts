import { describe, expect, it } from 'vitest'

import { addEmployerDomain, removeEmployerDomain } from './employerDomains'

describe('employerDomains helpers', () => {
  it('normalizes and deduplicates domains', () => {
    const first = addEmployerDomain([], ' HorizonSoftTech.Net ')
    expect(first.error).toBeNull()
    expect(first.next).toEqual(['horizonsofttech.net'])

    const second = addEmployerDomain(first.next, 'horizonsofttech.net')
    expect(second.added).toBe(false)
    expect(second.next).toEqual(['horizonsofttech.net'])
  })

  it('rejects invalid domains', () => {
    const invalid = addEmployerDomain([], 'not-a-domain')
    expect(invalid.added).toBe(false)
    expect(invalid.error).toBe('Invalid domain format')
  })

  it('removes domain case-insensitively', () => {
    const next = removeEmployerDomain(['horizonsofttech.net', 'example.com'], 'HorizonSoftTech.Net')
    expect(next).toEqual(['example.com'])
  })
})
