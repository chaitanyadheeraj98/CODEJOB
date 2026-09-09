// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'
import ResumePreview, { DEFAULT_SPEC, resumeBlocks, splitForLayout } from './ResumePreview'
import { paginate } from './usePagination'
import fixture from './resumePreview.fixture.json'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('resume preview', () => {
  it('uses the server splitter fixture headings and the Word name, contact and divider conventions', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    await act(async () => root.render(<div>{resumeBlocks(fixture.markdown, { ...DEFAULT_SPEC, skills_divider_inches: 2 })}</div>))
    expect(Array.from(container.querySelectorAll('[data-resume-heading]'), (node) => node.getAttribute('data-resume-heading'))).toEqual(fixture.headings)
    expect((container.querySelector('.resumeName') as HTMLElement).style.textAlign).toBe('center')
    expect((container.querySelector('.resumeName') as HTMLElement).style.fontSize).toBe('14pt')
    expect((container.querySelector('.resumeContact') as HTMLElement).style.textAlign).toBe('center')
    expect((container.querySelector('.resumeSkillsRow') as HTMLElement).style.gridTemplateColumns).toBe('2in 1fr')
    act(() => root.unmount())
  })

  it('passes page geometry and font numbers into the DOM without executable markdown', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    await act(async () => root.render(<ResumePreview markdown={'# Alex\n[x](javascript:alert)\n<script>bad</script>'}
      spec={{ ...DEFAULT_SPEC, body_font_size: 12, margin_left_inches: 0.75 }} />))
    expect((container.querySelector('.resumePage') as HTMLElement).style.paddingLeft).toBe('0.75in')
    expect((container.querySelector('.resumePreviewContent') as HTMLElement).style.fontSize).toBe('12pt')
    expect(container.querySelector('script, a')).toBeNull()
    act(() => root.unmount())
  })

  it('splits into the same header, main and sidebar the server renders the docx from', () => {
    const { header, main, sidebar } = splitForLayout(fixture.markdown, {
      ...DEFAULT_SPEC, layout: 'two-column', sidebar_sections: fixture.twoColumn.sidebar_sections,
    })
    // The expected values in the fixture are the output of split_for_layout in
    // resume_render_service.py. If these drift apart the preview shows Skills in
    // a different column than the download puts it, which is worse than no preview.
    expect(header.trim()).toBe(fixture.twoColumn.header)
    expect(main.trim()).toBe(fixture.twoColumn.main)
    expect(sidebar.trim()).toBe(fixture.twoColumn.sidebar)
  })

  it('renders two columns only once a sidebar section is named, at the spec widths', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    const spec = { ...DEFAULT_SPEC, layout: 'two-column' as const, sidebar_width_inches: 2, sidebar_sections: ['Skills'] }
    await act(async () => root.render(<ResumePreview markdown={fixture.markdown} spec={spec} />))
    const columns = container.querySelector('.resumeColumns') as HTMLElement
    // 8.5 page - 0.25 left - 0.5 right - 2 sidebar - 0.2 gutter = 5.55in main.
    expect(columns.style.gridTemplateColumns).toBe('5.55in 2in')

    await act(async () => root.render(<ResumePreview markdown={fixture.markdown} spec={{ ...spec, sidebar_sections: [] }} />))
    expect(container.querySelector('.resumeColumns')).toBeNull()
    act(() => root.unmount())
  })

  it('keeps short content on one page, moves intact items and prevents orphan headings', () => {
    expect(paginate([{ top: 0, bottom: 40 }], 100, 40)).toEqual([{ start: 0, end: 40 }])
    expect(paginate([{ top: 0, bottom: 70 }, { top: 70, bottom: 140 }], 100, 140)).toEqual([{ start: 0, end: 70 }, { start: 70, end: 140 }])
    expect(paginate([{ top: 0, bottom: 70 }, { top: 70, bottom: 90, heading: true }, { top: 90, bottom: 120 }], 100, 120)).toEqual([{ start: 0, end: 70 }, { start: 70, end: 120 }])
    expect(paginate([{ top: 0, bottom: 250 }], 100, 250)).toHaveLength(3)
    // A block taller than a page cannot be saved by a break, so it spans instead
    // of pushing an almost-empty page in front of itself. This is the shape a
    // two-column body has: one item several pages tall.
    expect(paginate([{ top: 10, bottom: 30 }, { top: 30, bottom: 900 }], 100, 900)[0]).toEqual({ start: 0, end: 100 })
  })
})
