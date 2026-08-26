import { useState } from 'react'

import { listResumeOptions } from '../premium_numbers/api'
import type { ResumeAssetOption } from '../premium_numbers/types'
import ResumesTab from './ResumesTab'
import SubmissionsTab from './SubmissionsTab'
import type { FilterValues } from '../../components/FilterSortBar'
import { submissionDefaultFilterValues } from './submissionFilters'

type Props = { apiBase: string; onNavigateToSettings?: (resumeId: number) => void; applicationsFilterValues?:FilterValues;onApplicationsFilterChange?:(values:FilterValues)=>void;applicationsSortValue?:string;onApplicationsSortChange?:(value:string)=>void }

export default function ResumeTrackingPage({ apiBase, onNavigateToSettings, applicationsFilterValues=submissionDefaultFilterValues,onApplicationsFilterChange=()=>undefined,applicationsSortValue='newest',onApplicationsSortChange=()=>undefined }: Props) {
  const [tab, setTab] = useState<'resumes' | 'submissions'>('resumes')
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
      {tab === 'resumes' ? <ResumesTab apiBase={apiBase} onNavigateToSettings={onNavigateToSettings} filterValues={applicationsFilterValues} onFilterChange={onApplicationsFilterChange} applicationsSortValue={applicationsSortValue} onApplicationsSortChange={onApplicationsSortChange} /> : <SubmissionsTab apiBase={apiBase} resumes={resumes} filterValues={applicationsFilterValues} onFilterChange={onApplicationsFilterChange} sortValue={applicationsSortValue} onSortChange={onApplicationsSortChange} />}
    </section>
  )
}
