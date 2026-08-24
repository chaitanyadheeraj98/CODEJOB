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

  const load = useCallback(() => {
    getResumePerformanceSummary(apiBase).then(setItems).catch((reason) => setError((reason as Error).message))
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
          return <article className={`resumeCard ${selectedId === item.resume.id ? 'selected' : ''}`} key={item.resume.id}>
            <div className="resumeCardMain">
              <button type="button" onClick={() => select(item.resume.id)} aria-expanded={selectedId === item.resume.id}>
                <span className="resumeCardTitle">{item.resume.variant_label || item.resume.file_name}</span>
                <span>{item.resume.primary_role || 'Role not set'}</span>
                <span>{item.submission_count} submissions · {pct}% acceptance</span>
              </button>
              {onNavigateToSettings && (!item.resume.primary_role || !item.resume.variant_label) ? <button type="button" className="resumeCardMissingDetails" onClick={() => onNavigateToSettings(item.resume.id)}>Add role & label</button> : null}
            </div>
            <div className="resumeCardBar" aria-hidden="true">
              <span className="resumeCardBarLabel">{pct}% acceptance</span>
              <span className="resumeCardBarTrack"><span className={`resumeCardBarFill ${tier}`} style={{ width: `${pct}%` }} /></span>
            </div>
            <div className="skillChips">{item.resume.structured_skills.map((skill) => <span className="trackingChip" key={skill}>{skill}</span>)}</div>
          </article>
        })}
      </div>
      {!items.length ? <p className="subtle">Upload a resume to start tracking performance.</p> : null}
      {selectedId && funnel ? <section className="funnelStrip" aria-label="Resume funnel"><span>Submitted <strong>{funnel.total_submissions}</strong></span><span>Viewed <strong>{Math.round(funnel.view_rate * 100)}%</strong></span><span>Shortlisted <strong>{Math.round(funnel.shortlist_rate * 100)}%</strong></span><span>Interviewed <strong>{Math.round(funnel.interview_rate * 100)}%</strong></span><span>Offered <strong>{Math.round(funnel.offer_rate * 100)}%</strong></span><span>Hired <strong>{Math.round(funnel.hire_rate * 100)}%</strong></span></section> : null}
      {selectedId ? <SubmissionsTab apiBase={apiBase} resumes={items.map((item) => item.resume)} resumeAssetId={selectedId} /> : null}
    </section>
  )
}
