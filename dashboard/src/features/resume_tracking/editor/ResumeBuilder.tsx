import DraftList from './DraftList'
import MarkdownPane from './MarkdownPane'
import PublishPanel from './PublishPanel'
import ResumePreview, { DEFAULT_SPEC, ExactPreview } from './ResumePreview'
import TemplateGallery from './TemplateGallery'
import FormattingPanel from './FormattingPanel'
import SectionOutline from './SectionOutline'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  createFormatProfile,
  createLayoutProfile,
  createResumeDraft,
  deleteFormatProfile,
  deleteResumeDraft,
  reorderResumeDraftSection,
  downloadResumeDraft,
  getResumeDraft,
  listFormatProfiles,
  listResumeDrafts,
  listResumeLibrary,
  publishResumeDraft,
  saveResumeDraft,
  updateFormatProfile,
} from '../api'
import type {
  ResumeDraftSummary,
  ResumeDraftSection,
  ResumeFormatSpec,
  ResumeExportFormat,
  ResumeFormatProfile,
  ResumeLibraryItem,
  ResumeVariantFormat,
} from '../types'

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
export default function ResumeBuilder({ apiBase }: Props) {
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
  const [sections, setSections] = useState<Record<number, ResumeDraftSection[]>>({})
  const [previewSpec, setPreviewSpec] = useState<ResumeFormatSpec | null>(null)
  const [draftProfiles, setDraftProfiles] = useState<Record<number, number | null>>({})
  const [savedProfiles, setSavedProfiles] = useState<Record<number, number | null>>({})
  const [draftSpecs, setDraftSpecs] = useState<Record<number, ResumeFormatSpec | null>>({})
  const [layoutName, setLayoutName] = useState('')
  const [savingLayout, setSavingLayout] = useState(false)
  // Wide by default. The app's own sidebar leaves the builder about 1000px, so
  // a letter page fitted into a third of that lands near 35% - legible as a
  // shape and nothing else. Full width under the editor renders it at 100%,
  // and a readable page one scroll down beats an unreadable one alongside.
  const [previewWide, setPreviewWide] = useState(true)
  const [movingSection, setMovingSection] = useState('')
  const openRef = useRef<number | null>(null)
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
  }

  const open = async (draft: ResumeDraftSummary) => {
    openRef.current = draft.id
    setPreviewSpec(draftSpecs[draft.id] ?? null)
    if (draft.id in draftProfiles) setProfileId(draftProfiles[draft.id])
    setOpenId(draft.id)
    setName(draft.name)
    setConfirmDeleteId(null)
    closePublish()
    if (texts[draft.id] !== undefined) { setLoadingDraft(false); return }
    setLoadingDraft(true)
    try {
      const full = await getResumeDraft(apiBase, draft.id)
      setTexts((current) => ({ ...current, [draft.id]: full.content_markdown }))
      setSaved((current) => ({ ...current, [draft.id]: full.content_markdown }))
      setSections((current) => ({ ...current, [draft.id]: full.sections ?? [] }))
      const binding = full.format_profile_id === undefined ? profileId : (profiles.some((p) => p.id === full.format_profile_id) ? full.format_profile_id : null)
      setDraftProfiles((current) => ({ ...current, [draft.id]: binding }))
      setSavedProfiles((current) => ({ ...current, [draft.id]: binding }))
      if (openRef.current === draft.id) setProfileId(binding)
    } catch (reason) {
      fail(reason)
    } finally {
      if (openRef.current === draft.id) setLoadingDraft(false)
    }
  }

  const start = async (source: ResumeLibraryItem | null) => {
    setCreating(true)
    try {
      const created = await createResumeDraft(apiBase, source ? { source_resume_id: source.id } : {})
      setDrafts(await listResumeDrafts(apiBase))
      setTexts((current) => ({ ...current, [created.id]: created.content_markdown }))
      setSaved((current) => ({ ...current, [created.id]: created.content_markdown }))
      setSections((current) => ({ ...current, [created.id]: created.sections ?? [] }))
      setPreviewSpec(null)
      openRef.current = created.id
      const binding = created.format_profile_id === undefined ? profileId : created.format_profile_id
      setProfileId(binding)
      setDraftProfiles((current) => ({ ...current, [created.id]: binding }))
      setSavedProfiles((current) => ({ ...current, [created.id]: binding }))
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
      const submittedText = texts[draft.id] ?? ''
      const stored = await saveResumeDraft(apiBase, draft.id, { name, content_markdown: submittedText, format_profile_id: profileId })
      // Re-seed from the server: it trims the text and the name, so the fields
      // have to show what was stored rather than what was typed.
      setTexts((current) => current[draft.id] === submittedText ? ({ ...current, [draft.id]: stored.content_markdown }) : current)
      setSaved((current) => ({ ...current, [draft.id]: stored.content_markdown }))
      setSections((current) => ({ ...current, [draft.id]: stored.sections ?? [] }))
      setSavedProfiles((current) => ({ ...current, [draft.id]: profileId }))
      if (openRef.current === draft.id) setName((current) => current === name ? stored.name : current)
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

  const publish = async (draft: ResumeDraftSummary, fields: { file_name: string; variant_label: string; primary_role: string; structured_skills_text: string }) => {
    setPublishing(true)
    try {
      const created = await publishResumeDraft(apiBase, draft.id, {
        ...fields,
        fmt: format === 'md' ? 'docx' : (format as ResumeVariantFormat),
        profile_id: profileId ?? 0,
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
  const dirty = openId != null && texts[openId] !== undefined && (texts[openId] !== saved[openId] || name !== selected?.name || profileId !== savedProfiles[openId])
  const activeProfile = profiles.find((item) => item.id === profileId) ?? null
  const activeSpec = { ...DEFAULT_SPEC, ...(previewSpec ?? activeProfile?.spec) }
  const layoutDirty = previewSpec != null
  const selectProfile = (id: number | null) => {
    setProfileId(id)
    setPreviewSpec(null)
    if (openId != null) {
      setDraftProfiles((current) => ({ ...current, [openId]: id }))
      setDraftSpecs((current) => ({ ...current, [openId]: null }))
    }
  }
  const editSpec = (spec: ResumeFormatSpec) => {
    setPreviewSpec(spec)
    if (openId != null) setDraftSpecs((current) => ({ ...current, [openId]: spec }))
  }
  const saveLayout = async () => {
    if (openId == null || !layoutName.trim()) return
    setSavingLayout(true)
    try {
      const created = await createLayoutProfile(apiBase, layoutName, activeSpec)
      setProfiles((current) => [...current, created])
      setDraftProfiles((current) => ({ ...current, [openId]: created.id }))
      setDraftSpecs((current) => ({ ...current, [openId]: null }))
      if (openRef.current === openId) { setProfileId(created.id); setPreviewSpec(null) }
      announce('Layout saved as a new profile. Save the draft to remember this layout.')
    } catch (reason) { fail(reason) } finally { setSavingLayout(false) }
  }
  /**
   * Reorder a section on the server and take back what it returns.
   *
   * The move is not applied locally first. The server owns the splitter, so it
   * is the only side that knows where a section ends, and echoing its text back
   * into the editor is what keeps the outline, the textarea and the draft in
   * agreement. Refused while there are unsaved edits: the section spans the
   * server computed are for the text it has, not the text on screen.
   */
  const moveSection = async (section: ResumeDraftSection, direction: 'up' | 'down') => {
    if (openId == null || movingSection) return
    setMovingSection(section.heading)
    try {
      const stored = await reorderResumeDraftSection(apiBase, openId, { section: section.heading, direction })
      setTexts((current) => ({ ...current, [openId]: stored.content_markdown }))
      setSaved((current) => ({ ...current, [openId]: stored.content_markdown }))
      setSections((current) => ({ ...current, [openId]: stored.sections ?? [] }))
      setDrafts(await listResumeDrafts(apiBase))
      announce(`Moved “${section.heading}” ${direction}.`)
    } catch (reason) {
      fail(reason)
    } finally {
      setMovingSection('')
    }
  }

  const sourceOptions = useMemo(() => resumes.filter((item) => item.is_enabled || item.is_current), [resumes])

  return (
    <section className="resumeEditorPanel">
      <header className="resumeLibraryIntro">
        <div>
          <h2>Resume editor</h2>
          <p className="subtle">
            Write a tailored resume and download it as Word, PDF or markdown, in the layout an employer asked for.
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

      {/* The guarantee stays on screen as its own sentence; the mechanism behind
          it folds away. It was five lines of prose above the tool, permanently,
          which is where a paragraph stops being read at all. */}
      <details className="resumeEditorRule">
        <summary>Editing here never changes a stored variant.</summary>
        <p>
          A variant's text is what the matcher and the chatbot read, and its file is what the recruiter
          receives — so a draft becomes a resume only when you <strong>save it as a new variant</strong>,
          which renders the file and reads its text back out of that same file. Downloading it and
          uploading it in <strong>Manage</strong> does the same thing by hand.
        </p>
      </details>

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
        <div className={`resumeEditorBody${previewWide ? ' previewWide' : ''}`}>
          <div className="resumeBuilderSidebar">
            <DraftList drafts={drafts} sourceOptions={sourceOptions} sourceId={sourceId} setSourceId={setSourceId} creating={creating} start={start} openId={openId} open={open} texts={texts} saved={saved} />
            {openId != null ? <SectionOutline sections={sections[openId] ?? []} stale={text !== saved[openId]} onSelect={(section) => {
              const textarea = document.querySelector<HTMLTextAreaElement>('.resumeEditorTextarea')
              if (!textarea) return
              const offset = text.split('\n').slice(0, section.start).reduce((sum, line) => sum + line.length + 1, 0)
              textarea.focus()
              textarea.setSelectionRange(offset, offset + section.heading.length + section.level + 1)
              textarea.scrollTop = section.start * (parseFloat(getComputedStyle(textarea).lineHeight) || 20)
            }} moving={movingSection} onMove={(section, direction) => moveSection(section, direction)} /> : null}
          </div>

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
<MarkdownPane text={text} onChange={(value) => setTexts((current) => ({ ...current, [selected.id]: value }))} />

                {publishOpen ? (
<PublishPanel key={selected.id} selected={selected} format={format} activeProfile={activeProfile} dirty={dirty || layoutDirty}
                    publishing={publishing} publish={(fields) => publish(selected, fields)} />
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
                        onChange={(event) => selectProfile(event.target.value ? Number(event.target.value) : null)}
                      >
                        <option value="">Built-in layout</option>
                        {profiles.map((profile) => (
                          <option key={profile.id} value={profile.id}>
                            {profile.name}{profile.is_default ? ' (default)' : ''}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button type="button" onClick={() => download(selected)} disabled={exporting || dirty || layoutDirty}>
                      {exporting ? 'Building...' : 'Download'}
                    </button>
                    <button
                      type="button"
                      onClick={() => (publishOpen ? closePublish() : setPublishOpen(true))}
                      aria-expanded={publishOpen}
                      disabled={dirty || layoutDirty}
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
          {selected && !loadingDraft ? <aside className="resumeBuilderPreview">
            <TemplateGallery apiBase={apiBase} profiles={profiles} profileId={profileId} onProfile={selectProfile}
              onTemplate={(spec, label) => { editSpec(spec); setLayoutName(label) }} />
            <FormattingPanel spec={activeSpec} onChange={editSpec} />
            {layoutDirty ? <div className="resumeLayoutSave">
              <p className="subtle">Save these formatting changes as a profile, then save the draft before exporting.</p>
              <label>Layout name<input value={layoutName} onChange={(event) => setLayoutName(event.target.value)} /></label>
              <button type="button" disabled={savingLayout || !layoutName.trim()} onClick={saveLayout}>{savingLayout ? 'Saving layout...' : 'Save layout as profile'}</button>
              <button type="button" onClick={() => selectProfile(profileId)}>Discard formatting changes</button>
            </div> : null}
            {!dirty && !layoutDirty ? <ExactPreview key={`${selected.id}:${saved[selected.id]}:${profileId}:${JSON.stringify(activeSpec)}`}
              apiBase={apiBase} draftId={selected.id} profileId={profileId} /> : <p className="subtle">Save text and layout before exact preview.</p>}
            <ResumePreview markdown={text} spec={activeSpec} wide={previewWide} onToggleWide={() => setPreviewWide((value) => !value)} />
          </aside> : null}
        </div>
      ) : null}
    </section>
  )
}
