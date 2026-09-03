import type { FilterFieldConfig, FilterValues, SortOption } from './components/FilterSortBar'
// Badge filters mirror the badges the cards carry (CandidateCard.tsx): a badge that
// cannot be filtered on only labels a card after you have already found it.
const badgeFilterFields: FilterFieldConfig[] = [
  { key: 'ats_strength', label: 'ATS strength', type: 'multiselect', options: [{ value: 'strong', label: 'Strong' }, { value: 'moderate', label: 'Moderate' }, { value: 'weak', label: 'Weak' }, { value: 'unknown', label: 'Unknown' }] },
  { key: 'contact_status', label: 'Contact status', type: 'multiselect', options: [{ value: 'active', label: 'Active' }, { value: 'flagged', label: 'Flagged' }] },
  { key: 'verification', label: 'Verification', type: 'multiselect', options: [{ value: 'unverified', label: 'Unverified' }, { value: 'verified', label: 'Verified' }, { value: 'trusted', label: 'Trusted' }] },
  { key: 'following', label: 'Following', type: 'multiselect', options: [{ value: 'bookmarked', label: 'Bookmarked' }, { value: 'tracked', label: 'Tracked' }, { value: 'active', label: 'Active Following' }] },
]
export const BADGE_FILTER_KEYS = badgeFilterFields.map((field) => field.key)
export const needsReviewFilterFields = (bucket = 'needs_review'): FilterFieldConfig[] => [
  // 'To' sits beside 'From' because on a bookmarked or nvoids-sourced item the recruiter
  // is the resolved To address, not the sender.
  { key: 'sender', label: 'From', type: 'text' }, { key: 'recipient', label: 'To', type: 'text' },
  { key: 'role', label: 'Job title', type: 'combobox', bucket }, { key: 'location', label: 'Location', type: 'combobox', bucket },
  { key: 'interview_type', label: 'Interview type', type: 'combobox', bucket },
  { key: 'source', label: 'Source', type: 'select', options: [{ value: 'all', label: 'All sources' }, { value: 'gmail', label: 'Gmail' }, { value: 'manual', label: 'Manual' }, { value: 'nvoids', label: 'Nvoids' }] },
  { key: 'sendability', label: 'Status', type: 'multiselect', options: [{ value: 'sendable', label: 'Sendable' }, { value: 'structural_block', label: 'Structural block' }, { value: 'eligibility_block', label: 'Eligibility block' }, { value: 'resume_block', label: 'Resume/mandatory block' }, { value: 'not_ready', label: 'Not ready' }] },
  { key: 'has_resume', label: 'Resume attached', type: 'boolean' }, { key: 'ats_score', label: 'ATS score', type: 'range', min: 0, max: 100 },
  ...badgeFilterFields,
]
export const needsReviewSortOptions: SortOption[] = [{ value: 'newest', label: 'Newest first' }, { value: 'oldest', label: 'Oldest first' }, { value: 'highest_score', label: 'Highest ATS score' }, { value: 'lowest_score', label: 'Lowest ATS score' }]
export const needsReviewDefaultFilterValues: FilterValues = { sender: '', recipient: '', role: '', location: '', interview_type: '', source: 'all', sendability: [], has_resume: null, ats_score: { min: null, max: null }, ats_strength: [], contact_status: [], verification: [], following: [] }
export function needsReviewFiltersToParams(v: FilterValues) { const p: Record<string,string> = {}; for (const k of ['sender','recipient','role','location','interview_type']) { const x=v[k] as string; if(x?.trim())p[k]=x.trim() } const source=v.source as string; if(source&&source!=='all')p.source=source; const statuses=v.sendability as string[]; if(statuses?.length)p.sendability=statuses.join(','); if(v.has_resume != null)p.has_resume=String(v.has_resume); const score=v.ats_score as {min:number|null;max:number|null}; if(score?.min!=null)p.min_ats_score=String(score.min); if(score?.max!=null)p.max_ats_score=String(score.max); for (const k of BADGE_FILTER_KEYS) { const selected=v[k] as string[]; if(selected?.length)p[k]=selected.join(',') } return p }
