import { useCallback, useEffect, useState } from 'react'

import { getResumeFunnel, getResumePerformanceSummary } from './api'
import SubmissionsTab from './SubmissionsTab'
import type { ResumeFunnelMetrics, ResumePerformanceSummaryItem } from './types'

type Props = { apiBase: string; onNavigateToSettings?: (resumeId: number) => void }

export default function ResumesTab({ apiBase, onNavigateToSettings }: Props) {
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

  return (
    <section className="resumeTrackingPanel">
      {error ? <p className="errorText" role="alert">{error}</p> : null}
      <div className="resumeCardGrid">
        {items.map((item) => {
          const pct = Math.round(item.acceptance_rate * 100)
          const tier = item.acceptance_rate >= 0.4 ? 'high' : item.acceptance_rate > 0 ? 'mid' : 'zero'
          const fullLabel = item.resume.variant_label || item.resume.file_name
          return <article className={`resumeCard ${tier} ${selectedId === item.resume.id ? 'selected' : ''}`} key={item.resume.id}>
            <header>
              <div className="resumeCardMain">
                <button type="button" onClick={() => select(item.resume.id)} aria-expanded={selectedId === item.resume.id}>
                  <span className="resumeCardTitle" title={fullLabel}>{fullLabel}</span>
                  <span className="resumeCardRole">{item.resume.primary_role || 'Role not set'}</span>
                </button>
                {onNavigateToSettings && (!item.resume.primary_role || !item.resume.variant_label) ? <button type="button" className="resumeCardMissingDetails" onClick={() => onNavigateToSettings(item.resume.id)}>Add role & label</button> : null}
              </div>
              <div className={`resumeCardStat ${tier}`}>
                <span className="resumeCardPct">{pct}%</span>
                <span className="resumeCardStatLabel">Acceptance</span>
              </div>
            </header>
            <div className="resumeCardBar" aria-hidden="true">
              <span className="resumeCardBarTrack"><span className={`resumeCardBarFill ${tier}`} style={{ width: `${pct}%` }} /></span>
              <span className="resumeCardBarLabel">{item.submission_count} submission{item.submission_count === 1 ? '' : 's'}</span>
            </div>
            {item.resume.structured_skills.length ? <div className="skillChips">{item.resume.structured_skills.map((skill) => <span className="trackingChip" key={skill}>{skill}</span>)}</div> : null}
          </article>
        })}
      </div>
      {loading ? <p className="subtle">Loading resumes...</p> : null}
      {!loading && !items.length ? <p className="subtle">Upload a resume to start tracking performance.</p> : null}
      {selectedId && funnel ? (
        <section className="funnelStrip" aria-label="Resume funnel">
          <div className="funnelStat"><strong>{funnel.total_submissions}</strong><span>Submitted</span></div>
          <div className="funnelStat"><strong>{Math.round(funnel.view_rate * 100)}%</strong><span>Viewed</span></div>
          <div className="funnelStat"><strong>{Math.round(funnel.shortlist_rate * 100)}%</strong><span>Shortlisted</span></div>
          <div className="funnelStat"><strong>{Math.round(funnel.interview_rate * 100)}%</strong><span>Interviewed</span></div>
          <div className="funnelStat"><strong>{Math.round(funnel.offer_rate * 100)}%</strong><span>Offered</span></div>
          <div className="funnelStat"><strong>{Math.round(funnel.hire_rate * 100)}%</strong><span>Hired</span></div>
        </section>
      ) : null}
      {selectedId ? <SubmissionsTab apiBase={apiBase} resumes={items.map((item) => item.resume)} resumeAssetId={selectedId} /> : null}
    </section>
  )
}
