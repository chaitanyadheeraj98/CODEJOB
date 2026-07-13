import { useEffect, useState } from 'react'

export type TrustedGmailGroup = {
  id: number
  display_name: string
  group_email: string
  normalized_group_email: string
  group_slug?: string | null
  enabled: boolean
}

type TrustedGmailGroupsPanelProps = {
  featureEnabled: boolean
  groups: TrustedGmailGroup[]
  busy: boolean
  onFeatureToggle: (enabled: boolean) => void
  onAddGroup: (value: string, displayName: string) => Promise<void>
  onBulkAdd: (value: string) => Promise<void>
  onUpdateGroup: (groupId: number, patch: { display_name?: string; enabled?: boolean }) => Promise<void>
  onDeleteGroup: (groupId: number) => Promise<void>
}

export default function TrustedGmailGroupsPanel({
  featureEnabled,
  groups,
  busy,
  onFeatureToggle,
  onAddGroup,
  onBulkAdd,
  onUpdateGroup,
  onDeleteGroup,
}: TrustedGmailGroupsPanelProps) {
  const [singleValue, setSingleValue] = useState('')
  const [singleDisplayName, setSingleDisplayName] = useState('')
  const [bulkValue, setBulkValue] = useState('')
  const [draftNames, setDraftNames] = useState<Record<number, string>>({})

  useEffect(() => {
    setDraftNames(Object.fromEntries(groups.map((group) => [group.id, group.display_name || ''])))
  }, [groups])

  return (
    <section className="card">
      <h2>Trusted Gmail Requirement Groups</h2>
      <div className="stack">
        <label className="toggleRow pillRow">
          <span>Enable trusted group recognition</span>
          <span className="toggleSwitch">
            <input
              type="checkbox"
              checked={featureEnabled}
              onChange={(e) => onFeatureToggle(e.target.checked)}
            />
            <span className="toggleTrack" />
          </span>
        </label>
        <p className="subtle">
          Messages from these groups get trusted source context, but CodeJob still checks each email for hotlists,
          candidate marketing, location fit, skills, routing, and score thresholds.
        </p>
        <label>
          Group email, Google Groups URL, or slug
          <input
            value={singleValue}
            onChange={(e) => setSingleValue(e.target.value)}
            placeholder="groups.google.com/g/C2C-Corp2Corp-Jobs"
          />
        </label>
        <label>
          Display name
          <input
            value={singleDisplayName}
            onChange={(e) => setSingleDisplayName(e.target.value)}
            placeholder="C2C Corp2Corp Jobs"
          />
        </label>
        <button
          type="button"
          onClick={() => {
            void onAddGroup(singleValue, singleDisplayName).then(() => {
              setSingleValue('')
              setSingleDisplayName('')
            })
          }}
          disabled={busy || !singleValue.trim()}
        >
          Add Group
        </button>
        <label>
          Bulk add groups
          <textarea
            rows={6}
            value={bulkValue}
            onChange={(e) => setBulkValue(e.target.value)}
            placeholder={'One per line.\nC2C-Corp2Corp-Jobs\nc2c-corp2corp-jobs@googlegroups.com\nC2C Corp2Corp Jobs | groups.google.com/g/C2C-Corp2Corp-Jobs'}
          />
        </label>
        <button
          type="button"
          onClick={() => {
            void onBulkAdd(bulkValue).then(() => setBulkValue(''))
          }}
          disabled={busy || !bulkValue.trim()}
        >
          Add All
        </button>
        {groups.length === 0 ? (
          <p className="subtle">No trusted Gmail requirement groups saved yet.</p>
        ) : (
          groups.map((group) => (
            <article key={group.id} className="emailItem">
              <p><strong>Email:</strong> {group.group_email}</p>
              {group.group_slug ? <p><strong>Slug:</strong> {group.group_slug}</p> : null}
              <label>
                Display name
                <input
                  value={draftNames[group.id] ?? group.display_name ?? ''}
                  onChange={(e) => setDraftNames((prev) => ({ ...prev, [group.id]: e.target.value }))}
                />
              </label>
              <label className="toggleRow pillRow">
                <span>Enabled</span>
                <span className="toggleSwitch">
                  <input
                    type="checkbox"
                    checked={group.enabled}
                    onChange={(e) => { void onUpdateGroup(group.id, { enabled: e.target.checked }) }}
                  />
                  <span className="toggleTrack" />
                </span>
              </label>
              <div className="rowBtns">
                <button
                  type="button"
                  onClick={() => { void onUpdateGroup(group.id, { display_name: draftNames[group.id] ?? group.display_name }) }}
                  disabled={busy}
                >
                  Save
                </button>
                <button
                  type="button"
                  onClick={() => { void onDeleteGroup(group.id) }}
                  disabled={busy}
                >
                  Delete
                </button>
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  )
}
