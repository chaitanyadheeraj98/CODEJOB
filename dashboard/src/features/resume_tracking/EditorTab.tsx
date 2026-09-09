import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createFormatProfile,
  createResumeDraft,
  deleteFormatProfile,
  deleteResumeDraft,
  downloadResumeDraft,
  getResumeDraft,
  listFormatProfiles,
  listResumeDrafts,
  listResumeLibrary,
  publishResumeDraft,
  saveResumeDraft,
  updateFormatProfile,
} from './api'
import { formatVariantLabel } from './resumeDisplay'
import type {
  ResumeDraftSummary,
  ResumeExportFormat,
  ResumeFormatProfile,
  ResumeLibraryItem,
  ResumeVariantFormat,
} from './types'

type Props = {
  apiBase: string
}

const FORMATS: Array<{ value: ResumeExportFormat; label: string }> = [
  { value: 'docx', label: 'Word (.docx)' },
  { value: 'pdf', label: 'PDF' },
  { value: 'md', label: 'Markdown' },
]

const code = (resume: ResumeLibraryItem) => resume.variant_code || `R${String(resume.id).padStart(2, '0')}`

const words = (text: string) => text.trim().split(/\s+/).filter(Boolean).length

const editedOn = (iso: string) => {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/**
 * Write a resume, then download it in the shape an employer asked for.
 *
 * Nothing here edits a stored variant. A variant is a file plus the text
 * extracted from it, and the two have to keep saying the same thing: the text is
 * what the matcher and the chatbot argue from, the file is what the recruiter
 * actually receives. Editing a variant's text in place would break that quietly -
 * the app would recommend a resume nobody was ever sent.
 *
 * So editing happens on a draft, and the only thing it produces is a download.
 * That download becomes a variant when it is uploaded back in Manage, where the
 * file and its text are read from the same document.
 */
export default function EditorTab({ apiBase }: Props) {
  const [drafts, setDrafts] = useState<ResumeDraftSummary[]>([])
  const [resumes, setResumes] = useState<ResumeLibraryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const [openId, setOpenId] = useState<number | null>(null)
  // Text is held per draft so switching between drafts to compare their wording
  // does not throw away edits that have not been saved yet.
  const [texts, setTexts] = useState<Record<number, string>>({})
  const [saved, setSaved] = useState<Record<number, string>>({})
  const [name, setName] = useState('')
  const [loadingDraft, setLoadingDraft] = useState(false)
  const [saving, setSaving] = useState(false)
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  const [sourceId, setSourceId] = useState('')
  const [creating, setCreating] = useState(false)

  const [profiles, setProfiles] = useState<ResumeFormatProfile[]>([])
  const [profileId, setProfileId] = useState<number | null>(null)
  const [format, setFormat] = useState<ResumeExportFormat>('docx')
  const [exporting, setExporting] = useState(false)

  const [publishOpen, setPublishOpen] = useState(false)
  const [publishName, setPublishName] = useState('')
  const [publishLabel, setPublishLabel] = useState('')
  const [publishRole, setPublishRole] = useState('')
  const [publishSkills, setPublishSkills] = useState('')
  const [publishing, setPublishing] = useState(false)

  const [profilesOpen, setProfilesOpen] = useState(false)
  const [sample, setSample] = useState<File | null>(null)
  const [profileName, setProfileName] = useState('')
  const [makeDefault, setMakeDefault] = useState(false)
  const [creatingProfile, setCreatingProfile] = useState(false)

  const announce = (message: string) => { setError(''); setNotice(message) }
  const fail = (reason: unknown) => { setNotice(''); setError((reason as Error).message) }

  const refreshProfiles = useCallback(async () => {
    const list = await listFormatProfiles(apiBase)
    setProfiles(list)
    // The default profile is what a download uses when none is picked, so the
    // selector opens on it rather than on "built-in".
    setProfileId((current) => (current != null && list.some((item) => item.id === current) ? current : list.find((item) => item.is_default)?.id ?? null))
    return list
  }, [apiBase])

  useEffect(() => {
    setLoading(true)
    Promise.all([listResumeDrafts(apiBase).then(setDrafts), listResumeLibrary(apiBase).then(setResumes), refreshProfiles()])
      .catch((reason) => setError((reason as Error).message))
      .finally(() => setLoading(false))
  }, [apiBase, refreshProfiles])

  const closePublish = () => {
    setPublishOpen(false)
    setPublishName('')
    setPublishLabel('')
    setPublishRole('')
    setPublishSkills('')
  }

  const open = async (draft: ResumeDraftSummary) => {
    setOpenId(draft.id)
    setName(draft.name)
    setConfirmDeleteId(null)
    closePublish()
    if (texts[draft.id] !== undefined) return
    setLoadingDraft(true)
    try {
      const full = await getResumeDraft(apiBase, draft.id)
      setTexts((current) => ({ ...current, [draft.id]: full.content_markdown }))
      setSaved((current) => ({ ...current, [draft.id]: full.content_markdown }))
    } catch (reason) {
      fail(reason)
    } finally {
      setLoadingDraft(false)
    }
  }

  const start = async (source: ResumeLibraryItem | null) => {
    setCreating(true)
    try {
      const created = await createResumeDraft(apiBase, source ? { source_resume_id: source.id } : {})
      setDrafts(await listResumeDrafts(apiBase))
      setTexts((current) => ({ ...current, [created.id]: created.content_markdown }))
      setSaved((current) => ({ ...current, [created.id]: created.content_markdown }))
      setOpenId(created.id)
      setName(created.name)
      setSourceId('')
      closePublish()
      announce(
        source
          ? `Copied ${code(source)} into a draft. ${code(source)} itself is unchanged — this becomes a variant only when you save it as a new one.`
          : 'Empty draft started. Paste the tailored resume in, then download it.',
      )
    } catch (reason) {
      fail(reason)
    } finally {
      setCreating(false)
    }
  }

  const save = async (draft: ResumeDraftSummary) => {
    setSaving(true)
    try {
      const stored = await saveResumeDraft(apiBase, draft.id, { name, content_markdown: texts[draft.id] ?? '' })
      // Re-seed from the server: it trims the text and the name, so the fields
      // have to show what was stored rather than what was typed.
      setTexts((current) => ({ ...current, [draft.id]: stored.content_markdown }))
      setSaved((current) => ({ ...current, [draft.id]: stored.content_markdown }))
      setName(stored.name)
      setDrafts(await listResumeDrafts(apiBase))
      announce(`“${stored.name}” saved. No stored variant was changed.`)
    } catch (reason) {
      fail(reason)
    } finally {
      setSaving(false)
    }
  }

  const remove = async (draft: ResumeDraftSummary) => {
    try {
      await deleteResumeDraft(apiBase, draft.id)
      setDrafts(await listResumeDrafts(apiBase))
      setOpenId((current) => (current === draft.id ? null : current))
      setConfirmDeleteId(null)
      announce(`“${draft.name}” deleted.`)
    } catch (reason) {
      fail(reason)
    }
  }

  const download = async (draft: ResumeDraftSummary) => {
    setExporting(true)
    try {
      const file = await downloadResumeDraft(apiBase, draft.id, format, profileId)
      announce(`Downloaded ${file}. To use it as a resume, upload it in Manage — or use “Save as new variant” to do both in one step.`)
    } catch (reason) {
      fail(reason)
    } finally {
      setExporting(false)
    }
  }

  const publish = async (draft: ResumeDraftSummary) => {
    setPublishing(true)
    try {
      const created = await publishResumeDraft(apiBase, draft.id, {
        file_name: publishName,
        variant_label: publishLabel,
        primary_role: publishRole,
        structured_skills_text: publishSkills,
        fmt: format === 'md' ? 'docx' : (format as ResumeVariantFormat),
        profile_id: profileId,
      })
      // The new variant belongs in the library list straight away: it is a
      // legitimate source for the next draft.
      setResumes(await listResumeLibrary(apiBase))
      closePublish()
      announce(
        `${created.variant_code} added as ${created.file_name} (v${created.version}). ` +
        'Its text was read back out of that file, so the two agree.',
      )
    } catch (reason) {
      fail(reason)
    } finally {
      setPublishing(false)
    }
  }

  const addProfile = async () => {
    if (!sample) return
    setCreatingProfile(true)
    try {
      const created = await createFormatProfile(apiBase, sample, profileName, makeDefault)
      await refreshProfiles()
      setProfileId(created.id)
      setSample(null)
      setProfileName('')
      setMakeDefault(false)
      announce(`Measured ${created.source_file_name} — saved as “${created.name}”.`)
    } catch (reason) {
      fail(reason)
    } finally {
      setCreatingProfile(false)
    }
  }

  const setDefaultProfile = async (profile: ResumeFormatProfile) => {
    try {
      await updateFormatProfile(apiBase, profile.id, { is_default: true })
      await refreshProfiles()
      announce(`“${profile.name}” is the default layout.`)
    } catch (reason) {
      fail(reason)
    }
  }

  const removeProfile = async (profile: ResumeFormatProfile) => {
    try {
      await deleteFormatProfile(apiBase, profile.id)
      const list = await refreshProfiles()
      if (!list.length) setProfileId(null)
      announce(`“${profile.name}” deleted. Downloads fall back to the built-in layout.`)
    } catch (reason) {
      fail(reason)
    }
  }

  const selected = drafts.find((item) => item.id === openId) ?? null
  const text = openId == null ? '' : texts[openId] ?? ''
  const dirty = openId != null && texts[openId] !== undefined && (texts[openId] !== saved[openId] || name !== selected?.name)
  const activeProfile = profiles.find((item) => item.id === profileId) ?? null
  // The one rule the form enforces client-side, so the reason is visible before
  // the request rather than only in the refusal that comes back.
  const nameClashesDraft = publishName.trim().length > 0
    && publishName.trim().toLowerCase() === (selected?.name ?? '').trim().toLowerCase()
  const canPublish = publishName.trim().length > 0 && !nameClashesDraft && !dirty && !publishing
  const sourceOptions = useMemo(() => resumes.filter((item) => item.is_enabled || item.is_current), [resumes])

  return (
    <section className="resumeEditorPanel">
      <header className="resumeLibraryIntro">
        <div>
          <h2>Resume editor</h2>
          <p className="subtle">
            Write a tailored resume and download it as Word, PDF or markdown. A format profile is measured
            from an employer's own sample, so the download comes out in their layout.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setProfilesOpen((value) => !value)}
          aria-expanded={profilesOpen}
        >
          {profilesOpen ? 'Hide format profiles' : `Format profiles (${profiles.length})`}
        </button>
      </header>

      <p className="resumeEditorRule">
        Editing here never changes a stored variant. A variant's text is what the matcher and the chatbot
        read, and its file is what the recruiter receives — so a draft becomes a resume only when you
        <strong> save it as a new variant</strong>, which renders the file and reads its text back out of
        that same file. Downloading it and uploading it in <strong>Manage</strong> does the same thing by
        hand.
      </p>

      {error ? <p className="errorText" role="alert">{error}</p> : null}
      {notice ? <p className="resumeLibraryNotice" aria-live="polite">{notice}</p> : null}

      {profilesOpen ? (
        <section className="resumeFormatProfiles" aria-label="Format profiles">
          <p className="subtle">
            Upload a resume laid out the way an employer wants it. Margins, fonts and the skills-table
            divider are measured from the file itself; the section headings that get a rule above them are
            read from its text and checked against the document before they are stored.
          </p>
          <div className="resumeFormatProfileForm">
            <label className="resumeLibraryFile">
              <span>Sample resume</span>
              <input
                type="file"
                accept=".docx,.doc,.pdf,.md,.txt"
                disabled={creatingProfile}
                onChange={(event) => setSample(event.target.files?.[0] ?? null)}
              />
              <small className="subtle">A .docx is measured most accurately — other formats use the built-in geometry.</small>
            </label>
            <label className="resumeField">
              <span>Profile name</span>
              <input
                value={profileName}
                placeholder="Vendor A layout"
                onChange={(event) => setProfileName(event.target.value)}
              />
            </label>
            <label className="resumeFormatProfileDefault">
              <input
                type="checkbox"
                checked={makeDefault}
                onChange={(event) => setMakeDefault(event.target.checked)}
              />
              <span>Use for downloads by default</span>
            </label>
            <button type="button" className="primaryButton" onClick={addProfile} disabled={!sample || creatingProfile}>
              {creatingProfile ? 'Measuring...' : 'Measure and save'}
            </button>
          </div>

          {profiles.length ? (
            <ul className="resumeFormatProfileList">
              {profiles.map((profile) => (
                <li key={profile.id}>
                  <div className="resumeFormatProfileIdentity">
                    <strong>{profile.name}</strong>
                    {profile.is_default ? <span className="resumeLibraryBadge on">Default</span> : null}
                    <span className="subtle">
                      {profile.spec.font_family} {profile.spec.body_font_size}pt · margins{' '}
                      {profile.spec.margin_left_inches}"/{profile.spec.margin_right_inches}" · divider{' '}
                      {profile.spec.skills_divider_inches}"
                    </span>
                    <span className="subtle">
                      {profile.spec.rule_before_sections.length
                        ? `Rules above: ${profile.spec.rule_before_sections.join(', ')}`
                        : 'No section rules'}
                    </span>
                  </div>
                  <div className="resumeFormatProfileActions">
                    {profile.is_default ? null : (
                      <button type="button" onClick={() => setDefaultProfile(profile)}>Make default</button>
                    )}
                    <button type="button" className="dangerButton" onClick={() => removeProfile(profile)}>Delete</button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="subtle">No profiles yet. Downloads use the built-in layout until you add one.</p>
          )}
        </section>
      ) : null}

      {loading ? <p className="subtle">Loading drafts...</p> : null}

      {!loading ? (
        <div className="resumeEditorBody">
          <aside className="resumeEditorList">
            <div className="resumeEditorStart">
              <button type="button" className="primaryButton" onClick={() => start(null)} disabled={creating}>
                {creating ? 'Starting...' : 'New draft'}
              </button>
              <label className="resumeField">
                <span>Or copy a variant</span>
                <select
                  value={sourceId}
                  disabled={creating || !sourceOptions.length}
                  onChange={(event) => {
                    setSourceId(event.target.value)
                    const source = sourceOptions.find((item) => String(item.id) === event.target.value)
                    if (source) start(source)
                  }}
                >
                  <option value="">Start from a variant...</option>
                  {sourceOptions.map((resume) => (
                    <option key={resume.id} value={resume.id}>
                      {code(resume)} · {formatVariantLabel(resume.variant_label) || resume.file_name}
                    </option>
                  ))}
                </select>
                <small className="subtle">Copies its text. The variant stays exactly as it is.</small>
              </label>
            </div>

            {drafts.map((draft) => (
              <button
                type="button"
                key={draft.id}
                className={`resumeEditorListItem${draft.id === openId ? ' active' : ''}`}
                aria-pressed={draft.id === openId}
                onClick={() => open(draft)}
              >
                <span className="resumeEditorListText">
                  <strong>{draft.name}</strong>
                  <span className="subtle">
                    {draft.source_variant_code ? `from ${draft.source_variant_code} · ` : ''}
                    {draft.character_count} characters · {editedOn(draft.updated_at)}
                  </span>
                </span>
                {texts[draft.id] !== undefined && texts[draft.id] !== saved[draft.id] ? (
                  <span className="resumeEditorDirtyDot" aria-label="Unsaved changes">●</span>
                ) : null}
              </button>
            ))}
            {!drafts.length ? <p className="subtle">No drafts yet.</p> : null}
          </aside>

          <div className="resumeEditorMain">
            {!selected ? (
              <p className="subtle">Start a draft, or pick one to keep working on it.</p>
            ) : loadingDraft ? (
              <p className="subtle">Reading “{selected.name}”...</p>
            ) : (
              <>
                <div className="resumeEditorHeading">
                  <label className="resumeField resumeEditorName">
                    <span>Draft name</span>
                    <input
                      value={name}
                      placeholder="Vendor A / Java full stack"
                      onChange={(event) => setName(event.target.value)}
                    />
                  </label>
                  <span className="subtle">{words(text)} words · {text.length} characters{dirty ? ' · unsaved' : ''}</span>
                </div>
                <label className="resumeField">
                  <span className="visuallyHidden">Draft text</span>
                  <textarea
                    className="resumeEditorTextarea"
                    rows={24}
                    spellCheck
                    value={text}
                    placeholder={'# Your Name\nCity | Phone | Email\n\n## Summary\n...'}
                    onChange={(event) => setTexts((current) => ({ ...current, [selected.id]: event.target.value }))}
                  />
                </label>
                <p className="subtle">
                  Markdown: <code>#</code> for the name, <code>##</code> for sections, <code>-</code> for bullets,
                  <code>**bold**</code> for keywords, and a pipe table for skills.
                </p>

                {publishOpen ? (
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
                      <button type="button" className="primaryButton" onClick={() => publish(selected)} disabled={!canPublish}>
                        {publishing ? 'Creating variant...' : 'Create variant'}
                      </button>
                    </div>
                  </section>
                ) : null}

                <div className="resumeEditorFoot">
                  <div className="resumeEditorExport">
                    <label className="resumeField">
                      <span>Format</span>
                      <select value={format} onChange={(event) => setFormat(event.target.value as ResumeExportFormat)}>
                        {FORMATS.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    </label>
                    <label className="resumeField">
                      <span>Layout</span>
                      <select
                        value={profileId ?? ''}
                        onChange={(event) => setProfileId(event.target.value ? Number(event.target.value) : null)}
                      >
                        <option value="">Built-in layout</option>
                        {profiles.map((profile) => (
                          <option key={profile.id} value={profile.id}>
                            {profile.name}{profile.is_default ? ' (default)' : ''}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button type="button" onClick={() => download(selected)} disabled={exporting || dirty}>
                      {exporting ? 'Building...' : 'Download'}
                    </button>
                    <button
                      type="button"
                      onClick={() => (publishOpen ? closePublish() : setPublishOpen(true))}
                      aria-expanded={publishOpen}
                      disabled={dirty}
                    >
                      {publishOpen ? 'Cancel' : 'Save as new variant'}
                    </button>
                  </div>
                  <div className="resumeEditorSave">
                    {dirty ? <span className="subtle">Save before downloading — the download renders the stored draft.</span> : null}
                    {!dirty && format !== 'md' ? (
                      <span className="subtle">
                        Rendering with {activeProfile ? `“${activeProfile.name}”` : 'the built-in layout'}.
                      </span>
                    ) : null}
                    {confirmDeleteId === selected.id ? (
                      <>
                        <span className="subtle">Delete “{selected.name}”?</span>
                        <button type="button" onClick={() => setConfirmDeleteId(null)}>Keep</button>
                        <button type="button" className="dangerButton" onClick={() => remove(selected)}>Delete</button>
                      </>
                    ) : (
                      <button type="button" className="dangerButton" onClick={() => setConfirmDeleteId(selected.id)}>Delete draft</button>
                    )}
                    <button type="button" className="primaryButton" onClick={() => save(selected)} disabled={saving || !dirty}>
                      {saving ? 'Saving...' : 'Save draft'}
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      ) : null}
    </section>
  )
}
