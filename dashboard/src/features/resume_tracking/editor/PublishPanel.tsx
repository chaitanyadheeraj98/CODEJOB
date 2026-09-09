import { useState } from 'react'
import type { ResumeDraftSummary, ResumeExportFormat, ResumeFormatProfile } from '../types'
const FORMATS: Array<{ value: ResumeExportFormat; label: string }> = [
  { value: 'docx', label: 'Word (.docx)' },
  { value: 'pdf', label: 'PDF' },
  { value: 'md', label: 'Markdown' },
]


type Props = {
  selected: ResumeDraftSummary
  format: ResumeExportFormat
  activeProfile: ResumeFormatProfile | null
  dirty: boolean
  publishing: boolean
  publish: (fields: { file_name: string; variant_label: string; primary_role: string; structured_skills_text: string }) => void
}

export default function PublishPanel({ selected, format, activeProfile, dirty, publishing, publish }: Props) {
  const [publishName, setPublishName] = useState('')
  const [publishLabel, setPublishLabel] = useState('')
  const [publishRole, setPublishRole] = useState('')
  const [publishSkills, setPublishSkills] = useState('')
  const nameClashesDraft = publishName.trim().length > 0 && publishName.trim().toLowerCase() === selected.name.trim().toLowerCase()
  const canPublish = publishName.trim().length > 0 && !nameClashesDraft && !dirty && !publishing
  return (<>
                  <section className="resumeEditorPublish" aria-label="Save as a new variant">
                    <h3>Save as a new variant</h3>
                    <p className="subtle">
                      The draft is rendered with the layout selected below, stored as a file, and its text
                      is read back out of that file — the same way an upload is handled, so extraction and
                      embedding are identical. “{selected.name}” stays here as a draft, and no existing
                      variant changes.
                    </p>
                    <div className="resumeFieldGrid">
                      <label className="resumeField">
                        <span>New variant name</span>
                        <input
                          value={publishName}
                          placeholder="Vendor A Java Full Stack"
                          onChange={(event) => setPublishName(event.target.value)}
                        />
                        <small className={nameClashesDraft ? 'errorText' : 'subtle'}>
                          {nameClashesDraft
                            ? `That is the draft's own name. Give the variant a different one so the two do not get confused.`
                            : 'Must differ from the draft name. Spaces become underscores in the filename.'}
                        </small>
                      </label>
                      <label className="resumeField">
                        <span>Variant label</span>
                        <input
                          value={publishLabel}
                          placeholder="Java / Banking"
                          onChange={(event) => setPublishLabel(event.target.value)}
                        />
                      </label>
                      <label className="resumeField">
                        <span>Primary role</span>
                        <input
                          value={publishRole}
                          placeholder="Senior Java Developer"
                          onChange={(event) => setPublishRole(event.target.value)}
                        />
                      </label>
                      <label className="resumeField">
                        <span>Structured skills</span>
                        <input
                          value={publishSkills}
                          placeholder="Java, Spring Boot, AWS"
                          onChange={(event) => setPublishSkills(event.target.value)}
                        />
                      </label>
                    </div>
                    <div className="resumeEditorPublishFoot">
                      <span className="subtle">
                        Saving as {format === 'md' ? 'Word (.docx)' : FORMATS.find((item) => item.value === format)?.label}
                        {' '}in {activeProfile ? `“${activeProfile.name}”` : 'the built-in layout'}.
                        {format === 'md' ? ' Markdown is not a document a recruiter can read, so Word is used.' : ''}
                      </span>
                      <button type="button" className="primaryButton" onClick={() => publish({ file_name: publishName, variant_label: publishLabel, primary_role: publishRole, structured_skills_text: publishSkills })} disabled={!canPublish}>
                        {publishing ? 'Creating variant...' : 'Create variant'}
                      </button>
                    </div>
                  </section>
  </>)
}
