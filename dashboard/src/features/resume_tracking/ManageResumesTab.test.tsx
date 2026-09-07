// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ManageResumesTab from './ManageResumesTab'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const resume = (over: Record<string, unknown> = {}) => ({
  id: 7,
  file_name: 'java.pdf',
  version: 3,
  skills_text: 'java, spring boot',
  primary_role: 'Java Developer',
  structured_skills: ['Java', 'AWS'],
  variant_label: 'Banking',
  variant_code: 'R07',
  is_enabled: true,
  is_current: true,
  created_at: '2026-08-12T10:00:00Z',
  updated_at: '2026-08-12T10:00:00Z',
  ...over,
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const mount = async (element: React.ReactElement) => {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root: Root = createRoot(container)
  await act(async () => { root.render(element); await new Promise((resolve) => setTimeout(resolve, 20)) })
  return { container, cleanup: () => { act(() => root.unmount()); container.remove() } }
}

const click = async (button: Element | undefined | null) => {
  await act(async () => { (button as HTMLButtonElement | null)?.click(); await new Promise((resolve) => setTimeout(resolve, 20)) })
}

const buttonNamed = (container: HTMLElement, text: string) =>
  Array.from(container.querySelectorAll('button')).find((item) => item.textContent === text)

describe('ManageResumesTab', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => { vi.unstubAllGlobals(); while (cleanups.length) cleanups.pop()?.() })

  it('lists the library by variant code with its role, state and skills', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json([resume()])))
    const { container, cleanup } = await mount(<ManageResumesTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    expect(container.querySelector('.resumeLibraryCode')?.textContent).toBe('R07')
    expect(container.textContent).toContain('Banking')
    expect(container.textContent).toContain('java.pdf')
    expect(container.textContent).toContain('Java Developer')
    expect(container.textContent).toContain('AWS')
    expect(container.textContent).toContain('Enabled')
    expect(container.textContent).toContain('Fallback')
    // The editor is a disclosure: the row stays scannable until one is opened.
    expect(container.querySelector('.resumeLibraryEditor')).toBeNull()
  })

  it('saves an edit through the same endpoint Settings uses, then tells the host', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') return json(resume({ primary_role: 'Staff Java Developer' }))
      return json([resume()])
    })
    vi.stubGlobal('fetch', fetchMock)
    const onLibraryChanged = vi.fn()
    const { container, cleanup } = await mount(
      <ManageResumesTab apiBase="http://localhost:8000" onLibraryChanged={onLibraryChanged} />,
    )
    cleanups.push(cleanup)

    await click(buttonNamed(container, 'Edit'))
    const roleInput = container.querySelector('.resumeLibraryEditor input') as HTMLInputElement
    expect(roleInput.value).toBe('Java Developer')
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set
      setter?.call(roleInput, 'Staff Java Developer')
      roleInput.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(buttonNamed(container, 'Save changes'))

    const patch = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')
    expect(String(patch?.[0])).toBe('http://localhost:8000/settings/resumes/7')
    expect(JSON.parse(String((patch?.[1] as RequestInit).body))).toEqual({
      primary_role: 'Staff Java Developer',
      variant_label: 'Banking',
      // The comma-separated field is split for the API, not sent as one string.
      structured_skills: ['Java', 'AWS'],
      skills_text: 'java, spring boot',
    })
    expect(container.textContent).toContain('R07 saved.')
    // Settings holds its own copy of this list; it has to be told to reload.
    expect(onLibraryChanged).toHaveBeenCalled()
  })

  it('asks before deleting, and surfaces the server refusal verbatim', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        return json({ detail: 'Resume is used by an active application; close or delete those applications first' }, 409)
      }
      return json([resume()])
    }))
    const { container, cleanup } = await mount(<ManageResumesTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(buttonNamed(container, 'Edit'))
    // One click arms the delete, it does not perform it.
    await click(buttonNamed(container, 'Delete'))
    expect(container.textContent).toContain('Delete R07 permanently?')

    await click(buttonNamed(container, 'Delete'))
    expect(container.querySelector('.errorText')?.textContent).toBe(
      'Resume is used by an active application; close or delete those applications first',
    )
  })

  it('opens the editor for the resume another tab handed over', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json([resume({ id: 9, variant_code: 'R09', primary_role: '', variant_label: '' })])))
    const { container, cleanup } = await mount(<ManageResumesTab apiBase="http://localhost:8000" focusResumeId={9} />)
    cleanups.push(cleanup)

    expect(container.querySelector('.resumeLibraryEditor')).not.toBeNull()
    expect(buttonNamed(container, 'Close')).toBeTruthy()
  })

  it('falls back to the extracted skills when the curated list is empty', async () => {
    // Every resume in the live library is in exactly this state: structured_skills
    // empty, skills_text full. Reading only the curated field showed a blank cell
    // on all eighteen rows.
    vi.stubGlobal('fetch', vi.fn(async () => json([resume({ structured_skills: [], skills_text: 'Java, Spring Boot, Microservices' })])))
    const { container, cleanup } = await mount(<ManageResumesTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    expect(Array.from(container.querySelectorAll('.trackingChip')).map((chip) => chip.textContent))
      .toEqual(['Java', 'Spring Boot', 'Microservices'])
  })

  it('filters the library rather than making the user read eighteen rows', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json([
      resume(),
      resume({ id: 8, variant_code: 'R08', variant_label: 'Healthcare', file_name: 'health.pdf', primary_role: 'Data Engineer', structured_skills: ['PySpark'] }),
    ])))
    const { container, cleanup } = await mount(<ManageResumesTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    expect(container.querySelectorAll('.resumeLibraryCard')).toHaveLength(2)
    const search = container.querySelector('.resumeLibrarySearch input') as HTMLInputElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set
      setter?.call(search, 'pyspark')
      search.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(container.querySelectorAll('.resumeLibraryCard')).toHaveLength(1)
    expect(container.querySelector('.resumeLibraryCode')?.textContent).toBe('R08')
  })
})
