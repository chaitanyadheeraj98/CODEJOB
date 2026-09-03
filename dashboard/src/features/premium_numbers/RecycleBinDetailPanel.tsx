import { CategoryChip } from './StatusBadge'
import type { InventoryRow } from './types'

const NO_NUMBER_PLACEHOLDER = '(XXX) XXX-XXXX'

type RecycleBinDetailPanelProps = {
  row: InventoryRow
  busy: boolean
  onClose: () => void
  onRestore: () => void
  onDelete: () => void
}

export default function RecycleBinDetailPanel({ row, busy, onClose, onRestore, onDelete }: RecycleBinDetailPanelProps) {
  const recruiter = row.recruiter
  const employer = row.employer
  const sourceLinkUrl = recruiter?.source_link_url || employer?.source_link_url
  const sourceType = recruiter?.source_type || employer?.source_type

  return (
    <div className="detailPanelOverlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="detailPanel" role="dialog" aria-modal="true" aria-label={`Deleted contact details for ${row.number || NO_NUMBER_PLACEHOLDER}`}>
        <header className="detailPanelHeader">
          <div>
            <p className="detailPanelEyebrow">Deleted contact</p>
            <h3 className={row.number ? '' : 'detailPanelNumberEmpty'}>{row.number || NO_NUMBER_PLACEHOLDER}</h3>
            <div className="categoryChips">
              {row.categories.map((category) => <CategoryChip key={category} category={category} />)}
            </div>
          </div>
          <button type="button" className="iconBtn" aria-label="Close details" onClick={onClose}>×</button>
        </header>

        <div className="detailPanelBody">
          {recruiter ? (
            <section className="detailSection">
              <h4>Recruiter profile</h4>
              <dl className="detailList">
                <div><dt>Name</dt><dd>{recruiter.recruiter_name}</dd></div>
                <div><dt>Company</dt><dd>{recruiter.company}</dd></div>
                <div><dt>Designation</dt><dd>{recruiter.designation}</dd></div>
                <div><dt>Email</dt><dd>{recruiter.recruiter_email || '--'}</dd></div>
                <div><dt>LinkedIn</dt><dd>{recruiter.linkedin_url || '--'}</dd></div>
              </dl>
            </section>
          ) : null}

          {employer ? (
            <section className="detailSection">
              <h4>Employer profile</h4>
              <dl className="detailList">
                <div><dt>Owner</dt><dd>{employer.owner_name}</dd></div>
                <div><dt>Company</dt><dd>{employer.company}</dd></div>
                <div><dt>Email</dt><dd>{employer.employer_email || '--'}</dd></div>
              </dl>
            </section>
          ) : null}

          <section className="detailSection">
            <h4>Deletion</h4>
            <dl className="detailList">
              <div><dt>Deleted</dt><dd>{Number.isFinite(new Date(row.lastCheckedAt).getTime()) ? new Date(row.lastCheckedAt).toLocaleString() : '--'}</dd></div>
            </dl>
            {sourceLinkUrl ? <a href={sourceLinkUrl} target="_blank" rel="noreferrer">{sourceType === 'nvoids' ? 'Open original post' : 'Open exact email in Gmail'}</a> : null}
          </section>
        </div>

        <footer className="detailPanelActions">
          <button type="button" onClick={onRestore} disabled={busy}>Restore</button>
          <button type="button" className="dangerButton" onClick={onDelete} disabled={busy}>Delete Forever</button>
        </footer>
      </div>
    </div>
  )
}
