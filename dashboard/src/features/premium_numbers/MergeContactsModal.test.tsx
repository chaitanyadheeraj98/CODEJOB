// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import MergeContactsModal from './MergeContactsModal'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

describe('MergeContactsModal', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.unstubAllGlobals()
  })

  it('shows every identity field and traps focus until Escape closes it', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({
      contact_a: {
        id: 10,
        recruiter_name: 'Jane Recruiter',
        owner_name: 'Unknown',
        company: 'Acme',
        secondary_company: 'Acme Staffing',
        recruiter_email: 'jane@acme.example',
        employer_email: '',
        normalized_phone_number: '12145550100',
        display_phone_number: '(214) 555-0100',
        phones: [
          { phone: '12145550100', extension: '', display: '(214) 555-0100', is_primary: true, is_verified: true, label: '' },
          { phone: '12145550101', extension: '9', display: '(214) 555-0101 ext 9', is_primary: false, is_verified: false, label: '' },
        ],
        emails: [
          { email: 'jane@acme.example', domain: 'acme.example', is_primary: true },
          { email: 'jane.alt@acme.example', domain: 'acme.example', is_primary: false },
        ],
        is_recruiter: true,
        is_employer: false,
        lead_count: 2,
        latest_evidence_at: null,
        leads: [],
      },
      contact_b: {
        id: 11,
        recruiter_name: 'Unknown',
        owner_name: 'Jane Employer',
        company: 'Beta',
        secondary_company: '',
        recruiter_email: '',
        employer_email: 'jane@beta.example',
        normalized_phone_number: '12145550200',
        display_phone_number: '(214) 555-0200',
        phones: [],
        emails: [],
        is_recruiter: false,
        is_employer: true,
        lead_count: 1,
        latest_evidence_at: null,
        leads: [],
      },
    })))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    const onClose = vi.fn()
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    await act(async () => {
      root.render(
        <MergeContactsModal
          apiBase="http://localhost:8000"
          contactIdA={10}
          contactIdB={11}
          onClose={onClose}
          onMerged={vi.fn()}
          onError={vi.fn()}
        />,
      )
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('Acme Staffing')
    expect(container.textContent).toContain('(214) 555-0101 ext 9')
    expect(container.textContent).toContain('jane.alt@acme.example')
    const dialog = container.querySelector<HTMLElement>('[role="dialog"]')
    const buttons = Array.from(dialog?.querySelectorAll<HTMLButtonElement>('button:not([disabled])') ?? [])
    buttons[0].focus()
    await act(async () => {
      buttons[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }))
    })
    expect(document.activeElement).toBe(buttons.at(-1))
    await act(async () => {
      dialog?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(onClose).toHaveBeenCalled()
  })
})
