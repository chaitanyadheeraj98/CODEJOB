// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ProposalCard from './ProposalCard'
import { PROPOSAL_HANDLERS } from './proposals'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const handler = PROPOSAL_HANDLERS.propose_send_email
const fields = {
  action: 'send_email',
  candidate_email_id: 42,
  to: 'sarah@acme-staffing.com',
  cc: '',
  subject: 'Re: Senior Backend Engineer',
  body: 'Happy to share my resume.',
}

describe('ProposalCard', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  const render = (node: React.ReactNode) => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(node))
    return container
  }

  const buttonLabelled = (text: string) => Array.from(
    container?.querySelectorAll<HTMLButtonElement>('button') ?? [],
  ).find((button) => button.textContent?.includes(text))

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  it('shows every summary row the handler produces', () => {
    render(<ProposalCard handler={handler} fields={fields} busy={false} disabled={false} onApprove={vi.fn()} onCancel={vi.fn()} />)

    expect(container?.textContent).toContain('Confirm action')
    expect(container?.textContent).toContain('sarah@acme-staffing.com')
    expect(container?.textContent).toContain('Re: Senior Backend Engineer')
    // Empty values render as a dash rather than collapsing the row away.
    expect(container?.querySelectorAll('dd')[1]?.textContent).toBe('-')
  })

  it('fires the callbacks without acting on its own', () => {
    const onApprove = vi.fn()
    const onCancel = vi.fn()
    render(<ProposalCard handler={handler} fields={fields} busy={false} disabled={false} onApprove={onApprove} onCancel={onCancel} />)

    act(() => buttonLabelled('Send Email')?.click())
    expect(onApprove).toHaveBeenCalledTimes(1)

    act(() => buttonLabelled('Cancel')?.click())
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  // busy and disabled are separate props on purpose: a card blocked because a
  // *different* proposal is running must not claim to be working itself.
  it('shows Working only on the running card, while disabling both', () => {
    render(<ProposalCard handler={handler} fields={fields} busy disabled onApprove={vi.fn()} onCancel={vi.fn()} />)

    expect(container?.textContent).toContain('Working...')
    expect(buttonLabelled('Cancel')?.disabled).toBe(true)
  })

  it('disables without claiming to work when another proposal is running', () => {
    render(<ProposalCard handler={handler} fields={fields} busy={false} disabled onApprove={vi.fn()} onCancel={vi.fn()} />)

    expect(container?.textContent).not.toContain('Working...')
    expect(buttonLabelled('Send Email')?.disabled).toBe(true)
  })

  it('replaces the buttons with the outcome once a result exists', () => {
    render(<ProposalCard handler={handler} fields={fields} result={{ approved: true, detail: 'Email sent.' }} busy={false} disabled={false} onApprove={vi.fn()} onCancel={vi.fn()} />)

    expect(container?.textContent).toContain('Email sent.')
    expect(container?.querySelector('.chatProposalActions')).toBeNull()
  })

  it('marks a failed result so it is not mistaken for success', () => {
    render(<ProposalCard handler={handler} fields={fields} result={{ approved: false, detail: 'Gmail rejected the send.' }} busy={false} disabled={false} onApprove={vi.fn()} onCancel={vi.fn()} />)

    expect(container?.querySelector('.chatError')?.textContent).toBe('Gmail rejected the send.')
  })

  it('says plainly that nothing happened when cancelled', () => {
    render(<ProposalCard handler={handler} fields={fields} result="cancelled" busy={false} disabled={false} onApprove={vi.fn()} onCancel={vi.fn()} />)

    expect(container?.textContent).toContain('No changes were made.')
    expect(container?.querySelector('.chatProposalActions')).toBeNull()
  })
})
