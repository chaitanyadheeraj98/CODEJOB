import { filterSortRegistry, resolveRegistryEntry } from '../filterSortRegistry'
import { LOCKED_FIELD_KEYS, visibleFieldsFor } from '../filterVisibility'
import type { ResumeAssetOption } from '../features/premium_numbers/types'

type Props = {
  visibleFilters: Record<string, string[]>
  onChange: (next: Record<string, string[]>) => void
  resumeAssets: ResumeAssetOption[]
  savingLabel: string
}

// Labels carry their own hierarchy: "Premium Contacts · Opportunities" is a family
// plus a leaf. Splitting them lets the leaf lead the scan, so eleven rows read as
// six dashboard families instead of eleven similar strings.
const SCOPE_SEPARATOR = ' · '

export const REGISTRY_SECTIONS = [
  { key: 'needs_review', label: 'Needs Review' },
  { key: 'failed_mapping', label: 'Failed Mapping' },
  { key: 'sent_items', label: 'Sent Items' },
  { key: 'inbox', label: 'Inbox' },
  { key: 'premium_numbers:opportunities', label: 'Premium Contacts · Opportunities' },
  { key: 'premium_numbers:inventory', label: 'Premium Contacts · Number Inventory' },
  { key: 'premium_numbers:recycle_bin', label: 'Premium Contacts · Recycle Bin' },
  { key: 'resume_tracking:resumes', label: 'Resume Tracking · Resumes' },
  { key: 'resume_tracking:submissions', label: 'Resume Tracking · Submissions' },
  { key: 'application_tracking:bookmarked', label: 'Application Tracking · Bookmarked' },
  { key: 'application_tracking:tracked', label: 'Application Tracking · Tracked' },
] as const

export default function FilterVisibilitySettings({ visibleFilters, onChange, resumeAssets, savingLabel }: Props) {
  return <section className="card filterVisibilityCard">
    <h2>Filter visibility</h2>
    {/* Single scroll child by contract: `.runQueueGrid > .card > :not(h2)` turns every
        direct child into its own flex/scroll region, so the groups must be wrapped. */}
    <div className="filterVisibilityBody">
      <p className="subtle filterVisibilityIntro">Choose which filters appear on each dashboard. Hiding one only removes it from that filter bar {'—'} you can bring it back any time.</p>
      <div className="filterVisibilityList">
        {REGISTRY_SECTIONS.map(({ key, label }) => {
          const fields = resolveRegistryEntry(filterSortRegistry[key], { resumeAssets })?.fields ?? []
          if (!fields.length) return null
          const preference = visibleFilters[key]
          const shown = visibleFieldsFor(fields, preference).length
          const reduced = shown < fields.length
          const [scope, leaf] = label.includes(SCOPE_SEPARATOR)
            ? label.split(SCOPE_SEPARATOR)
            : [null, label]
          const setPreference = (next: string[]) => onChange({ ...visibleFilters, [key]: next })
          const toggle = (fieldKey: string, checked: boolean) => {
            const selected = new Set(preference ?? fields.map((field) => field.key))
            if (checked) selected.add(fieldKey)
            else selected.delete(fieldKey)
            setPreference(fields.map((field) => field.key).filter((candidate) => selected.has(candidate)))
          }
          return <details key={key} className="filterVisibilityGroup">
            <summary>
              <span className="filterVisibilityName">
                {scope ? <span className="filterVisibilityScope">{scope}</span> : null}
                <span className="filterVisibilityLeaf">{leaf}</span>
              </span>
              <span
                className={`filterVisibilityCount${reduced ? ' is-reduced' : ''}`}
                aria-label={`${shown} of ${fields.length} filters shown`}
              >{shown}/{fields.length}</span>
            </summary>
            <div className="filterVisibilityPanel">
              <div className="filterVisibilityActions">
                <button type="button" onClick={() => setPreference(fields.map((field) => field.key))}>Show all</button>
                <span className="filterVisibilityActionsDivider" aria-hidden="true" />
                <button type="button" onClick={() => setPreference([])}>Hide all</button>
              </div>
              <div className="filterVisibilityFields">
                {fields.map((field) => {
                  const locked = LOCKED_FIELD_KEYS.has(field.key)
                  return <label key={field.key} className={`filterVisibilityField${locked ? ' is-locked' : ''}`}>
                    <input
                      type="checkbox"
                      checked={locked || preference === undefined || preference.includes(field.key)}
                      disabled={locked}
                      onChange={(event) => toggle(field.key, event.target.checked)}
                    />
                    <span className="filterVisibilityFieldName">{field.label}</span>
                    {locked ? <span className="filterVisibilityLock">Always shown</span> : null}
                  </label>
                })}
              </div>
            </div>
          </details>
        })}
      </div>
      <p className={`filterVisibilityStatus${savingLabel ? ' is-visible' : ''}`} role="status">{savingLabel}</p>
    </div>
  </section>
}
