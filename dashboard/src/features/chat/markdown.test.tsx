// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

import { renderMarkdownLite } from './markdown'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('renderMarkdownLite links', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (markdown: string) => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<>{renderMarkdownLite(markdown)}</>))
    return container
  }

  const anchor = () => container!.querySelector('a')

  it('renders an external link and opens it safely', () => {
    render('See [the posting](https://example.com/jobs/12) for details.')

    expect(anchor()?.getAttribute('href')).toBe('https://example.com/jobs/12')
    expect(anchor()?.textContent).toBe('the posting')
    expect(anchor()?.getAttribute('target')).toBe('_blank')
    expect(anchor()?.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it('keeps a same-origin link in the tab', () => {
    render('Open [Needs Review](?page=needs_review).')

    expect(anchor()?.getAttribute('href')).toBe('?page=needs_review')
    expect(anchor()?.getAttribute('target')).toBeNull()
  })

  // Assistant output quotes recruiter email bodies and untrusted web results, so
  // every one of these is reachable by someone who is not the user.
  it.each([
    ['javascript:', '[Click me](javascript:alert(1))'],
    ['data:', '[Click me](data:text/html;base64,PHNjcmlwdD4=)'],
    ['vbscript:', '[Click me](vbscript:msgbox)'],
    ['protocol-relative //', '[Click me](//evil.example.com)'],
    ['backslash-relative', '[Click me](/\\evil.example.com)'],
    ['bare scheme-less host', '[Click me](evil.example.com)'],
  ])('refuses to link a %s href, leaving the text visible', (_label, markdown) => {
    render(markdown)

    expect(anchor()).toBeNull()
    expect(container?.textContent).toContain('Click me')
  })

  it('still renders the other inline marks alongside links', () => {
    render('**Bold** and `code` and [a link](https://example.com).')

    expect(container?.querySelector('strong')?.textContent).toBe('Bold')
    expect(container?.querySelector('code')?.textContent).toBe('code')
    expect(anchor()?.textContent).toBe('a link')
  })

  it('renders links inside table cells and list items', () => {
    render([
      '| Role | Source |',
      '| --- | --- |',
      '| Backend | [posting](https://example.com/a) |',
      '',
      '- see [notes](https://example.com/b)',
    ].join('\n'))

    const hrefs = Array.from(container!.querySelectorAll('a')).map((link) => link.getAttribute('href'))
    expect(hrefs).toEqual(['https://example.com/a', 'https://example.com/b'])
  })
})

it('links only the record ids this thread actually fetched', () => {
  const selected: number[] = []
  const citations = {
    ids: new Map([['record-1', 41], ['record-2', 42]]),
    onSelect: (id: number) => selected.push(id),
  }
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  try {
    act(() => {
      root.render(
        <div>
          {renderMarkdownLite(
            'record-1 replied, record-2 did not, and record-9 is not on file. See `record-1` too.',
            citations,
          )}
        </div>,
      )
    })
    const buttons = Array.from(container.querySelectorAll('button.chatRecordCitation'))
    expect(buttons.map((node) => node.textContent)).toEqual(['record-1', 'record-2'])
    // An id the assistant produced with no tool behind it stays plain text -
    // that is the whole point of indexing off the transcript.
    expect(container.textContent).toContain('record-9 is not on file')
    expect(container.querySelector('code')?.textContent).toBe('record-1')
    act(() => { (buttons[1] as HTMLButtonElement).click() })
    expect(selected).toEqual([42])
  } finally {
    act(() => root.unmount())
    container.remove()
  }
})

it('renders unchanged when no citations are supplied', () => {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  try {
    act(() => { root.render(<div>{renderMarkdownLite('record-1 replied.')}</div>) })
    expect(container.querySelector('button')).toBeNull()
    expect(container.textContent).toBe('record-1 replied.')
  } finally {
    act(() => root.unmount())
    container.remove()
  }
})
