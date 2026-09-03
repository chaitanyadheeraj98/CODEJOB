// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import CandidateTable from './CandidateTable'
import CandidateTableCompact from './CandidateTableCompact'
import { ChatContext, type ChatContextValue } from './chatContext'
import type { CandidateTableData } from './renderers'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const data: CandidateTableData = {
  title: 'Top matches',
  columns: ['role', 'ats_score', 'state'],
  rows: [
    { candidate_id: 11, record_id: 'a', role: 'Backend Engineer', ats_score: 61.5, state: 'needs_review' },
    { candidate_id: 12, record_id: 'b', role: 'Data Engineer', ats_score: 76.25, state: 'needs_review' },
    { candidate_id: 13, record_id: 'c', role: 'Analyst', ats_score: null, state: 'approved_sent' },
  ],
  dropped: [],
  truncated: false,
}

const chatStub = (overrides: Partial<ChatContextValue> = {}) => ({
  proposalResults: {},
  proposalBusyId: null,
  approveProposal: vi.fn(),
  cancelProposal: vi.fn(),
  focusCandidate: vi.fn(),
  ...overrides,
}) as unknown as ChatContextValue

describe('CandidateTable', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (chat: ChatContextValue, table: CandidateTableData = data) => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(
      <ChatContext.Provider value={chat}>
        <CandidateTable messageId={99} data={table} />
      </ChatContext.Provider>,
    ))
    return container
  }

  const cellText = (columnIndex: number) => Array.from(
    container!.querySelectorAll('tbody tr'),
  ).map((row) => row.children[columnIndex].textContent)

  const buttonLabelled = (text: string) => Array.from(
    container!.querySelectorAll<HTMLButtonElement>('button'),
  ).find((button) => button.textContent?.includes(text))

  it('renders every row and column with a readable placeholder for empty cells', () => {
    render(chatStub())

    expect(container?.textContent).toContain('Top matches')
    expect(cellText(1)).toEqual(['Backend Engineer', 'Data Engineer', 'Analyst'])
    // ats_score null renders as a dash, not 0 and not "null".
    expect(cellText(2)).toEqual(['61.50', '76.25', '—'])
  })

  it('sorts a column ascending then descending', () => {
    render(chatStub())
    const header = buttonLabelled('ATS score')!

    act(() => header.click())
    expect(cellText(1)).toEqual(['Backend Engineer', 'Data Engineer', 'Analyst'])

    act(() => header.click())
    // Descending by score, but the unscored row stays last either way: nobody
    // scored it, which is not the same as scoring worst.
    expect(cellText(1)).toEqual(['Analyst', 'Data Engineer', 'Backend Engineer'])
  })

  it('opens a record through the focus seam rather than navigating itself', () => {
    const chat = chatStub()
    render(chat)

    act(() => container!.querySelectorAll<HTMLButtonElement>('.candidateTableOpen')[1].click())

    expect(chat.focusCandidate).toHaveBeenCalledWith(12)
  })

  it('turns a selection into a confirmation card and executes nothing before the click', () => {
    const chat = chatStub()
    render(chat)

    act(() => (container!.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]')[0]).click())
    act(() => (container!.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]')[1]).click())
    act(() => buttonLabelled('Approve')!.click())

    expect(chat.approveProposal).not.toHaveBeenCalled()
    expect(container?.textContent).toContain('Confirm action')

    act(() => buttonLabelled('Approve 2 Emails')!.click())
    expect(chat.approveProposal).toHaveBeenCalledTimes(1)

    const [messageId, proposal] = (chat.approveProposal as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(messageId).toBe(99)
    expect(proposal.fields.candidate_ids).toEqual([11, 12])
    expect(typeof proposal.fields.idempotency_key).toBe('string')
  })

  // A second click on a table selection is far likelier than on a single card,
  // and approve-bulk dedupes on this key.
  it('keeps one idempotency key for the life of a pending action', () => {
    const chat = chatStub()
    render(chat)

    act(() => (container!.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]')[0]).click())
    act(() => buttonLabelled('Approve')!.click())
    act(() => buttonLabelled('Approve 1 Emails')!.click())
    act(() => buttonLabelled('Approve 1 Emails')!.click())

    const calls = (chat.approveProposal as ReturnType<typeof vi.fn>).mock.calls
    expect(calls).toHaveLength(2)
    expect(calls[0][1].fields.idempotency_key).toBe(calls[1][1].fields.idempotency_key)
  })

  it('returns to a selectable table after a cancel', () => {
    const chat = chatStub()
    render(chat)

    act(() => (container!.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]')[0]).click())
    act(() => buttonLabelled('Approve')!.click())
    act(() => buttonLabelled('Cancel')!.click())

    expect(container?.textContent).not.toContain('Confirm action')
    expect(chat.approveProposal).not.toHaveBeenCalled()

    // Selectable again. Recording a cancelled *result* would have keyed it to
    // this table's own message id and frozen the table for good.
    act(() => (container!.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]')[1]).click())
    expect(buttonLabelled('Approve')?.disabled).toBe(false)
  })

  it('will not offer to approve a row that is not in Needs Review', () => {
    const chat = chatStub()
    render(chat)

    act(() => (container!.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]')[2]).click())

    expect(buttonLabelled('Approve')?.disabled).toBe(true)
  })

  it('reports rows the server refused to render', () => {
    render(chatStub(), { ...data, truncated: true, dropped: [{ candidate_id: 91, reason: 'not_found' }] })

    expect(container?.textContent).toContain('1 not shown: 91')
    expect(container?.textContent).toContain('Showing the first 3 candidates')
  })

  it('says so plainly when the table is empty', () => {
    render(chatStub(), { ...data, rows: [] })

    expect(container?.textContent).toContain('No candidates matched')
    expect(container?.querySelector('table')).toBeNull()
  })
})

describe('CandidateTableCompact', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (table: CandidateTableData = data) => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<CandidateTableCompact data={table} />))
    return container
  }

  it('shows the same rows with no controls at all', () => {
    render()

    expect(container?.textContent).toContain('Backend Engineer')
    expect(container?.textContent).toContain('Data Engineer')
    expect(container?.querySelectorAll('button')).toHaveLength(0)
    expect(container?.querySelectorAll('input')).toHaveLength(0)
    expect(container?.querySelectorAll('a')).toHaveLength(0)
  })

  it('points at the workspace when it cannot show everything', () => {
    render({ ...data, truncated: true })

    expect(container?.textContent).toContain('Open the Assistant page')
  })
})
