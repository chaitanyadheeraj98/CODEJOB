// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

import Provenance from './Provenance'
import Unavailable from './Unavailable'
import { asProvenance, renderForMessage, type CoverageEntry, type ProvenanceData } from './renderers'
import type { ChatMessage } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const base: ProvenanceData = {
  metric: 'Candidate states',
  source: 'get_chart/candidate_states',
  row_count: 8836,
  date_range: { from: null, to: null },
  filters: {},
  assumptions: [],
  coverage: [],
}

const complete: CoverageEntry = {
  field: 'recruiter_emails.state', label: 'Candidate state',
  populated: 8836, total: 8836, percent: 100, complete: true,
}
const partial: CoverageEntry = {
  field: 'recruiter_opportunities.end_client', label: 'End client',
  populated: 49, total: 1117, percent: 4.4, complete: false,
}

let container: HTMLDivElement | null = null
let root: Root | null = null

function render(node: React.ReactNode): HTMLDivElement {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => { root!.render(node) })
  return container
}

afterEach(() => {
  act(() => { root?.unmount() })
  container?.remove()
  container = null
  root = null
})

describe('Provenance coverage', () => {
  it('shows a complete column in the quiet caption and raises no warning', () => {
    const el = render(<Provenance data={{ ...base, coverage: [complete] }} />)

    expect(el.querySelector('.chatCoverageWarning')).toBeNull()
    expect(el.querySelector('.chatProvenance')!.textContent)
      .toContain('Candidate state 8,836 of 8,836 records (100%)')
  })

  it('lifts an incomplete column out of the caption into a warning', () => {
    const el = render(<Provenance data={{ ...base, coverage: [partial] }} />)

    const warning = el.querySelector('.chatCoverageWarning')
    expect(warning).not.toBeNull()
    // The figures, the shortfall, and what the shortfall means - a warning that
    // only says "incomplete" leaves the reader to guess the size of the problem.
    expect(warning!.textContent).toContain('49 of 1,117 records (4.4%)')
    expect(warning!.textContent).toContain('1,068')
    expect(warning!.textContent).toContain('not what happened')
  })

  it('warns about the incomplete column while still reporting the complete one', () => {
    const el = render(<Provenance data={{ ...base, coverage: [partial, complete] }} />)

    expect(el.querySelector('.chatCoverageWarning')!.textContent).toContain('End client')
    expect(el.querySelector('.chatCoverageWarning')!.textContent).not.toContain('Candidate state')
    expect(el.querySelector('.chatProvenance')!.textContent).toContain('Candidate state')
  })

  it('renders the incumbent caption unchanged when nothing declares coverage', () => {
    const el = render(<Provenance data={base} />)

    expect(el.querySelector('.chatCoverageWarning')).toBeNull()
    expect(el.querySelector('.chatCoverageComplete')!.textContent).toContain('8,836 rows read')
  })

  it('survives a payload with no coverage key at all', () => {
    // An older backend, or a hand-built fixture. A caption that throws takes the
    // whole message down with it, and an older payload is not an error.
    const legacy = { ...base } as Partial<ProvenanceData>
    delete legacy.coverage

    expect(() => render(<Provenance data={legacy as ProvenanceData} />)).not.toThrow()
  })
})

describe('asProvenance coverage projection', () => {
  it('reads a well-formed coverage array', () => {
    const parsed = asProvenance({
      metric: 'm', source: 's', row_count: 1, date_range: {}, filters: {}, assumptions: [],
      coverage: [{ field: 'a.b', label: 'B', populated: 2, total: 4, percent: 50, complete: false }],
    })

    expect(parsed!.coverage).toEqual([
      { field: 'a.b', label: 'B', populated: 2, total: 4, percent: 50, complete: false },
    ])
  })

  it('treats a missing complete flag as complete rather than inventing a warning', () => {
    const parsed = asProvenance({
      metric: 'm', source: 's', row_count: 1, date_range: {}, filters: {}, assumptions: [],
      coverage: [{ field: 'a.b', label: 'B', populated: 4, total: 4, percent: 100 }],
    })

    // A warning is a claim. Do not manufacture one from an absent field.
    expect(parsed!.coverage[0].complete).toBe(true)
  })

  it('drops entries without usable counts instead of rendering zeroes', () => {
    const parsed = asProvenance({
      metric: 'm', source: 's', row_count: 1, date_range: {}, filters: {}, assumptions: [],
      coverage: [{ field: 'a.b', label: 'B' }, 'nonsense', null],
    })

    expect(parsed!.coverage).toEqual([])
  })
})

describe('Unavailable', () => {
  it('routes a server refusal to the unavailable renderer instead of drawing nothing', () => {
    const message = {
      role: 'tool', tool_name: 'get_chart', content: JSON.stringify({
        unavailable: true,
        subject: 'the prime_vendor chart',
        blocked_fields: [{ field: 'prime_vendor', label: 'Prime vendor', reason: 'Nothing has ever written to it.' }],
      }),
    } as unknown as ChatMessage

    const payload = renderForMessage(message)

    expect(payload?.kind).toBe('unavailable')
  })

  it('names the missing data without echoing the raw column id at the reader', () => {
    const el = render(<Unavailable data={{
      subject: 'the prime_vendor chart',
      blocked: [{ field: 'prime_vendor', label: 'Prime vendor', reason: 'Nothing has ever written to it.' }],
    }} />)

    const text = el.querySelector('.chatUnavailable')!.textContent!
    expect(text).toContain('No chart drawn')
    expect(text).toContain('Prime vendor')
    expect(text).not.toContain('prime_vendor')
  })

  it('states the reason, which describes the system and not the world', () => {
    const el = render(<Unavailable data={{
      subject: 'the vendor chart',
      blocked: [{
        field: 'prime_vendor', label: 'Prime vendor',
        reason: 'The column exists but nothing has ever written to it. It does not mean there are no prime vendors.',
      }],
    }} />)

    expect(el.textContent).toContain('does not mean there are no prime vendors')
  })
})


describe('W11 chart coverage reaches the reader', () => {
  it('warns with the normalized share, not the populated one', () => {
    // The real payload from get_chart/location_by_work_mode. `location` is
    // populated on 98.9% of rows; 50.1% of them name a place. The chart reports
    // the second, because a map captioned 98.9% would be true and misleading.
    const el = render(<Provenance data={{
      ...base,
      metric: 'Where the roles are (every work mode)',
      row_count: 560,
      coverage: [
        { field: 'recruiter_opportunities.location', label: 'Location (as a place)',
          populated: 560, total: 1117, percent: 50.1, complete: false },
        { field: 'recruiter_opportunities.work_mode', label: 'Work mode (incl. derived)',
          populated: 874, total: 1117, percent: 78.2, complete: false },
      ],
    }} />)

    const warning = el.querySelector('.chatCoverageWarning')!
    expect(warning.textContent).toContain('560 of 1,117 records (50.1%)')
    expect(warning.textContent).toContain('874 of 1,117 records (78.2%)')
    // Both shortfalls, each with what it means.
    expect(warning.querySelectorAll('li')).toHaveLength(2)
    expect(warning.textContent).toContain('557')
  })
})
