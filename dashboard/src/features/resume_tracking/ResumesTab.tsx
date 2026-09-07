import { useCallback, useEffect, useState } from 'react'
import { getResumeFunnel, getResumePerformanceSummary } from './api'
import type { ResumeFunnelMetrics, ResumePerformanceSummaryItem } from './types'
import SubmissionsTab from './SubmissionsTab'
import type { FilterValues } from '../../components/FilterSortBar'

type Props = { apiBase: string; onNavigateToSettings?: (resumeId: number) => void; sortValue?: string; filterValues?: FilterValues }

const code = (item: ResumePerformanceSummaryItem) => item.resume.variant_code || `R${String(item.resume.id).padStart(2, '0')}`

export default function ResumesTab({ apiBase, onNavigateToSettings, sortValue = 'recent', filterValues = {} }: Props) {
  const [items, setItems] = useState<ResumePerformanceSummaryItem[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [funnel, setFunnel] = useState<ResumeFunnelMetrics | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    getResumePerformanceSummary(apiBase)
      .then(setItems)
      .catch((reason) => setError((reason as Error).message))
      .finally(() => setLoading(false))
  }, [apiBase])

  useEffect(() => { load() }, [load])

  const select = (id: number) => {
    if (selectedId === id) {
      setSelectedId(null)
      setFunnel(null)
      return
    }
    setSelectedId(id)
    getResumeFunnel(apiBase, id).then(setFunnel).catch((reason) => setError((reason as Error).message))
  }

  // Outcome rates only move when a submission's status is advanced by hand. Showing
  // a wall of 0% reads as "these resumes fail"; it actually means "nothing logged".
  const anyOutcomes = items.some((item) => item.acceptance_rate > 0)

  return (
    <section className="resumeTrackingPanel">
      {error ? <p className="errorText" role="alert">{error}</p> : null}

      {loading ? <p className="subtle">Loading resumes...</p> : null}
      {!loading && !items.length ? <p className="subtle">Upload a resume to start tracking performance.</p> : null}

      {items.length ? (
        <div className="resumeTableWrap">
          <table className="resumeTable">
            <thead>
              <tr>
                <th scope="col">Code</th>
                <th scope="col">Variant</th>
                <th scope="col">Primary role</th>
                <th scope="col">Skills</th>
                <th scope="col" className="numeric">Submissions</th>
                {anyOutcomes ? <th scope="col" className="numeric">Acceptance</th> : null}
                <th scope="col"><span className="visuallyHidden">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const isOpen = selectedId === item.resume.id
                const label = item.resume.variant_label || item.resume.file_name
                return (
                  <tr key={item.resume.id} className={isOpen ? 'selected' : ''}>
                    <th scope="row" className="resumeCodeCell">
                      <button type="button" onClick={() => select(item.resume.id)} aria-expanded={isOpen}>
                        {code(item)}
                      </button>
                    </th>
                    <td className="resumeLabelCell" title={label}>{label}</td>
                    <td className="subtle">{item.resume.primary_role || 'Role not set'}</td>
                    <td className="resumeSkillsCell">
                      {item.resume.structured_skills.length ? (
                        <span className="skillChips">
                          {item.resume.structured_skills.slice(0, 4).map((skill) => <span className="trackingChip" key={skill}>{skill}</span>)}
                          {item.resume.structured_skills.length > 4 ? <span className="subtle">+{item.resume.structured_skills.length - 4}</span> : null}
                        </span>
                      ) : <span className="subtle">—</span>}
                    </td>
                    <td className="numeric">{item.submission_count.toLocaleString()}</td>
                    {anyOutcomes ? <td className="numeric">{Math.round(item.acceptance_rate * 100)}%</td> : null}
                    <td className="resumeRowActions">
                      {onNavigateToSettings && (!item.resume.primary_role || !item.resume.variant_label) ? (
                        <button type="button" className="linkButton" onClick={() => onNavigateToSettings(item.resume.id)}>Add role &amp; label</button>
                      ) : null}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {selectedId && funnel ? (
        funnel.total_submissions ? (
          <section className="funnelStrip" aria-label="Resume funnel">
            <div className="funnelStat"><strong>{funnel.total_submissions}</strong><span>Submitted</span></div>
            <div className="funnelStat"><strong>{Math.round(funnel.view_rate * 100)}%</strong><span>Viewed</span></div>
            <div className="funnelStat"><strong>{Math.round(funnel.shortlist_rate * 100)}%</strong><span>Shortlisted</span></div>
            <div className="funnelStat"><strong>{Math.round(funnel.interview_rate * 100)}%</strong><span>Interviewed</span></div>
            <div className="funnelStat"><strong>{Math.round(funnel.offer_rate * 100)}%</strong><span>Offered</span></div>
            <div className="funnelStat"><strong>{Math.round(funnel.hire_rate * 100)}%</strong><span>Hired</span></div>
          </section>
        ) : (
          <p className="subtle funnelEmpty">
            No outcomes logged for this resume yet. These rates only move when you advance a submission&rsquo;s
            status below — they measure your logging, not the resume.
          </p>
        )
      ) : null}

      {selectedId ? <SubmissionsTab apiBase={apiBase} resumes={items.map((item) => item.resume)} resumeAssetId={selectedId} filterValues={filterValues} sortValue={sortValue} /> : null}
    </section>
  )
}
