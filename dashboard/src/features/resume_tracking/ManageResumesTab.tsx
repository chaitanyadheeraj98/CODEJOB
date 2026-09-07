import { useCallback, useEffect, useMemo, useState } from 'react'

import { deleteResumeAsset, listResumeLibrary, updateResumeAsset, uploadResumeAsset } from './api'
import { displaySkills, formatVariantLabel } from './resumeDisplay'
import type { ResumeLibraryItem } from './types'

type Props = {
  apiBase: string
  /** Open this resume's editor on arrival - used when another tab hands one over. */
  focusResumeId?: number | null
  /** Let the host refresh its own copy of the library (Settings holds one too). */
  onLibraryChanged?: () => void
}

type Draft = {
  primary_role: string
  variant_label: string
  structured_skills: string
  skills_text: string
}

const EMPTY_DRAFT: Draft = { primary_role: '', variant_label: '', structured_skills: '', skills_text: '' }

const code = (resume: ResumeLibraryItem) => resume.variant_code || `R${String(resume.id).padStart(2, '0')}`

const draftFrom = (resume: ResumeLibraryItem): Draft => ({
  primary_role: resume.primary_role ?? '',
  variant_label: resume.variant_label ?? '',
  structured_skills: (resume.structured_skills ?? []).join(', '),
  skills_text: resume.skills_text ?? '',
})

const splitSkills = (value: string) => value.split(',').map((item) => item.trim()).filter(Boolean)

const addedOn = (iso: string) => {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

const matches = (resume: ResumeLibraryItem, query: string) => {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return [code(resume), resume.file_name, resume.variant_label, resume.primary_role, resume.skills_text, ...(resume.structured_skills ?? [])]
    .join(' ')
    .toLowerCase()
    .includes(needle)
}

/**
 * The resume library, managed from inside Resume Tracking.
 *
 * Settings keeps its own copy of this panel - deliberately, since resumes are
 * also a settings concern. Both call the same four endpoints, so neither view can
 * drift from the other; only the layout differs. This one is built for the case
 * Settings handles badly: eighteen variants that all need reading before you can
 * tell them apart.
 */
export default function ManageResumesTab({ apiBase, focusResumeId, onLibraryChanged }: Props) {
  const [items, setItems] = useState<ResumeLibraryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [query, setQuery] = useState('')

  const [uploadOpen, setUploadOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [uploadDraft, setUploadDraft] = useState<Draft>(EMPTY_DRAFT)
  const [uploading, setUploading] = useState(false)

  const [openId, setOpenId] = useState<number | null>(null)
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [savingId, setSavingId] = useState<number | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  const refresh = useCallback(async () => {
    const list = await listResumeLibrary(apiBase)
    setItems(list)
    return list
  }, [apiBase])

  useEffect(() => {
    setLoading(true)
    refresh()
      .catch((reason) => setError((reason as Error).message))
      .finally(() => setLoading(false))
  }, [refresh])

  // A handoff from another tab ("Add role & label") should land on the open editor,
  // not on a list the user then has to search through again.
  useEffect(() => {
    if (focusResumeId == null) return
    const target = items.find((item) => item.id === focusResumeId)
    if (!target) return
    setOpenId(focusResumeId)
    setDrafts((current) => (current[focusResumeId] ? current : { ...current, [focusResumeId]: draftFrom(target) }))
    // Optional call: jsdom, and any non-browser host, has no scrollIntoView.
    document.getElementById(`resume-library-${focusResumeId}`)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  }, [focusResumeId, items])

  const visible = useMemo(() => items.filter((item) => matches(item, query)), [items, query])

  const announce = (message: string) => { setError(''); setNotice(message) }
  const fail = (reason: unknown) => { setNotice(''); setError((reason as Error).message) }

  const toggleEditor = (resume: ResumeLibraryItem) => {
    setConfirmDeleteId(null)
    if (openId === resume.id) {
      setOpenId(null)
      return
    }
    setOpenId(resume.id)
    setDrafts((current) => (current[resume.id] ? current : { ...current, [resume.id]: draftFrom(resume) }))
  }

  const editDraft = (resumeId: number, patch: Partial<Draft>) => {
    setDrafts((current) => ({ ...current, [resumeId]: { ...(current[resumeId] ?? EMPTY_DRAFT), ...patch } }))
  }

  const save = async (resume: ResumeLibraryItem) => {
    const draft = drafts[resume.id] ?? draftFrom(resume)
    setSavingId(resume.id)
    try {
      await updateResumeAsset(apiBase, resume.id, {
        primary_role: draft.primary_role,
        variant_label: draft.variant_label,
        structured_skills: splitSkills(draft.structured_skills),
        skills_text: draft.skills_text,
      })
      // Re-seed from the server: it normalises the skills text, so the field must
      // show what was actually stored rather than what was typed.
      const list = await refresh()
      const stored = list.find((item) => item.id === resume.id)
      if (stored) setDrafts((current) => ({ ...current, [resume.id]: draftFrom(stored) }))
      announce(`${code(resume)} saved.`)
      onLibraryChanged?.()
    } catch (reason) {
      fail(reason)
    } finally {
      setSavingId(null)
    }
  }

  const setEnabled = async (resume: ResumeLibraryItem, isEnabled: boolean) => {
    setBusyId(resume.id)
    try {
      await updateResumeAsset(apiBase, resume.id, { is_enabled: isEnabled })
      // Enabling also moves the legacy fallback, which changes another row - so the
      // whole list is reloaded rather than patched in place.
      await refresh()
      announce(isEnabled ? `${code(resume)} is enabled and can be sent.` : `${code(resume)} is disabled and will not be sent.`)
      onLibraryChanged?.()
    } catch (reason) {
      fail(reason)
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (resume: ResumeLibraryItem) => {
    setBusyId(resume.id)
    try {
      await deleteResumeAsset(apiBase, resume.id)
      await refresh()
      setConfirmDeleteId(null)
      setOpenId((current) => (current === resume.id ? null : current))
      announce(`${code(resume)} deleted.`)
      onLibraryChanged?.()
    } catch (reason) {
      // The server refuses while an application still points at the resume, and
      // says which - that message is more useful than anything invented here.
      fail(reason)
    } finally {
      setBusyId(null)
    }
  }

  const upload = async () => {
    if (!file) return
    setUploading(true)
    try {
      const created = await uploadResumeAsset(apiBase, {
        file,
        skills_text: uploadDraft.skills_text,
        primary_role: uploadDraft.primary_role,
        structured_skills_text: uploadDraft.structured_skills,
        variant_label: uploadDraft.variant_label,
      })
      await refresh()
      setFile(null)
      setUploadDraft(EMPTY_DRAFT)
      setUploadOpen(false)
      setOpenId(created.id)
      setDrafts((current) => ({ ...current, [created.id]: draftFrom(created) }))
      announce(`${code(created)} added to the library.`)
      onLibraryChanged?.()
    } catch (reason) {
      fail(reason)
    } finally {
      setUploading(false)
    }
  }

  return (
    <section className="resumeLibraryPanel">
      <header className="resumeLibraryIntro">
        <div>
          <h2>Resume library</h2>
          <p className="subtle">
            Add a variant, keep its role and skills accurate, retire the ones you no longer send.
            Settings shows the same library — a change here shows there.
          </p>
        </div>
        <button
          type="button"
          className="primaryButton"
          onClick={() => setUploadOpen((open) => !open)}
          aria-expanded={uploadOpen}
        >
          {uploadOpen ? 'Cancel upload' : 'Upload resume'}
        </button>
      </header>

      {error ? <p className="errorText" role="alert">{error}</p> : null}
      {notice ? <p className="resumeLibraryNotice" aria-live="polite">{notice}</p> : null}

      {uploadOpen ? (
        <section className="resumeLibraryUpload" aria-label="Upload a resume">
          <h3>New resume variant</h3>
          <p className="subtle">
            The file is read on upload to build its ATS profile. Everything below is optional now and
            editable later, but a role and a label are what make the variant recognisable.
          </p>
          <label className="resumeLibraryFile">
            <span>Resume file</span>
            <input
              type="file"
              accept=".pdf,.doc,.docx"
              disabled={uploading}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <div className="resumeFieldGrid">
            <label className="resumeField">
              <span>Primary role</span>
              <input
                value={uploadDraft.primary_role}
                placeholder="Senior Java Developer"
                onChange={(event) => setUploadDraft((current) => ({ ...current, primary_role: event.target.value }))}
              />
            </label>
            <label className="resumeField">
              <span>Variant label</span>
              <input
                value={uploadDraft.variant_label}
                placeholder="Java / Banking"
                onChange={(event) => setUploadDraft((current) => ({ ...current, variant_label: event.target.value }))}
              />
            </label>
          </div>
          <label className="resumeField">
            <span>Structured skills</span>
            <input
              value={uploadDraft.structured_skills}
              placeholder="Java, Spring Boot, AWS"
              onChange={(event) => setUploadDraft((current) => ({ ...current, structured_skills: event.target.value }))}
            />
            <small className="subtle">Comma-separated. These are what the resume picker matches against.</small>
          </label>
          <label className="resumeField">
            <span>Matching skills</span>
            <textarea
              rows={3}
              value={uploadDraft.skills_text}
              placeholder="java, spring boot, microservices, aws"
              onChange={(event) => setUploadDraft((current) => ({ ...current, skills_text: event.target.value }))}
            />
            <small className="subtle">Leave empty to use the skills extracted from the file.</small>
          </label>
          <div className="resumeLibraryUploadFoot">
            <button type="button" className="primaryButton" onClick={upload} disabled={!file || uploading}>
              {uploading ? 'Reading resume...' : 'Add to library'}
            </button>
            {uploading ? <span className="subtle" aria-live="polite">Extracting content and preparing the ATS profile...</span> : null}
          </div>
        </section>
      ) : null}

      {loading ? <p className="subtle">Loading the resume library...</p> : null}

      {!loading && !items.length ? (
        <p className="subtle">No resumes stored yet. Upload one to start matching it against incoming roles.</p>
      ) : null}

      {items.length ? (
        <div className="resumeLibraryToolbar">
          <label className="resumeLibrarySearch">
            <span className="visuallyHidden">Search resumes</span>
            <input
              type="search"
              value={query}
              placeholder="Search by code, role, label or skill"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <span className="subtle">
            {query ? `${visible.length} of ${items.length}` : `${items.length} resume${items.length === 1 ? '' : 's'}`}
          </span>
        </div>
      ) : null}

      {items.length && !visible.length ? <p className="subtle">Nothing matches “{query}”.</p> : null}

      <div className="resumeLibraryList">
        {visible.map((resume) => {
          const isOpen = openId === resume.id
          const draft = drafts[resume.id] ?? draftFrom(resume)
          const skills = displaySkills(resume)
          return (
            <article
              key={resume.id}
              id={`resume-library-${resume.id}`}
              className={`resumeLibraryCard${resume.is_enabled ? '' : ' disabled'}${isOpen ? ' open' : ''}`}
            >
              <div className="resumeLibraryRow">
                <span className="resumeLibraryCode">{code(resume)}</span>
                <div className="resumeLibraryIdentity">
                  <strong>{formatVariantLabel(resume.variant_label) || resume.file_name}</strong>
                  <span className="subtle resumeLibraryMeta">
                    {resume.file_name} · v{resume.version}
                    {resume.created_at ? ` · added ${addedOn(resume.created_at)}` : ''}
                  </span>
                </div>
                <div className="resumeLibraryRole">
                  {resume.primary_role ? <span>{resume.primary_role}</span> : <span className="subtle">Role not set</span>}
                  {skills.length ? (
                    <span className="skillChips">
                      {skills.slice(0, 3).map((skill) => <span className="trackingChip" key={skill}>{skill}</span>)}
                      {skills.length > 3 ? <span className="subtle">+{skills.length - 3}</span> : null}
                    </span>
                  ) : null}
                </div>
                <div className="resumeLibraryState">
                  {resume.is_current ? <span className="resumeLibraryBadge current">Fallback</span> : null}
                  <span className={`resumeLibraryBadge ${resume.is_enabled ? 'on' : 'off'}`}>
                    {resume.is_enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>
                <button type="button" className="resumeLibraryEdit" onClick={() => toggleEditor(resume)} aria-expanded={isOpen}>
                  {isOpen ? 'Close' : 'Edit'}
                </button>
              </div>

              {isOpen ? (
                <div className="resumeLibraryEditor">
                  <div className="resumeFieldGrid">
                    <label className="resumeField">
                      <span>Primary role</span>
                      <input
                        value={draft.primary_role}
                        placeholder="Senior Java Developer"
                        onChange={(event) => editDraft(resume.id, { primary_role: event.target.value })}
                      />
                    </label>
                    <label className="resumeField">
                      <span>Variant label</span>
                      <input
                        value={draft.variant_label}
                        placeholder="Java / Banking"
                        onChange={(event) => editDraft(resume.id, { variant_label: event.target.value })}
                      />
                    </label>
                  </div>
                  <label className="resumeField">
                    <span>Structured skills</span>
                    <input
                      value={draft.structured_skills}
                      placeholder="Java, Spring Boot, AWS"
                      onChange={(event) => editDraft(resume.id, { structured_skills: event.target.value })}
                    />
                    <small className="subtle">Comma-separated. These are what the resume picker matches against.</small>
                  </label>
                  <label className="resumeField">
                    <span>Matching skills</span>
                    <textarea
                      rows={4}
                      value={draft.skills_text}
                      placeholder="java, spring boot, microservices, aws"
                      onChange={(event) => editDraft(resume.id, { skills_text: event.target.value })}
                    />
                    <small className="subtle">
                      {resume.skills_text ? 'Stored on the resume. Clear it to fall back to the skills extracted from the file.' : 'Empty — the skills extracted from the file are used instead.'}
                    </small>
                  </label>

                  <div className="resumeLibraryEditorFoot">
                    <label className="resumeLibraryToggle">
                      <span className="toggleSwitch">
                        <input
                          type="checkbox"
                          checked={resume.is_enabled}
                          disabled={busyId === resume.id}
                          onChange={(event) => setEnabled(resume, event.target.checked)}
                        />
                        <span className="toggleTrack" />
                      </span>
                      <span>{resume.is_enabled ? 'Enabled — can be sent' : 'Disabled — never sent'}</span>
                    </label>
                    <div className="resumeLibraryEditorButtons">
                      {confirmDeleteId === resume.id ? (
                        <>
                          <span className="subtle">Delete {code(resume)} permanently?</span>
                          <button type="button" onClick={() => setConfirmDeleteId(null)}>Keep</button>
                          <button type="button" className="dangerButton" onClick={() => remove(resume)} disabled={busyId === resume.id}>
                            {busyId === resume.id ? 'Deleting...' : 'Delete'}
                          </button>
                        </>
                      ) : (
                        <button type="button" className="dangerButton" onClick={() => setConfirmDeleteId(resume.id)}>Delete</button>
                      )}
                      <button type="button" className="primaryButton" onClick={() => save(resume)} disabled={savingId === resume.id}>
                        {savingId === resume.id ? 'Saving...' : 'Save changes'}
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}
            </article>
          )
        })}
      </div>
    </section>
  )
}
