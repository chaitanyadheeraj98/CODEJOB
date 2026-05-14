import { describe, expect, it } from 'vitest'

import { getOverallVerdict } from './App'

const baseCandidate = {
  ai_score: 0,
  routing_confidence: 0,
  draft_resume_context_status: 'limited',
  recipient_email: 'to@example.com',
  cc_email: 'cc@example.com',
  draft_ai_error: null as string | null,
}

describe('overall verdict scoring', () => {
  it('returns Excellent for strong, fully supported signals', () => {
    const verdict = getOverallVerdict(
      {
        ...baseCandidate,
        ai_score: 1,
        routing_confidence: 1,
        draft_resume_context_status: 'injected',
      },
      'Non-empty draft',
      true,
    )

    expect(verdict.score).toBe(100)
    expect(verdict.label).toBe('Excellent')
  })

  it('applies missing resume context penalty', () => {
    const verdict = getOverallVerdict(
      {
        ...baseCandidate,
        ai_score: 0.4,
        routing_confidence: 0.5,
        draft_resume_context_status: 'missing_resume',
      },
      'Non-empty draft',
      true,
    )

    expect(verdict.score).toBe(66)
    expect(verdict.label).toBe('Risky')
  })

  it('applies recipient/cc and empty draft penalties', () => {
    const verdict = getOverallVerdict(
      {
        ...baseCandidate,
        ai_score: 0.6,
        routing_confidence: 0.6,
        draft_resume_context_status: 'injected',
        recipient_email: null,
        cc_email: null,
      },
      '   ',
      true,
    )

    expect(verdict.score).toBe(78)
    expect(verdict.label).toBe('Review')
  })

  it('applies ai fallback error penalty', () => {
    const withError = getOverallVerdict(
      {
        ...baseCandidate,
        ai_score: 0.4,
        routing_confidence: 0.4,
        draft_resume_context_status: 'injected',
        draft_ai_error: 'fallback used',
      },
      'Non-empty draft',
      true,
    )
    const withoutError = getOverallVerdict(
      {
        ...baseCandidate,
        ai_score: 0.4,
        routing_confidence: 0.4,
        draft_resume_context_status: 'injected',
        draft_ai_error: null,
      },
      'Non-empty draft',
      true,
    )

    expect(withoutError.score - withError.score).toBe(6)
  })

  it('maps scores to expected boundary bands', () => {
    expect(getOverallVerdict(baseCandidate, '', false).label).toBe('Risky')

    expect(
      getOverallVerdict(
        { ...baseCandidate, ai_score: 0.3, routing_confidence: 0.4, draft_resume_context_status: 'limited' },
        'ok',
        true,
      ).label,
    ).toBe('Review')

    expect(
      getOverallVerdict(
        { ...baseCandidate, ai_score: 0.5, routing_confidence: 0.6, draft_resume_context_status: 'limited' },
        'ok',
        true,
      ).label,
    ).toBe('Good')

    expect(
      getOverallVerdict(
        { ...baseCandidate, ai_score: 0.7, routing_confidence: 0.65, draft_resume_context_status: 'limited' },
        'ok',
        true,
      ).label,
    ).toBe('Strong')
  })
})
