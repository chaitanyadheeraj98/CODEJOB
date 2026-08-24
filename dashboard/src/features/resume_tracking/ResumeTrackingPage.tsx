import { useState } from 'react'

import { listResumeOptions } from '../premium_numbers/api'
import type { ResumeAssetOption } from '../premium_numbers/types'
import ResumesTab from './ResumesTab'
import SubmissionsTab from './SubmissionsTab'

type Props = { apiBase: string; onNavigateToSettings?: (resumeId: number) => void }

export default function ResumeTrackingPage({ apiBase, onNavigateToSettings }: Props) {
  const [tab, setTab] = useState<'resumes' | 'submissions'>('resumes')
  const [resumes, setResumes] = useState<ResumeAssetOption[]>([])

  const openSubmissions = () => {
    setTab('submissions')
    listResumeOptions(apiBase).then(setResumes).catch(() => setResumes([]))
  }

  return (
    <section className="card pageSection resumeTrackingPage">
      <div className="resumeTrackingHeader">
        <div><h2>Resume Tracking</h2><p className="subtle">See which resume variants move through the funnel and why others stall.</p></div>
        <div className="tabBar" role="tablist" aria-label="Resume tracking views">
          <button type="button" role="tab" aria-selected={tab === 'resumes'} className={tab === 'resumes' ? 'active' : ''} onClick={() => setTab('resumes')}>Resumes</button>
          <button type="button" role="tab" aria-selected={tab === 'submissions'} className={tab === 'submissions' ? 'active' : ''} onClick={openSubmissions}>Submissions</button>
        </div>
      </div>
      {tab === 'resumes' ? <ResumesTab apiBase={apiBase} onNavigateToSettings={onNavigateToSettings} /> : <SubmissionsTab apiBase={apiBase} resumes={resumes} />}
    </section>
  )
}
