// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import CandidateTable from './CandidateTable'
import { ChatContext, type ChatContextValue } from './chatContext'
import { PROPOSAL_HANDLERS, proposalResultDetail } from './proposals'
import type { CandidateTableData } from './renderers'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const handler = PROPOSAL_HANDLERS.propose_candidate_action

const fields = (overrides: Record<string, unknown> = {}) => ({
  action: 'propose_candidate_action',
  candidate_action: 'reject',
  label: 'Reject',
  candidate_ids: [11, 12],
  count: 2,
  reversible: false,
  reversible_detail: 'Rejected emails leave the review queue; there is no un-reject action.',
  roles: ['Java Developer'],
  reason: 'Not a fit',
  dropped: [],
  ...overrides,
})

describe('propose_candidate_action handler', () => {
  it.each([
    ['reject', '/candidates/reject-bulk'],
    ['track', '/candidates/track-bulk'],
    ['untrack', '/candidates/track-bulk'],
    ['regenerate', '/candidates/regenerate-bulk'],
    ['send_to_failed_mapping', '/candidates/send-to-failed-mapping-bulk'],
  ])('routes %s to its bulk endpoint', (action, endpoint) => {
    const resolve = handler.endpoint as (f: Record<string, unknown>) => string

    expect(resolve(fields({ candidate_action: action }))).toBe(endpoint)
  })

  // The client never routes to a server-supplied URL: a malformed payload
  // would otherwise aim a POST anywhere.
  it('resolves to an empty endpoint for an action it does not know', () => {
    const resolve = handler.endpoint as (f: Record<string, unknown>) => string

    expect(resolve(fields({ candidate_action: 'wire_money', endpoint: '/evil' }))).toBe('')
  })

  it('sends ids, reason, and the tracked flag only when present', () => {
    expect(handler.buildBody(fields())).toEqual({ ids: [11, 12], reason: 'Not a fit' })
    expect(handler.buildBody(fields({ candidate_action: 'track', reason: '', tracked: true })))
      .toEqual({ ids: [11, 12], tracked: true })
  })

  it('states the count and reversibility on the card', () => {
    const summary = Object.fromEntries(handler.summary(fields()))

    expect(summary.Action).toBe('Reject')
    expect(summary['Affected emails']).toBe('2')
    expect(summary.Reversible).toBe('No')
    expect(summary.Detail).toContain('no un-reject action')
    expect(summary.Roles).toBe('Java Developer')
  })

  it('says Yes for a reversible action', () => {
    const summary = Object.fromEntries(handler.summary(fields({
      candidate_action: 'track', label: 'Mark for tracking', reversible: true,
    })))

    expect(summary.Reversible).toBe('Yes')
  })

  it('reports skipped emails on the card', () => {
    const summary = Object.fromEntries(handler.summary(fields({
      dropped: [{ candidate_id: 13, reason: 'wrong_state' }],
    })))

    expect(summary['Not included']).toBe('1 email(s) skipped')
  })

  it('labels the confirm button with the action and count', () => {
    expect(handler.confirmLabel(fields())).toBe('Reject 2')
  })

  // Every bulk route returns succeeded_ids, so the verb has to come from the
  // proposal - a reject reporting "3 approved" names the wrong action.
  it('reports the result using the proposal\'s own verb', () => {
    expect(proposalResultDetail({ succeeded_ids: [1, 2, 3] }, fields())).toBe('3 reject.')
    expect(proposalResultDetail({ succeeded_ids: [1], failed: [{ id: 2 }] }, fields({ label: 'Rejected' })))
      .toBe('1 rejected; 1 failed.')
    expect(proposalResultDetail({ succeeded_ids: [1] })).toBe('1 approved.')
  })
})

const tableData: CandidateTableData = {
  title: 'Top matches',
  columns: ['role', 'state'],
  rows: [
    { candidate_id: 11, record_id: 'a', role: 'Backend', state: 'needs_review' },
    { candidate_id: 12, record_id: 'b', role: 'Data', state: 'approved_sent' },
  ],
  dropped: [],
  truncated: false,
}

describe('CandidateTable action bar', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const chatStub = (overrides: Partial<ChatContextValue> = {}) => ({
    proposalResults: {},
    proposalBusyId: null,
    approveProposal: vi.fn(),
    cancelProposal: vi.fn(),
    focusCandidate: vi.fn(),
    ...overrides,
  }) as unknown as ChatContextValue

  const render = (chat: ChatContextValue) => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(
      <ChatContext.Provider value={chat}>
        <CandidateTable messageId={99} data={tableData} />
      </ChatContext.Provider>,
    ))
    return container
  }

  // Scoped to the selection bar: the confirmation card's own confirm button
  // carries the action's label too ("Reject 1"), so an unscoped text match
  // would find whichever rendered first.
  const barButton = (label: string) => Array.from(container!.querySelectorAll('.candidateTableActions button'))
    .find((node) => node.textContent?.trim().startsWith(label)) as HTMLButtonElement | undefined

  const cardButtons = () => Array.from(
    container!.querySelectorAll('.chatProposalActions button'),
  ) as HTMLButtonElement[]

  const select = (el: HTMLElement) => act(() => {
    (el.querySelector('input[type="checkbox"]') as HTMLInputElement).click()
  })

  it('offers reject and track beside approve on a selection', () => {
    select(render(chatStub()))

    expect(barButton('Approve')).toBeTruthy()
    expect(barButton('Reject')).toBeTruthy()
    expect(barButton('Track')).toBeTruthy()
  })

  it('builds a reject proposal from the selection', () => {
    const approveProposal = vi.fn()
    const el = render(chatStub({ approveProposal }))
    select(el)
    act(() => { barButton('Reject')?.click() })

    expect(el.textContent).toContain('Rejected emails leave the review queue')

    act(() => { cardButtons()[0].click() })
    const [, proposal] = approveProposal.mock.calls[0]
    expect(proposal.fields.candidate_action).toBe('reject')
    expect(proposal.fields.candidate_ids).toEqual([11])
  })

  // F8: reject-bulk has no idempotency key, so the guard is the card itself.
  // The second click must be impossible, not merely harmless.
  it('disables both card buttons while any proposal is in flight', () => {
    const el = render(chatStub({ proposalBusyId: 99 }))
    select(el)
    act(() => { barButton('Reject')?.click() })

    const [confirm, cancel] = cardButtons()
    expect(confirm.disabled).toBe(true)
    expect(cancel.disabled).toBe(true)
    expect(confirm.textContent).toContain('Working')
  })

  it('leaves approve routed to its own idempotent endpoint', () => {
    const approveProposal = vi.fn()
    const el = render(chatStub({ approveProposal }))
    select(el)
    act(() => { barButton('Approve')?.click() })
    act(() => { cardButtons()[0].click() })

    const [, proposal] = approveProposal.mock.calls[0]
    expect(proposal.fields.action).toBe('approve_candidates')
    expect(proposal.fields.idempotency_key).toBeTruthy()
  })
})
