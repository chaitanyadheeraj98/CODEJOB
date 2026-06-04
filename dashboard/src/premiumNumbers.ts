export type PremiumScope = 'all_review' | 'recruiter_numbers' | 'employer_numbers' | 'recruiter_opportunities'

export type PremiumPageMeta = {
  nextCursor: number | null
  hasNext: boolean
}

export type PremiumPageMetaMap = Record<PremiumScope, PremiumPageMeta>

export function defaultPremiumPageMeta(): PremiumPageMetaMap {
  return {
    all_review: { nextCursor: null, hasNext: false },
    recruiter_numbers: { nextCursor: null, hasNext: false },
    employer_numbers: { nextCursor: null, hasNext: false },
    recruiter_opportunities: { nextCursor: null, hasNext: false },
  }
}

type BuildPremiumScopeUrlArgs = {
  apiBase: string
  scope: PremiumScope
  cursor: number
  limit: number
  q: string
  mailDate: string | null
  opportunityStatus: 'all' | string
  opportunitySource: 'all' | 'gmail' | 'nvoids'
}

export function buildPremiumScopeUrl(args: BuildPremiumScopeUrlArgs): string {
  const params = new URLSearchParams({
    cursor: String(args.cursor),
    limit: String(args.limit),
  })
  const query = args.q.trim()
  if (query) params.set('q', query)
  if (args.mailDate) params.set('mail_date', args.mailDate)

  if (args.scope === 'recruiter_opportunities') {
    if (args.opportunityStatus !== 'all') params.set('status', args.opportunityStatus)
    if (args.opportunitySource !== 'all') params.set('source_type', args.opportunitySource)
    return `${args.apiBase}/recruiter-opportunities?${params.toString()}`
  }

  if (args.scope === 'all_review') return `${args.apiBase}/number-review?${params.toString()}`
  if (args.scope === 'recruiter_numbers') return `${args.apiBase}/recruiter-numbers?${params.toString()}`
  return `${args.apiBase}/employer-numbers?${params.toString()}`
}
