// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ParserDetailsPanel } from './App'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('ParserDetailsPanel', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
  })

  it('renders collapsed by default and expands backend-provided parser details', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    const onToggle = vi.fn()

    act(() => {
      root.render(
        <ParserDetailsPanel
          candidateId={42}
          source="nvoids"
          expanded={false}
          onToggle={onToggle}
          parserDetails={{
            parser_version: 'spacy_enrichment_v1',
            source: 'nvoids',
            merged_result: { role: 'Full Stack Developer', skills_text: 'Java, Spring Boot, React' },
            base_parser_result: { role: 'Full Stack Developer' },
            enrichment_result: { company: 'Acme', confidence: 0.62 },
            merge_notes: ['merged taxonomy-normalized skills from deterministic parser and enrichment'],
          }}
        />,
      )
    })

    const button = container.querySelector('.parserDetailsToggle') as HTMLButtonElement | null
    expect(button?.textContent).toBe('View Details')
    expect(container.querySelector('.parserDetailsPanel')).toBeNull()

    act(() => {
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(onToggle).toHaveBeenCalledWith(42)

    act(() => {
      root.render(
        <ParserDetailsPanel
          candidateId={42}
          source="nvoids"
          expanded={true}
          onToggle={onToggle}
          parserDetails={{
            parser_version: 'spacy_enrichment_v1',
            source: 'nvoids',
            merged_result: { role: 'Full Stack Developer', skills_text: 'Java, Spring Boot, React' },
            base_parser_result: { role: 'Full Stack Developer' },
            enrichment_result: { company: 'Acme', confidence: 0.62 },
            merge_notes: ['merged taxonomy-normalized skills from deterministic parser and enrichment'],
          }}
        />,
      )
    })

    expect(container.querySelector('.parserDetailsPanel')).not.toBeNull()
    expect(container.textContent ?? '').toContain('Final Extracted Result')
    expect(container.textContent ?? '').toContain('Full Stack Developer')
    expect(container.textContent ?? '').toContain('merged taxonomy-normalized skills')
  })
})
