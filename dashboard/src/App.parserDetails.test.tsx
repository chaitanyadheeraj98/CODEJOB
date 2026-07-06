// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ParserDetailsPanel, ResumePickerPanel } from './App'

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
    expect(container.querySelectorAll('.parserDetailsCardBody').length).toBeGreaterThanOrEqual(5)
    expect(container.querySelectorAll('.parserChip').length).toBeGreaterThanOrEqual(4)
    expect(container.querySelectorAll('.parserMetricRow').length).toBeGreaterThanOrEqual(4)
    expect(container.querySelectorAll('.parserStatusBadge').length).toBeGreaterThanOrEqual(2)
    expect(container.textContent ?? '').toContain('Raw Debug')
    expect(container.textContent ?? '').toContain('Readable v3')
    expect(container.textContent ?? '').toContain('Raw v1')
    expect(container.textContent ?? '').not.toContain('Enrichment Result')
    expect(container.textContent ?? '').not.toContain('Merge Notes')
    expect(container.textContent ?? '').not.toContain('AI Merge Notes')
    expect(container.textContent ?? '').not.toContain('Winning Sources')
    expect(container.textContent ?? '').not.toContain('Conflict Notes')

    const rawToggle = Array.from(container.querySelectorAll('button')).find((node) => node.textContent === 'Raw v1') as HTMLButtonElement | undefined
    expect(rawToggle).toBeDefined()

    act(() => {
      rawToggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(container.querySelectorAll('.parserLegacyPre').length).toBeGreaterThanOrEqual(5)
    expect(container.textContent ?? '').toContain('fallback_used: false')
    expect(container.textContent ?? '').toContain('selected_resume_file_name: resume.docx')
    expect(container.textContent ?? '').toContain('canonical_title: Full Stack Developer')
    expect(container.textContent ?? '').toContain('confidence: 0.87')
    expect(container.textContent ?? '').toContain('evidence: title: Full Stack Developer')
  })

  it('renders resume picker diagnostics and top alternatives', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    act(() => {
      root.render(
        <ResumePickerPanel
          candidate={{
            id: 3988,
            subject: 'Java Full Stack Developer',
            sender: 'recruiter@example.com',
            body: 'body',
            role: 'Java Full Stack Developer',
            location: 'Irving, TX',
            salary_text: '',
            skills_text: 'Java, Oracle, PL/SQL',
            gmail_message_url: null,
            recipient_email: 'to@example.com',
            cc_email: 'cc@example.com',
            routing_status: 'safe',
            routing_confidence: 0.9,
            routing_reason: 'safe',
            routing_evidence: [],
            routing_candidates: [],
            routing_confirmed: false,
            ai_score: 0.64,
            ats_score: 56.13,
            ats_score_source: 'hybrid_structured_plus_semantic',
            ats_summary: 'ATS hybrid score 56/100',
            ats_breakdown: {},
            resume_picker_score: 0.81,
            resume_picker_reason: 'Final 0.81; ai=0.64; ats=56.13; priority=0.90; role_fit=0.72; matched=Oracle, PL/SQL',
            resume_picker_breakdown: {
              matched_priority_skills: ['Oracle', 'PL/SQL', 'AI tools'],
              missing_priority_skills: ['OpenShift'],
            },
            resume_picker_candidates: {
              rankings: [
                {
                  resume_file_name: 'selected.docx',
                  final_resume_score: 0.81,
                  selection_reason: 'winner',
                },
                {
                  resume_file_name: 'alternative-1.docx',
                  final_resume_score: 0.77,
                  selection_reason: 'close second',
                },
                {
                  resume_file_name: 'alternative-2.docx',
                  final_resume_score: 0.74,
                  selection_reason: 'third',
                },
              ],
            },
            draft_reply: 'draft',
            draft_source: 'rules_only',
            draft_model: null,
            draft_ai_error: null,
            draft_resume_context_status: 'rules_only',
            draft_quality: null,
            resume_file_name: 'selected.docx',
            parser_details: null,
            attachment_file_names: [],
            state: 'needs_review',
            last_error: null,
            source: 'gmail',
            external_message_id: 'm-3988',
            external_thread_id: 't-3988',
            gmail_sent_id: null,
          }}
        />,
      )
    })

    expect(container.textContent ?? '').toContain('Resume Picker:')
    expect(container.textContent ?? '').toContain('selected.docx')
    expect(container.textContent ?? '').toContain('Final Score:')
    expect(container.textContent ?? '').toContain('81')
    expect(container.textContent ?? '').toContain('Matched Priority Skills:')
    expect(container.textContent ?? '').toContain('Oracle, PL/SQL, AI tools')
    expect(container.textContent ?? '').toContain('Missing Priority Skills:')
    expect(container.textContent ?? '').toContain('OpenShift')
    expect(container.textContent ?? '').toContain('Top Alternatives:')
    expect(container.textContent ?? '').toContain('alternative-1.docx')
    expect(container.textContent ?? '').toContain('alternative-2.docx')
  })
})
