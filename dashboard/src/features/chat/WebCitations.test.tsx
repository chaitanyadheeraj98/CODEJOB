// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

import WebCitations from './WebCitations'
import { renderForMessage, type WebResultsData } from './renderers'
import type { ChatMessage } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const payload = {
  action: 'search_web',
  query: 'H1B transfer timeline',
  results: [
    { url: 'https://www.uscis.gov/h1b', title: 'H-1B Specialty Occupations', snippet: 'Processing times vary.', score: 1 },
    { url: 'https://example.com/blog', title: '', snippet: 'A blog post.', score: 0.5 },
  ],
}

const toolMessage = (content: unknown): ChatMessage => ({
  id: 1,
  role: 'tool',
  tool_name: 'search_web',
  content: typeof content === 'string' ? content : JSON.stringify(content),
  created_at: '2026-01-01T00:00:00Z',
})

const parse = (raw: unknown): WebResultsData => {
  const parsed = renderForMessage(toolMessage(raw))
  if (parsed?.kind !== 'web_results') throw new Error('fixture did not parse')
  return parsed.data
}

describe('WebCitations', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (data: WebResultsData, surface: 'page' | 'compact' = 'page') => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<WebCitations data={data} surface={surface} />))
    return container
  }

  it('marks the block as external and shows hostnames', () => {
    const el = render(parse(payload))

    expect(el.textContent).toContain('From the web')
    expect(el.textContent).toContain('uscis.gov')
    // Nothing here may borrow the candidate table's styling.
    expect(el.querySelector('.candidateTable')).toBeNull()
  })

  it('links out with noopener on every external result', () => {
    const el = render(parse(payload))

    const links = Array.from(el.querySelectorAll('a'))
    expect(links.length).toBe(2)
    for (const link of links) {
      expect(link.getAttribute('target')).toBe('_blank')
      expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    }
  })

  it('falls back to the hostname when a result has no title, never the snippet', () => {
    const el = render(parse(payload))

    const second = el.querySelectorAll('li')[1]
    expect(second.querySelector('a')?.textContent).toBe('example.com')
    expect(second.querySelector('a')?.textContent).not.toContain('A blog post')
  })

  it.each([
    ['javascript:', 'javascript:alert(1)'],
    ['data:', 'data:text/html,<script>alert(1)</script>'],
    ['protocol-relative', '//evil.example.com/x'],
  ])('renders a %s url as text with no anchor', (_label, url) => {
    const el = render(parse({ ...payload, results: [{ url, title: 'Click me', snippet: '' }] }))

    expect(el.querySelectorAll('a').length).toBe(0)
    expect(el.textContent).toContain('Click me')
  })

  // Running attacker text through renderMarkdownLite would let a search result
  // mint links inside the assistant's own answer.
  it('renders a snippet containing markdown link syntax as literal text', () => {
    const el = render(parse({
      ...payload,
      results: [{ url: 'https://example.com', title: 'Result', snippet: 'See [our offer](https://evil.example.com) now' }],
    }))

    expect(el.textContent).toContain('[our offer](https://evil.example.com)')
    expect(el.querySelectorAll('a').length).toBe(1)
    expect(el.querySelector('a')?.getAttribute('href')).toBe('https://example.com')
  })

  it('says there were no results rather than rendering nothing', () => {
    const el = render(parse({ ...payload, results: [] }))

    expect(el.textContent).toContain('No results')
    expect(el.textContent).toContain('H1B transfer timeline')
  })

  it('renders titles and links but no snippets on the compact surface', () => {
    const el = render(parse(payload), 'compact')

    expect(el.querySelectorAll('a').length).toBe(2)
    expect(el.textContent).not.toContain('Processing times vary')
  })
})

describe('search_web payload parsing', () => {
  it('keeps title and snippet separate from the delimited model text', () => {
    const data = parse(payload)

    expect(data.results[0].title).toBe('H-1B Specialty Occupations')
    expect(data.results[0].snippet).toBe('Processing times vary.')
  })

  it.each([
    ['no action discriminator', { query: 'x', results: [] }],
    ['results that are not an array', { ...payload, results: 'two' }],
    ['a result with no url', { ...payload, results: [{ title: 'x' }] }],
    ['not JSON at all', 'search failed'],
  ])('returns null for %s', (_label, content) => {
    expect(renderForMessage(toolMessage(content))).toBeNull()
  })
})
