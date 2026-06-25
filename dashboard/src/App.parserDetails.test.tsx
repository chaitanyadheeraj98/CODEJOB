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
          atsScore={84}
          atsSource="hybrid_structured_only"
          atsSummary="ATS hybrid score 84/100; raw_overlap=0.75; intent_match=0.82"
          atsBreakdown={{ raw_overlap: 0.75, matched_raw_skills: ['Java', 'Spring Boot'], selected_resume_file_name: 'resume.docx' }}
          expanded={false}
          onToggle={onToggle}
          parserDetails={{
            parser_version: 'ai_parser_modes_v1',
            source: 'nvoids',
            parser_mode: 'ai_primary',
            fallback_used: false,
            merged_result: { role: 'Full Stack Developer', location: 'Dallas, TX', skills_text: 'Java, Spring Boot, React, Temporal Workflow' },
            base_parser_result: { role: 'Full Stack Developer', location: 'Remote' },
            ai_extractor_result: {
              role_candidates: ['Full Stack Developer'],
              primary_location: 'Austin, TX',
              confidence: 0.87,
              evidence: { title: ['Full Stack Developer'] },
              skills_text: 'Java, Spring Boot, React, Temporal Workflow',
            },
            skills_audit: {
              skills_text: 'Java, Spring Boot, React, Temporal Workflow',
              known: ['Java', 'Spring Boot', 'React'],
              unknown: ['Temporal Workflow'],
              evidence: { Temporal_Workflow: 'ai_extractor' },
            },
            approved_skills_text: 'Java, Spring Boot, React',
            unknown_skills: ['Temporal Workflow'],
            parser_warning: null,
            source_hints: { canonical_title: 'Full Stack Developer', canonical_location: 'Remote, USA', work_mode: 'Remote' },
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
          atsScore={84}
          atsSource="hybrid_structured_only"
          atsSummary="ATS hybrid score 84/100; raw_overlap=0.75; intent_match=0.82"
          atsBreakdown={{ raw_overlap: 0.75, matched_raw_skills: ['Java', 'Spring Boot'], selected_resume_file_name: 'resume.docx' }}
          expanded={true}
          onToggle={onToggle}
          parserDetails={{
            parser_version: 'ai_parser_modes_v1',
            source: 'nvoids',
            parser_mode: 'ai_primary',
            fallback_used: false,
            merged_result: { role: 'Full Stack Developer', location: 'Dallas, TX', skills_text: 'Java, Spring Boot, React, Temporal Workflow' },
            base_parser_result: { role: 'Full Stack Developer', location: 'Remote' },
            ai_extractor_result: {
              role_candidates: ['Full Stack Developer'],
              primary_location: 'Austin, TX',
              confidence: 0.87,
              evidence: { title: ['Full Stack Developer'] },
              skills_text: 'Java, Spring Boot, React, Temporal Workflow',
            },
            skills_audit: {
              skills_text: 'Java, Spring Boot, React, Temporal Workflow',
              known: ['Java', 'Spring Boot', 'React'],
              unknown: ['Temporal Workflow'],
              evidence: { Temporal_Workflow: 'ai_extractor' },
            },
            approved_skills_text: 'Java, Spring Boot, React',
            unknown_skills: ['Temporal Workflow'],
            parser_warning: null,
            source_hints: { canonical_title: 'Full Stack Developer', canonical_location: 'Remote, USA', work_mode: 'Remote' },
          }}
        />,
      )
    })

    expect(container.querySelector('.parserDetailsPanel')).not.toBeNull()
    expect(container.textContent ?? '').toContain('Mode:')
    expect(container.textContent ?? '').toContain('ai_primary')
    expect(container.textContent ?? '').toContain('Final Extracted Result')
    expect(container.textContent ?? '').toContain('Full Stack Developer')
    expect(container.textContent ?? '').toContain('Parser Status')
    expect(container.textContent ?? '').toContain('Approved Skills')
    expect(container.textContent ?? '').toContain('Unknown Skills')
    expect(container.textContent ?? '').toContain('Temporal Workflow')
    expect(container.textContent ?? '').toContain('ATS Summary')
    expect(container.textContent ?? '').toContain('ATS Breakdown')
    expect(container.textContent ?? '').toContain('ATS hybrid score 84/100')
    expect(container.textContent ?? '').toContain('selected_resume_file_name')
    expect(container.textContent ?? '').toContain('Final Skills Text')
    expect(container.textContent ?? '').toContain('Skills Audit')
    expect(container.textContent ?? '').toContain('Source Hints')
    expect(container.textContent ?? '').toContain('AI Evidence')
    expect(container.textContent ?? '').not.toContain('Enrichment Result')
    expect(container.textContent ?? '').not.toContain('Merge Notes')
    expect(container.textContent ?? '').not.toContain('AI Merge Notes')
    expect(container.textContent ?? '').not.toContain('Winning Sources')
    expect(container.textContent ?? '').not.toContain('Conflict Notes')
  })
})
