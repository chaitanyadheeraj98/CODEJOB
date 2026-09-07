import { useEffect, useRef, type KeyboardEvent, type RefObject } from 'react'

import { CategoryChip, StatusBadge } from './StatusBadge'
import type { CompanyCard, CompanyDetail, InventoryRow, TrackedCount } from './types'

const NO_NUMBER_PLACEHOLDER = '(XXX) XXX-XXXX'

type CompanyDetailPanelProps = {
  company: CompanyCard
  detail: CompanyDetail | null
  loading: boolean
  returnFocusRef: RefObject<HTMLElement | null>
  onClose: () => void
  onOpenContact: (company: CompanyCard, contact: InventoryRow) => void
  onViewInInventory: (company: CompanyCard) => void
}

function rowEmail(row: InventoryRow): string {
  return row.recruiter?.recruiter_email || row.employer?.employer_email || ''
}

function formatDate(value: string | null): string {
  if (!value) return '--'
  const timestamp = new Date(value).getTime()
  return Number.isFinite(timestamp) ? new Date(value).toLocaleDateString() : '--'
}

// A count nobody has ever recorded reads as a dash, not a zero: "0 RTRs" sounds
// like a fact about the company when it is a fact about the pipeline.
function Tile({ label, count }: { label: string; count: TrackedCount }) {
  return (
    <div>
      <strong className={count.tracked ? '' : 'companyTileUntracked'}>{count.tracked ? count.value : '--'}</strong>
      <span>{count.tracked ? label : `${label} · not tracked yet`}</span>
    </div>
  )
}

export default function CompanyDetailPanel({
  company,
  detail,
  loading,
  returnFocusRef,
  onClose,
  onOpenContact,
  onViewInInventory,
}: CompanyDetailPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const panel = panelRef.current
    const returnFocus = returnFocusRef.current
    panel?.focus()
    return () => returnFocus?.focus?.()
  }, [returnFocusRef])

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onClose()
    }
  }

  const responsiveness = detail?.responsiveness
  const replyRate = responsiveness?.reply_rate

  return (
    <div className="detailPanelOverlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div
        ref={panelRef}
        className="detailPanel"
        role="dialog"
        aria-modal="true"
        aria-label={`Company details for ${company.name}`}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <header className="detailPanelHeader">
          <div>
            <p className="detailPanelEyebrow">Company</p>
            <h3>{company.name}</h3>
            <div className="categoryChips">
              <span className="categoryChip">{company.domain || 'No email domain'}</span>
              {company.recruiter_count > 0 ? <CategoryChip category="Recruiter" /> : null}
              {company.employer_count > 0 ? <CategoryChip category="Employer" /> : null}
            </div>
          </div>
          <button type="button" className="iconBtn" aria-label="Close details" onClick={onClose}>×</button>
        </header>

        <div className="detailPanelBody">
          <section className="detailSection">
            <h4>People</h4>
            <div className="applicationSummary">
              <div><strong>{company.contact_count}</strong><span>In this company</span></div>
              <div><strong>{company.active_count}</strong><span>Active</span></div>
              <div><strong>{company.flagged_count}</strong><span>Flagged</span></div>
              <div><strong>{company.unscored_count}</strong><span>Unscored</span></div>
            </div>
          </section>

          <section className="detailSection">
            <h4>Pipeline</h4>
            {detail ? (
              <div className="applicationSummary">
                <div><strong>{company.opportunity_count}</strong><span>Opportunities</span></div>
                <Tile label="Applications" count={detail.pipeline.applications} />
                <Tile label="Submissions" count={detail.pipeline.submissions} />
                <Tile label="Interviews" count={detail.pipeline.interviews} />
                <Tile label="Submitted to client" count={detail.pipeline.submitted_to_client} />
                <Tile label="RTRs" count={detail.pipeline.rtrs} />
              </div>
            ) : <p className="subtle">{loading ? 'Loading pipeline...' : 'No pipeline data.'}</p>}
            {detail && !detail.pipeline.applications.tracked ? (
              <p className="inventoryNote">
                This company has no email domain, so applications and email cannot be attributed to it.
              </p>
            ) : null}
          </section>

          <section className="detailSection">
            <h4>Interaction</h4>
            {responsiveness ? (
              <>
                <div className="applicationSummary">
                  <div><strong>{responsiveness.emails_received}</strong><span>Emails received</span></div>
                  <div><strong>{responsiveness.conversations}</strong><span>Threads you replied to</span></div>
                  <div><strong>{responsiveness.replied}</strong><span>They wrote back</span></div>
                  <div>
                    <strong className={replyRate == null ? 'companyTileUntracked' : ''}>
                      {replyRate == null ? '--' : `${Math.round(replyRate * 100)}%`}
                    </strong>
                    <span>Reply rate</span>
                  </div>
                </div>
                <dl className="detailList">
                  <div><dt>Last email from them</dt><dd>{formatDate(responsiveness.last_inbound_at)}</dd></div>
                  <div><dt>Last thread activity</dt><dd>{formatDate(responsiveness.last_reply_at)}</dd></div>
                </dl>
              </>
            ) : <p className="subtle">{loading ? 'Loading interaction history...' : 'No interaction data.'}</p>}
          </section>

          {detail && detail.opportunities.length > 0 ? (
            <section className="detailSection">
              <h4>Recent opportunities</h4>
              <ul className="companyOpportunityList">
                {detail.opportunities.map((opportunity) => (
                  <li key={opportunity.id}>
                    <span>{opportunity.job_title}</span>
                    <small>{[opportunity.end_client, opportunity.status, formatDate(opportunity.created_at)].filter(Boolean).join(' · ')}</small>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className="detailSection">
            <div className="detailSectionHeader">
              <h4>Contacts</h4>
              <button type="button" onClick={() => onViewInInventory(company)}>View in Number Inventory</button>
            </div>
            {detail ? (
              <div className="inventoryTableScroll">
                <table className="inventoryTable companyContactsTable">
                  <thead>
                    <tr><th>Owner</th><th>Number</th><th>Email</th><th>Category</th><th>Score</th><th>Status</th></tr>
                  </thead>
                  <tbody>
                    {detail.contacts.map((contact) => (
                      <tr key={contact.key} onClick={() => onOpenContact(company, contact)}>
                        <td>
                          <button
                            type="button"
                            className="companyContactLink"
                            onClick={(event) => { event.stopPropagation(); onOpenContact(company, contact) }}
                          >
                            {contact.owner || 'Unassigned'}
                          </button>
                          {contact.company && contact.company !== 'Unknown' && contact.company !== company.name
                            ? <small>{contact.company}</small>
                            : null}
                        </td>
                        <td className={`inventoryNumber ${contact.number ? '' : 'inventoryNumber--empty'}`}>{contact.number || NO_NUMBER_PLACEHOLDER}</td>
                        <td>{rowEmail(contact) || '--'}</td>
                        <td>
                          <div className="categoryChips">
                            {contact.categories.map((category) => <CategoryChip key={category} category={category} />)}
                          </div>
                        </td>
                        <td className="inventoryScore">{contact.score == null ? '--' : `${contact.score}/100`}</td>
                        <td><StatusBadge status={contact.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="subtle">{loading ? 'Loading contacts...' : 'No contacts.'}</p>}
          </section>
        </div>
      </div>
    </div>
  )
}
