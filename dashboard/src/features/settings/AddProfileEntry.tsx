import { useState } from 'react'

import { PROFILE_FIELD_LABELS, composeEntry, planAppend, sha256Hex } from './profileEntry'

const VERBATIM_OPTION = '__verbatim__'

type AddProfileEntryProps = {
  apiBase: string
  profile: string
  onSaved: () => void
}

/**
 * Add one line to the Candidate Profile from Settings - path A3.
 *
 * The only way into the profile with no model in it at all. If the assistant
 * never offers, or offers and never calls the tool, this still records the fact
 * and the loop the feature exists to close still closes. That is its whole job,
 * which is why it previews rather than saving on click: every write shows the
 * complete document it will produce first, and this path has no proposal card
 * to do that for it.
 */
export default function AddProfileEntry({ apiBase, profile, onSaved }: AddProfileEntryProps) {
  const [field, setField] = useState<string>(PROFILE_FIELD_LABELS[0])
  const [label, setLabel] = useState<string>(PROFILE_FIELD_LABELS[0])
  const [value, setValue] = useState('')
  const [preview, setPreview] = useState<{ entry: string; resulting: string; replaces: string[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Nothing to append to: appending to an empty profile is creating one, and
  // creating one is an upload. The panel already prompts for that.
  if (!profile.trim()) return null

  const verbatim = field === VERBATIM_OPTION

  const showPreview = () => {
    setError('')
    const entry = composeEntry(verbatim ? label : field, value, verbatim)
    const { combined, replaces } = planAppend(profile, entry)
    setPreview({ entry, resulting: combined, replaces })
  }

  const save = async () => {
    if (!preview) return
    setBusy(true)
    setError('')
    try {
      const response = await fetch(`${apiBase}/settings/candidate-profile/entries`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry: preview.entry, base_sha256: await sha256Hex(profile) }),
      })
      if (!response.ok) {
        const detail = await response.json().then((body) => body?.detail).catch(() => null)
        throw new Error(detail || 'Failed to add the entry')
      }
      setPreview(null)
      setValue('')
      onSaved()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Failed to add the entry')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="addProfileEntry">
      <div className="addProfileEntryRow">
        <label>
          Field
          <select
            aria-label="Profile field"
            value={field}
            onChange={(event) => {
              setField(event.target.value)
              setPreview(null)
            }}
          >
            {PROFILE_FIELD_LABELS.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
            <option value={VERBATIM_OPTION}>In my own words</option>
          </select>
        </label>
        {verbatim ? (
          <label>
            Label
            <select
              aria-label="Verbatim field label"
              value={label}
              onChange={(event) => {
                setLabel(event.target.value)
                setPreview(null)
              }}
            >
              {PROFILE_FIELD_LABELS.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </label>
        ) : null}
        <label>
          {verbatim ? 'Your wording' : 'Value'}
          <input
            aria-label="Profile entry value"
            value={value}
            onChange={(event) => {
              setValue(event.target.value)
              setPreview(null)
            }}
            placeholder={verbatim ? 'I can join after two weeks' : '2 weeks'}
          />
        </label>
        <button type="button" onClick={showPreview} disabled={!value.trim() || busy}>
          Preview
        </button>
      </div>
      {preview ? (
        <div className="addProfileEntryPreview">
          <dl>
            {/* The old value first: a correction that showed only what it was
                adding would hide the half most worth checking. */}
            {preview.replaces.length ? (
              <div>
                <dt>Replacing</dt>
                <dd><pre className="chatProposalDocument">{preview.replaces.join('\n')}</pre></dd>
              </div>
            ) : null}
            <div>
              <dt>{preview.replaces.length ? 'Entry after this change' : 'Entry added'}</dt>
              <dd>{preview.entry}</dd>
            </div>
            <div>
              <dt>Complete profile after saving</dt>
              <dd><pre className="chatProposalDocument">{preview.resulting}</pre></dd>
            </div>
          </dl>
          <div className="chatProposalActions">
            <button type="button" onClick={() => void save()} disabled={busy}>
              {busy ? 'Working...' : preview.replaces.length ? 'Update Profile' : 'Save to Profile'}
            </button>
            <button type="button" onClick={() => setPreview(null)} disabled={busy}>Cancel</button>
          </div>
        </div>
      ) : null}
      {error ? <p className="chatError" role="alert">{error}</p> : null}
    </div>
  )
}
