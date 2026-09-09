import { useState } from 'react'

import { listResumeOptions } from '../premium_numbers/api'
import type { ResumeAssetOption } from '../premium_numbers/types'
import EditorTab from './EditorTab'
import ManageResumesTab from './ManageResumesTab'
import ResumesTab from './ResumesTab'
import RoleGapsTab from './RoleGapsTab'
import SubmissionsTab from './SubmissionsTab'
import type { FilterValues } from '../../components/FilterSortBar'

type TrackingTab = 'gaps' | 'resumes' | 'submissions' | 'manage' | 'editor'

type Props = {
  apiBase: string
  onNavigateToSettings?: (resumeId: number) => void
  onLibraryChanged?: () => void
  activeTab?: TrackingTab
  onTabChange?: (tab: TrackingTab) => void
  filterValues?: FilterValues
  sortValue?: string
}

export default function ResumeTrackingPage({ apiBase, onNavigateToSettings, onLibraryChanged, activeTab, onTabChange, filterValues = {}, sortValue = 'newest' }: Props) {
  const [localTab, setLocalTab] = useState<TrackingTab>('gaps')
  const tab = activeTab ?? localTab
  const setTab = (next: TrackingTab) => { setLocalTab(next); onTabChange?.(next) }
  const [resumes, setResumes] = useState<ResumeAssetOption[]>([])
  const [manageResumeId, setManageResumeId] = useState<number | null>(null)

  const openSubmissions = () => {
    setTab('submissions')
    listResumeOptions(apiBase).then(setResumes).catch(() => setResumes([]))
  }

  // Fixing a resume no longer means leaving the module for Settings.
  const openManage = (resumeId: number) => {
    setManageResumeId(resumeId)
    setTab('manage')
  }

  return (
    <section className="card pageSection resumeTrackingPage">
      <div className="resumeTrackingHeader">
        <div className="tabBar" role="tablist" aria-label="Resume tracking views">
          <button type="button" role="tab" aria-selected={tab === 'gaps'} className={tab === 'gaps' ? 'active' : ''} onClick={() => setTab('gaps')}>Role Gaps</button>
          <button type="button" role="tab" aria-selected={tab === 'resumes'} className={tab === 'resumes' ? 'active' : ''} onClick={() => setTab('resumes')}>Resumes</button>
          <button type="button" role="tab" aria-selected={tab === 'submissions'} className={tab === 'submissions' ? 'active' : ''} onClick={openSubmissions}>Submissions</button>
          <button type="button" role="tab" aria-selected={tab === 'manage'} className={tab === 'manage' ? 'active' : ''} onClick={() => setTab('manage')}>Manage</button>
          <button type="button" role="tab" aria-selected={tab === 'editor'} className={tab === 'editor' ? 'active' : ''} onClick={() => setTab('editor')}>Editor</button>
        </div>
      </div>
      {tab === 'gaps' ? <RoleGapsTab apiBase={apiBase} /> : null}
      {tab === 'resumes' ? <ResumesTab apiBase={apiBase} onManageResume={openManage} onNavigateToSettings={onNavigateToSettings} sortValue={sortValue} filterValues={filterValues} /> : null}
      {tab === 'submissions' ? <SubmissionsTab apiBase={apiBase} resumes={resumes} filterValues={filterValues} sortValue={sortValue} /> : null}
      {tab === 'manage' ? <ManageResumesTab apiBase={apiBase} focusResumeId={manageResumeId} onLibraryChanged={onLibraryChanged} /> : null}
      {tab === 'editor' ? <EditorTab apiBase={apiBase} /> : null}
    </section>
  )
}
