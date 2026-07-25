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
          resumePickerBreakdown={null}
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
          resumePickerBreakdown={{
            mandatory_gate_status: 'needs_review',
            mandatory_coverage: 0.75,
            satisfied_required_groups: ['Database requirement', 'Java 17/21'],
            unmet_required_groups: ['Messaging requirement'],
            matched_alternatives: { 'Database requirement': 'PostgreSQL' },
            version_unverified: ['Java 17/21'],
          }}
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
            structured_requirements: {
              schema_version: 1,
              required_groups: [
                {
                  group_id: 'req-1',
                  mode: 'all',
                  skills: [{ canonical_name: 'Java', versions: ['17', '21'] }],
                },
                {
                  group_id: 'req-2',
                  mode: 'any',
                  skills: [{ canonical_name: 'Kafka' }, { canonical_name: 'JMS' }],
                },
              ],
              preferred_groups: [
                {
                  group_id: 'pref-1',
                  mode: 'any',
                  skills: [{ canonical_name: 'AWS' }, { canonical_name: 'Azure' }],
                },
              ],
              informational_groups: [],
              experience_years_min: 8,
              local_required: true,
              work_mode: 'Onsite',
              locations: ['San Antonio, TX', 'Remote, USA'],
              preferred_domains: ['USAA'],
            },
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
    expect(container.textContent ?? '').toContain('Required Requirements')
    expect(container.textContent ?? '').toContain('One required')
    expect(container.textContent ?? '').toContain('Kafka or JMS')
    expect(container.textContent ?? '').toContain('Preferred Requirements')
    expect(container.textContent ?? '').toContain('AWS or Azure')
    expect(container.textContent ?? '').toContain('Constraints')
    expect(container.textContent ?? '').toContain('8+ years')
    expect(container.textContent ?? '').toContain('San Antonio, TX')
    expect(container.textContent ?? '').toContain('Required')
    expect(container.textContent ?? '').toContain('Onsite')
    expect(container.textContent ?? '').toContain('USAA')
    expect(container.textContent ?? '').toContain('Resume-Picker Result')
    expect(container.textContent ?? '').toContain('Database requirement through PostgreSQL')
    expect(container.textContent ?? '').toContain('Messaging requirement')
    expect(container.textContent ?? '').toContain('Java 17/21')
    expect(container.textContent ?? '').toContain('Matched Alternatives')
    expect(container.textContent ?? '').toContain('PostgreSQL')
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
    expect(container.textContent ?? '').toContain('Required Requirements')
    expect(container.textContent ?? '').toContain('All required: Java 17/21')
    expect(container.textContent ?? '').toContain('One required: Kafka or JMS')
    expect(container.textContent ?? '').toContain('Preferred Requirements')
    expect(container.textContent ?? '').toContain('Preferred: AWS or Azure')
    expect(container.textContent ?? '').toContain('Constraints')
    expect(container.textContent ?? '').toContain('Experience: 8+ years')
    expect(container.textContent ?? '').toContain('Location: San Antonio, TX')
    expect(container.textContent ?? '').toContain('Resume-Picker Result')
    expect(container.textContent ?? '').toContain('Satisfied: Database requirement through PostgreSQL; Java 17/21')
    expect(container.textContent ?? '').toContain('Matched alternatives: PostgreSQL')
  })

  it('renders safely when structured payload data is missing or malformed', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    act(() => {
      root.render(
        <ParserDetailsPanel
          candidateId={7}
          source="gmail"
          atsScore={null}
          atsSource={null}
          atsSummary={null}
          atsBreakdown={{}}
          resumePickerBreakdown={{ mandatory_gate_status: 'fail', matched_alternatives: 'bad-shape' as unknown as Record<string, unknown> }}
          expanded={true}
          onToggle={() => {}}
          parserDetails={{
            parser_version: 'v1',
            source: 'gmail',
            parser_mode: 'base_only',
            fallback_used: false,
            merged_result: { role: 'Backend Engineer', location: 'Remote', skills_text: 'Java, SQL' },
            structured_requirements: {
              required_groups: 'bad-shape',
              preferred_groups: null,
              locations: ['Remote', 'not a location\nfragment'],
            } as unknown as Record<string, unknown>,
          }}
        />,
      )
    })

    expect(container.textContent ?? '').toContain('Required Requirements')
    expect(container.textContent ?? '').toContain('Preferred Requirements')
    expect(container.textContent ?? '').toContain('Constraints')
    expect(container.textContent ?? '').toContain('Resume-Picker Result')
    expect(container.textContent ?? '').toContain('Final Skills Text')
    expect(container.textContent ?? '').toContain('Java')
  })

  it('preserves the AI truncation fallback warning in readable and raw modes', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    act(() => {
      root.render(
        <ParserDetailsPanel
          candidateId={78}
          source="nvoids"
          atsScore={73}
          atsSource="hybrid_structured_only"
          atsSummary="ATS hybrid score 73/100"
          atsBreakdown={{ raw_overlap: 0.28, intent_match: 0.65 }}
          resumePickerBreakdown={null}
          expanded={true}
          onToggle={() => {}}
          parserDetails={{
            parser_version: 'ai_fallback_v2',
            source: 'nvoids',
            parser_mode: 'ai_fallback',
            fallback_used: true,
            merged_result: { role: 'Java Developer', skills_text: 'Java, Spring Boot' },
            base_parser_result: { role: 'Java Developer', skills_text: 'Java, Spring Boot' },
            ai_extractor_result: {
              error: 'truncated JSON content',
              evidence: { extractor_error: ['truncated JSON content'] },
            },
            parser_warning: 'AI extractor failed; base parser fallback used: truncated JSON content',
            approved_skills_text: 'Java, Spring Boot',
            unknown_skills: [],
          }}
        />,
      )
    })

    expect(container.textContent ?? '').toContain('Mode: ai_fallback')
    expect(container.textContent ?? '').toContain('Fallback: Yes')
    expect(container.textContent ?? '').toContain(
      'AI extractor failed; base parser fallback used: truncated JSON content',
    )

    const rawToggle = Array.from(container.querySelectorAll('button')).find(
      (node) => node.textContent === 'Raw v1',
    ) as HTMLButtonElement | undefined
    act(() => {
      rawToggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(container.textContent ?? '').toContain('fallback_used: true')
    expect(container.textContent ?? '').toContain('error: truncated JSON content')
    expect(container.textContent ?? '').not.toContain('system secret')
    expect(container.textContent ?? '').not.toContain('user secret')
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
