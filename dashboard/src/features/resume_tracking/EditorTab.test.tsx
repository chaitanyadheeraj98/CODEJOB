// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import EditorTab from './EditorTab'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const resume = (over: Record<string, unknown> = {}) => ({
  id: 7,
  file_name: 'java.pdf',
  version: 3,
  skills_text: 'java, spring boot',
  primary_role: 'Java Developer',
  structured_skills: ['Java'],
  variant_label: 'Banking',
  variant_code: 'R07',
  is_enabled: true,
  is_current: true,
  created_at: '2026-08-12T10:00:00Z',
  updated_at: '2026-08-12T10:00:00Z',
  ...over,
})

const draft = (over: Record<string, unknown> = {}) => ({
  id: 4,
  name: 'R07 Banking',
  source_resume_id: 7,
  source_variant_code: 'R07',
  character_count: 42,
  created_at: '2026-09-01T10:00:00Z',
  updated_at: '2026-09-01T10:00:00Z',
  ...over,
})

const fullDraft = (over: Record<string, unknown> = {}) => ({
  ...draft(),
  content_markdown: '# Chaithanya\n\n## Summary\nJava developer.',
  ...over,
})

const profile = (over: Record<string, unknown> = {}) => ({
  id: 3,
  name: 'Vendor A',
  source_file_name: 'vendor.docx',
  is_default: true,
  spec: {
    font_family: 'Arial',
    body_font_size: 10,
    name_font_size: 14,
    heading_font_size: 10,
    heading_bold: true,
    heading_uppercase: false,
    margin_left_inches: 0.25,
    margin_right_inches: 0.5,
    margin_top_inches: 0.5,
    margin_bottom_inches: 0.5,
    line_spacing: 1,
    justify_body: true,
    bullet_indent_inches: 0.5,
    bullet_hanging_inches: 0.25,
    rule_before_sections: ['Summary', 'Skills'],
    heading_space_before_pt: 4,
    heading_space_after_pt: 2,
    skills_divider_inches: 3,
    skills_category_bold: true,
    skills_row_gap_pt: 6,
    environment_gap_pt: 8,
  },
  created_at: '2026-09-01T10:00:00Z',
  updated_at: '2026-09-01T10:00:00Z',
  ...over,
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

type Overrides = Partial<Record<'library' | 'profiles' | 'drafts' | 'create' | 'get' | 'save' | 'export' | 'publish', () => Response>>

/** Route by URL so a test states what each endpoint returns, not what order they are called in. */
const routes = (over: Overrides = {}) =>
  vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (url.includes('/settings/resumes')) return (over.library ?? (() => json([resume()])))()
    if (url.includes('/resume-editor/profiles')) return (over.profiles ?? (() => json([])))()
    if (url.includes('/export')) return (over.export ?? (() => json({ detail: 'unexpected' }, 500)))()
    if (url.includes('/publish')) {
      return (over.publish ?? (() => json({ resume_id: 9, variant_code: 'R09', file_name: 'Vendor_A_Java.docx', version: 4 })))()
    }
    if (url.includes('/drafts') && method === 'POST') return (over.create ?? (() => json(fullDraft())))()
    if (url.includes('/drafts') && method === 'PUT') return (over.save ?? (() => json(fullDraft())))()
    if (url.match(/\/drafts\/\d+$/)) return (over.get ?? (() => json(fullDraft())))()
    if (url.includes('/drafts')) return (over.drafts ?? (() => json([draft()])))()
    return json({ detail: `unrouted ${url}` }, 500)
  })

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

const type = async (field: Element | null, value: string) => {
  const textarea = field as HTMLTextAreaElement
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
    setter?.call(textarea, value)
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
}

const fill = async (field: Element | null, value: string) => {
  const input = field as HTMLInputElement
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    setter?.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
}

const labelled = (container: HTMLElement, text: string) =>
  Array.from(container.querySelectorAll('label')).find((item) => item.querySelector('span')?.textContent === text)
    ?.querySelector('input')

const choose = async (select: Element | null, value: string) => {
  const element = select as HTMLSelectElement
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set
    setter?.call(element, value)
    element.dispatchEvent(new Event('change', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
}

const buttonNamed = (container: HTMLElement, text: string) =>
  Array.from(container.querySelectorAll('button')).find((item) => item.textContent === text)

const file = (name: string, body = 'PK sample') => new File([body], name)

// jsdom has no DragEvent, and the drop path is the one `accept` cannot police, so
// the event is assembled by hand rather than routed through the file input.
const drop = async (zone: Element | null, dropped: File) => {
  await act(async () => {
    const event = new Event('drop', { bubbles: true })
    Object.defineProperty(event, 'dataTransfer', { value: { files: [dropped] } })
    zone?.dispatchEvent(event)
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
}

describe('EditorTab', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => { vi.unstubAllGlobals(); while (cleanups.length) cleanups.pop()?.() })

  it('says up front that editing does not change a stored variant', async () => {
    vi.stubGlobal('fetch', routes())
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    const rule = container.querySelector('.resumeEditorRule')?.textContent ?? ''
    expect(rule).toContain('never changes a stored variant')
    expect(rule).toContain('save it as a new variant')
    expect(rule).toContain('reads its text back out of that same file')
  })

  it('copies a variant into a draft without writing to the variant', async () => {
    const fetchMock = routes()
    vi.stubGlobal('fetch', fetchMock)
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await choose(container.querySelector('.resumeEditorStart select'), '7')

    const post = fetchMock.mock.calls.find((call) => (call[1] as RequestInit | undefined)?.method === 'POST')
    expect(post?.[0]).toBe('http://localhost:8000/resume-editor/drafts')
    expect(JSON.parse((post?.[1] as RequestInit).body as string)).toEqual({ source_resume_id: 7 })
    // Nothing may be written under a resume - that is the invariant this tab exists for.
    const writes = fetchMock.mock.calls.filter((call) => ['POST', 'PUT', 'PATCH', 'DELETE'].includes((call[1] as RequestInit | undefined)?.method ?? 'GET'))
    expect(writes.every((call) => !String(call[0]).includes('/resumes/'))).toBe(true)
    expect(container.textContent).toContain('R07 itself is unchanged')
  })

  it('opens a draft and loads its text', async () => {
    vi.stubGlobal('fetch', routes())
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(container.querySelector('.resumeEditorListItem'))
    expect((container.querySelector('.resumeEditorTextarea') as HTMLTextAreaElement).value).toContain('Java developer.')
  })

  it('saves to the draft endpoint and says no variant was touched', async () => {
    const edited = '# Chaithanya\n\n## Summary\nSenior Java developer.'
    const fetchMock = routes({ save: () => json(fullDraft({ content_markdown: edited })) })
    vi.stubGlobal('fetch', fetchMock)
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(container.querySelector('.resumeEditorListItem'))
    await type(container.querySelector('.resumeEditorTextarea'), edited)
    expect(container.textContent).toContain('unsaved')

    await click(buttonNamed(container, 'Save draft'))
    const put = fetchMock.mock.calls.find((call) => (call[1] as RequestInit | undefined)?.method === 'PUT')
    expect(put?.[0]).toBe('http://localhost:8000/resume-editor/drafts/4')
    expect(JSON.parse((put?.[1] as RequestInit).body as string).content_markdown).toBe(edited)
    expect(container.textContent).toContain('No stored variant was changed.')
    expect(container.textContent).not.toContain('unsaved')
  })

  it('will not download text that has not been saved yet', async () => {
    vi.stubGlobal('fetch', routes())
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(container.querySelector('.resumeEditorListItem'))
    expect((buttonNamed(container, 'Download') as HTMLButtonElement).disabled).toBe(false)
    await type(container.querySelector('.resumeEditorTextarea'), 'changed')
    expect((buttonNamed(container, 'Download') as HTMLButtonElement).disabled).toBe(true)
    expect(container.textContent).toContain('Save before downloading')
  })

  it('downloads the draft and points at the upload step that makes it a variant', async () => {
    const fetchMock = routes({
      profiles: () => json([profile()]),
      export: () => new Response('body', {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="R07_Banking.docx"' },
      }),
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:x', revokeObjectURL: () => undefined }))
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(container.querySelector('.resumeEditorListItem'))
    await click(buttonNamed(container, 'Download'))

    const exportCall = fetchMock.mock.calls.find((call) => String(call[0]).includes('/export'))
    // The default profile is pre-selected, so a download uses it without being asked.
    expect(exportCall?.[0]).toBe('http://localhost:8000/resume-editor/drafts/4/export?fmt=docx&profile_id=3')
    expect(container.textContent).toContain('Downloaded R07_Banking.docx')
    expect(container.textContent).toContain('Save as new variant')
  })

  it('shows the server refusal instead of opening a window of raw JSON', async () => {
    vi.stubGlobal('fetch', routes({ export: () => json({ detail: 'This draft is empty.' }, 400) }))
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(container.querySelector('.resumeEditorListItem'))
    await click(buttonNamed(container, 'Download'))
    expect(container.querySelector('.errorText')?.textContent).toBe('This draft is empty.')
  })

  it('asks before deleting a draft', async () => {
    const fetchMock = routes()
    vi.stubGlobal('fetch', fetchMock)
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(container.querySelector('.resumeEditorListItem'))
    await click(buttonNamed(container, 'Delete draft'))
    expect(container.textContent).toContain('Delete “R07 Banking”?')
    expect(fetchMock.mock.calls.some((call) => (call[1] as RequestInit | undefined)?.method === 'DELETE')).toBe(false)

    await click(buttonNamed(container, 'Delete'))
    const removed = fetchMock.mock.calls.find((call) => (call[1] as RequestInit | undefined)?.method === 'DELETE')
    expect(removed?.[0]).toBe('http://localhost:8000/resume-editor/drafts/4')
  })

  it('names the variant a draft came from, and drops it once that variant is gone', async () => {
    vi.stubGlobal('fetch', routes({
      drafts: () => json([draft(), draft({ id: 5, name: 'Orphan', source_variant_code: '' })]),
    }))
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    const rows = container.querySelectorAll('.resumeEditorListItem')
    expect(rows[0].textContent).toContain('from R07')
    expect(rows[1].textContent).not.toContain('from')
  })

  it('lists a format profile by the numbers it was measured at', async () => {
    vi.stubGlobal('fetch', routes({ profiles: () => json([profile()]) }))
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(buttonNamed(container, 'Format profiles (1)'))
    const listed = container.querySelector('.resumeFormatProfileList')?.textContent ?? ''
    expect(listed).toContain('Vendor A')
    expect(listed).toContain('Arial 10pt')
    expect(listed).toContain('divider 3"')
    expect(listed).toContain('Rules above: Summary, Skills')
  })

  it('takes a dropped sample and names the file it is about to measure', async () => {
    const fetchMock = routes()
    vi.stubGlobal('fetch', fetchMock)
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(buttonNamed(container, 'Format profiles (0)'))
    const measure = () => buttonNamed(container, 'Measure and save') as HTMLButtonElement
    expect(measure().disabled).toBe(true)
    expect(container.querySelector('.resumeFormatProfileFoot')?.textContent).toContain('Add a sample resume to measure')

    await drop(container.querySelector('.resumeFileDrop'), file('Vendor A layout.docx'))

    expect(container.querySelector('.resumeFileDrop')?.className).toContain('loaded')
    expect(container.querySelector('.resumeFileDropText')?.textContent).toContain('Vendor A layout.docx')
    expect(measure().disabled).toBe(false)
    // The name is optional, so the panel says what the profile will be called.
    expect(container.querySelector('.resumeFormatProfileFoot')?.textContent).toContain('saved as “Vendor A layout”')

    await click(measure())
    const post = fetchMock.mock.calls.find((call) => String(call[0]).includes('/profiles') && (call[1] as RequestInit | undefined)?.method === 'POST')
    expect((post?.[1] as RequestInit).body).toBeInstanceOf(FormData)
    expect(((post?.[1] as RequestInit).body as FormData).get('file')).toBeInstanceOf(File)
  })

  // A dropped file bypasses `accept` entirely, so without this the only feedback
  // is a 400 after the upload.
  it('refuses a sample the server would reject, before uploading it', async () => {
    const fetchMock = routes()
    vi.stubGlobal('fetch', fetchMock)
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(buttonNamed(container, 'Format profiles (0)'))
    await drop(container.querySelector('.resumeFileDrop'), file('screenshot.png'))

    expect(container.querySelector('.resumeFileDrop')?.className).toContain('invalid')
    expect(container.querySelector('.resumeFileDropError')?.textContent).toContain('screenshot.png is not a .docx, .doc, .pdf, .md, .txt file')
    expect((buttonNamed(container, 'Measure and save') as HTMLButtonElement).disabled).toBe(true)
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes('/profiles') && (call[1] as RequestInit | undefined)?.method === 'POST')).toBe(false)

    // An empty file has nothing to measure either, and the server says so too.
    await drop(container.querySelector('.resumeFileDrop'), file('blank.docx', ''))
    expect(container.querySelector('.resumeFileDropError')?.textContent).toContain('blank.docx is empty')
  })

  it('keeps an unsaved edit when the user switches drafts to compare them', async () => {
    vi.stubGlobal('fetch', routes({ drafts: () => json([draft(), draft({ id: 5, name: 'Second' })]) }))
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    const rows = container.querySelectorAll('.resumeEditorListItem')
    await click(rows[0])
    await type(container.querySelector('.resumeEditorTextarea'), 'work in progress')
    await click(rows[1])
    await click(rows[0])
    expect((container.querySelector('.resumeEditorTextarea') as HTMLTextAreaElement).value).toBe('work in progress')
  })

  it('creates a variant from the draft through the publish endpoint', async () => {
    const fetchMock = routes({ profiles: () => json([profile()]) })
    vi.stubGlobal('fetch', fetchMock)
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(container.querySelector('.resumeEditorListItem'))
    await click(buttonNamed(container, 'Save as new variant'))
    await fill(labelled(container, 'New variant name') ?? null, 'Vendor A Java')
    await fill(labelled(container, 'Variant label') ?? null, 'Java / Banking')
    await click(buttonNamed(container, 'Create variant'))

    const call = fetchMock.mock.calls.find((item) => String(item[0]).includes('/publish'))
    expect(call?.[0]).toBe('http://localhost:8000/resume-editor/drafts/4/publish')
    expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({
      file_name: 'Vendor A Java',
      variant_label: 'Java / Banking',
      primary_role: '',
      structured_skills_text: '',
      fmt: 'docx',
      profile_id: 3,
    })
    expect(container.textContent).toContain('R09 added as Vendor_A_Java.docx (v4)')
  })

  it('refuses to reuse the draft name and says why before the request', async () => {
    const fetchMock = routes()
    vi.stubGlobal('fetch', fetchMock)
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(container.querySelector('.resumeEditorListItem'))
    await click(buttonNamed(container, 'Save as new variant'))
    await fill(labelled(container, 'New variant name') ?? null, 'r07 banking')

    expect(container.textContent).toContain("That is the draft's own name")
    expect((buttonNamed(container, 'Create variant') as HTMLButtonElement).disabled).toBe(true)
    expect(fetchMock.mock.calls.some((item) => String(item[0]).includes('/publish'))).toBe(false)
  })

  it('will not publish an unnamed draft, or one with unsaved edits', async () => {
    vi.stubGlobal('fetch', routes())
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(container.querySelector('.resumeEditorListItem'))
    await click(buttonNamed(container, 'Save as new variant'))
    expect((buttonNamed(container, 'Create variant') as HTMLButtonElement).disabled).toBe(true)

    await fill(labelled(container, 'New variant name') ?? null, 'Vendor A Java')
    expect((buttonNamed(container, 'Create variant') as HTMLButtonElement).disabled).toBe(false)

    await type(container.querySelector('.resumeEditorTextarea'), 'changed but not saved')
    expect((buttonNamed(container, 'Create variant') as HTMLButtonElement).disabled).toBe(true)
  })

  it('surfaces the server refusal when a variant already has that name', async () => {
    vi.stubGlobal('fetch', routes({
      publish: () => json({ detail: 'R03 is already called Vendor_A_Java.docx. Pick another name.' }, 400),
    }))
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(container.querySelector('.resumeEditorListItem'))
    await click(buttonNamed(container, 'Save as new variant'))
    await fill(labelled(container, 'New variant name') ?? null, 'Vendor A Java')
    await click(buttonNamed(container, 'Create variant'))
    expect(container.querySelector('.errorText')?.textContent).toContain('already called Vendor_A_Java.docx')
  })

  it('publishes as Word when the download format is markdown', async () => {
    const fetchMock = routes()
    vi.stubGlobal('fetch', fetchMock)
    const { container, cleanup } = await mount(<EditorTab apiBase="http://localhost:8000" />)
    cleanups.push(cleanup)

    await click(container.querySelector('.resumeEditorListItem'))
    const formatSelect = container.querySelectorAll('.resumeEditorExport select')[0]
    await choose(formatSelect, 'md')
    await click(buttonNamed(container, 'Save as new variant'))
    await fill(labelled(container, 'New variant name') ?? null, 'Vendor A Java')
    await click(buttonNamed(container, 'Create variant'))

    const call = fetchMock.mock.calls.find((item) => String(item[0]).includes('/publish'))
    expect(JSON.parse((call?.[1] as RequestInit).body as string).fmt).toBe('docx')
  })
})
