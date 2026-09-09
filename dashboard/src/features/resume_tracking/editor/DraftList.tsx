import { formatVariantLabel } from '../resumeDisplay'
import type { ResumeDraftSummary, ResumeLibraryItem } from '../types'
const code = (resume: ResumeLibraryItem) => resume.variant_code || `R${String(resume.id).padStart(2, '0')}`

const editedOn = (iso: string) => {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}


type Props = {
drafts: ResumeDraftSummary[]
sourceOptions: ResumeLibraryItem[]
sourceId: string
setSourceId: (value: string) => void
creating: boolean
start: (source: ResumeLibraryItem | null) => void
openId: number | null
open: (draft: ResumeDraftSummary) => void
texts: Record<number, string>
saved: Record<number, string>
}

export default function DraftList({ drafts, sourceOptions, sourceId, setSourceId, creating, start, openId, open, texts, saved }: Props) {
  return (<>
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
  </>)
}
