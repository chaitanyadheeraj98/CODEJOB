import { useState } from 'react'

import { listResumeOptions } from '../premium_numbers/api'
import type { ResumeAssetOption } from '../premium_numbers/types'
import ResumesTab from './ResumesTab'
import SubmissionsTab from './SubmissionsTab'
import type { FilterValues } from '../../components/FilterSortBar'

type Props = { apiBase: string; onNavigateToSettings?: (resumeId: number) => void; activeTab?: 'resumes' | 'submissions'; onTabChange?: (tab: 'resumes' | 'submissions') => void; filterValues?: FilterValues; sortValue?: string }

export default function ResumeTrackingPage({ apiBase, onNavigateToSettings, activeTab, onTabChange, filterValues = {}, sortValue = 'newest' }: Props) {
  const [localTab, setLocalTab] = useState<'resumes' | 'submissions'>('resumes')
  const tab = activeTab ?? localTab
  const setTab = (next: 'resumes' | 'submissions') => { setLocalTab(next); onTabChange?.(next) }
  const [resumes, setResumes] = useState<ResumeAssetOption[]>([])

  const openSubmissions = () => {
    setTab('submissions')
    listResumeOptions(apiBase).then(setResumes).catch(() => setResumes([]))
  }

  return (
    <section className="card pageSection resumeTrackingPage">
      <div className="resumeTrackingHeader">
        <div className="tabBar" role="tablist" aria-label="Resume tracking views">
          <button type="button" role="tab" aria-selected={tab === 'resumes'} className={tab === 'resumes' ? 'active' : ''} onClick={() => setTab('resumes')}>Resumes</button>
          <button type="button" role="tab" aria-selected={tab === 'submissions'} className={tab === 'submissions' ? 'active' : ''} onClick={openSubmissions}>Submissions</button>
        </div>
      </div>
      {tab === 'resumes' ? <ResumesTab apiBase={apiBase} onNavigateToSettings={onNavigateToSettings} sortValue={sortValue} filterValues={filterValues} /> : <SubmissionsTab apiBase={apiBase} resumes={resumes} filterValues={filterValues} sortValue={sortValue} />}
    </section>
  )
}
