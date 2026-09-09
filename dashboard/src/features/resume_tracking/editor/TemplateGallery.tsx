import { useState } from 'react'
import type { ResumeFormatProfile, ResumeFormatSpec } from '../types'
import { DEFAULT_SPEC } from './ResumePreview'

const TEMPLATES: Array<[string, Partial<ResumeFormatSpec>]> = [
  ['Employer classic', {}],
  ['Clean', { font_family: 'Calibri', rule_before_sections: [], margin_left_inches: 0.65, margin_right_inches: 0.65 }],
  ['Modern', { accent_color: '#245b78', heading_uppercase: true, section_spacing_pt: 5 }],
  ['Serif', { font_family: 'Times New Roman', body_font_size: 11, name_font_size: 18 }],
  ['Compact', { compact: true, font_family: 'Arial' }],
  ['A4 classic', { page_size: 'A4', margin_left_inches: 0.6, margin_right_inches: 0.6 }],
  ['Editorial', { font_family: 'Georgia', accent_color: '#574039', name_font_size: 20, section_spacing_pt: 8 }],
  // The sidebar names default headings this app already writes. They are a
  // starting point, not a guess at the draft: a heading that is not in the
  // document simply stays out of the sidebar.
  ['Sidebar', {
    layout: 'two-column', sidebar_width_inches: 2.2, justify_body: false,
    sidebar_sections: ['Skills', 'Certifications', 'Education Details'],
    margin_left_inches: 0.5, rule_before_sections: ['Summary', 'Experiences'],
  }],
  ['Sidebar accent', {
    layout: 'two-column', sidebar_width_inches: 2.4, justify_body: false, heading_uppercase: true,
    sidebar_sections: ['Skills', 'Certifications', 'Education Details'],
    accent_color: '#245b78', margin_left_inches: 0.5, rule_before_sections: [],
  }],
]

function Thumbnail({ url, name }: { url: string; name: string }) {
  const [failed, setFailed] = useState(false)
  return failed ? <span className="subtle">Thumbnail unavailable</span> : <img src={url} loading="lazy" alt={`${name} sample page`} onError={() => setFailed(true)} />
}

type Props = { apiBase: string; profiles: ResumeFormatProfile[]; profileId: number | null;
  onProfile: (id: number | null) => void; onTemplate: (spec: ResumeFormatSpec, name: string) => void }

export default function TemplateGallery({ apiBase, profiles, profileId, onProfile, onTemplate }: Props) {
  return <details className="resumeTemplateGallery"><summary>Templates and employer layouts</summary>
    <div className="resumeTemplateCards">
      {TEMPLATES.map(([name, changes]) => <button type="button" key={name} onClick={() => onTemplate({ ...DEFAULT_SPEC, ...changes }, name)}>
        {/* A miniature of the page, not text to read - the card is named by the
            heading under it, so the sample is hidden from assistive tech rather
            than announced as a resume for someone called Alex Rivera. The
            miniature shows the column shape, which is the difference between
            these presets that a list of numbers would not convey. */}
        <span className="resumeTemplateSwatch" aria-hidden="true" style={{ fontFamily: changes.font_family ?? DEFAULT_SPEC.font_family, color: changes.accent_color || '#171717' }}>
          <strong>Alex Rivera</strong>
          {changes.layout === 'two-column' ? (
            <span className="resumeTemplateSplit">
              <span><span>SUMMARY</span><i /><i /><span>EXPERIENCE</span><i /><i /></span>
              <span><span>SKILLS</span><i /><i /><i /></span>
            </span>
          ) : (
            <><span>SUMMARY</span><i /><i /><span>EXPERIENCE</span><i /><i /></>
          )}
        </span><strong>{name}</strong>
      </button>)}
      {profiles.map((profile) => <button type="button" key={profile.id} aria-pressed={profileId === profile.id} onClick={() => onProfile(profile.id)}>
        <Thumbnail key={profile.updated_at} name={profile.name} url={`${apiBase}/resume-editor/profiles/${profile.id}/preview.png?v=${encodeURIComponent(profile.updated_at)}`} />
        <strong>{profile.name}</strong>
      </button>)}
    </div>
  </details>
}
