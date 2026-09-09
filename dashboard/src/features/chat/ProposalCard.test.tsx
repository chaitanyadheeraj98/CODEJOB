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

  it('shows grounding cautions without blocking the section digest approval', () => {
    const section = { action: 'propose_resume_section', draft_id: 4, section: 'Summary', current: 'Built APIs.', replacement: 'Saved 40%.', base_sha256: 'digest',
      grounding: { novel_numbers: ['40%'], novel_organisations: ['Acme Corp'], similarity: 0.3, low_similarity: true } }
    const handler = PROPOSAL_HANDLERS.propose_resume_section
    const onApprove = vi.fn()
    render(<ProposalCard handler={handler} fields={section} progress="1 of 2 sections proposed." busy={false} disabled={false} onApprove={onApprove} onCancel={vi.fn()} />)
    expect(container?.querySelector('.chatGroundingCaution')?.textContent).toContain('40%')
    expect(container?.textContent).toContain('Acme Corp')
    expect(container?.textContent).toContain('1 of 2')
    act(() => buttonLabelled('Apply Rewrite')?.click())
    expect(onApprove).toHaveBeenCalledOnce()
    expect(handler.buildBody(section)).toEqual({ section: 'Summary', replacement: 'Saved 40%.', base_sha256: 'digest' })
  })

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  it('shows every summary row the handler produces', () => {
    render(<ProposalCard handler={handler} fields={fields} busy={false} disabled={false} onApprove={vi.fn()} onCancel={vi.fn()} />)

    expect(container?.textContent).toContain('Confirm action')
    expect(container?.querySelector('.chatGroundingCaution')).toBeNull()
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

  // --- a whole document in one row ------------------------------------

  const profileHandler = PROPOSAL_HANDLERS.propose_profile_update
  const longProfile = `# Chaithanya Dheeraj\n${'- Skill line\n'.repeat(400)}- Notice period: LAST LINE`
  const profileFields = {
    action: 'propose_profile_update',
    operation: 'append',
    field: 'Notice period',
    value: '2 weeks',
    entry: '- Notice period: 2 weeks',
    existing_profile: '# Chaithanya Dheeraj\n',
    resulting_profile: longProfile,
    base_sha256: 'a'.repeat(64),
    provenance: 'assistant_asked',
  }

  it('renders a long value in full inside a scrollable block', () => {
    render(<ProposalCard handler={profileHandler} fields={profileFields} busy={false} disabled={false} onApprove={vi.fn()} onCancel={vi.fn()} />)

    const block = container?.querySelector('pre.chatProposalDocument')
    expect(block).not.toBeNull()
    // Every character, not a truncation and not a title attribute: R5 means the
    // user can read what will be stored before they click.
    expect(block?.textContent).toContain('LAST LINE')
    expect(block?.textContent).toBe(longProfile)
  })

  it('leaves a short single-line value as plain text', () => {
    render(<ProposalCard handler={profileHandler} fields={profileFields} busy={false} disabled={false} onApprove={vi.fn()} onCancel={vi.fn()} />)

    const first = container?.querySelectorAll('dd')[0]
    expect(first?.querySelector('pre')).toBeNull()
    expect(first?.textContent).toBe('Notice period')
  })

  // --- the app's own statement of pending state -----------------------

  it('says nothing has been saved yet while the card is unresolved', () => {
    render(<ProposalCard handler={profileHandler} fields={profileFields} busy={false} disabled={false} onApprove={vi.fn()} onCancel={vi.fn()} />)

    expect(container?.querySelector('.chatProposalPending')?.textContent)
      .toContain('Nothing has been saved yet')
  })

  it('drops the pending line the moment a result exists', () => {
    // The negative half is the one that matters: a card showing both an outcome
    // and "nothing has been saved yet" is a false statement in the other
    // direction, which is no better than the claim it replaces.
    for (const result of [{ approved: true, detail: 'Profile updated.' }, 'cancelled'] as const) {
      render(<ProposalCard handler={profileHandler} fields={profileFields} result={result} busy={false} disabled={false} onApprove={vi.fn()} onCancel={vi.fn()} />)
      expect(container?.querySelector('.chatProposalPending')).toBeNull()
      if (root) act(() => root?.unmount())
      container?.remove()
    }
  })

  it('shows no pending line for a handler that declares none', () => {
    render(<ProposalCard handler={handler} fields={fields} busy={false} disabled={false} onApprove={vi.fn()} onCancel={vi.fn()} />)

    expect(container?.querySelector('.chatProposalPending')).toBeNull()
  })
})
