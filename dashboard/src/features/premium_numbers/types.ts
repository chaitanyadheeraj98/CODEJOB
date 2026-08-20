export type PaginatedListResponse<TItem> = {
  items: TItem[]
  next_cursor: number | null
  has_next: boolean
}

export type NumberReviewCard = {
  id: number
  source_email_id: number | null
  source_external_opportunity_id: number | null
  source_lead_id: number | null
  normalized_phone_number: string
  display_phone_number: string
  owner_name: string
  company: string
  designation: string
  confidence: 'high' | 'medium' | 'low'
  purpose: string
  evidence_snippet: string
  email_subject: string
  email_sender: string
  contact_email: string
  contact_type: string
  recruiter_relevance_score: number
  relevance_reason: string
  extraction_source: string
  scored_with: string
  gmail_open_url: string
  state: string
  created_at: string
  updated_at: string
  linkedin_url?: string
}

export type PremiumNumberVersion = {
  id: number
  role: string
  owner_name: string
  company: string
  designation: string
  contact_email: string
  confidence: string
  extraction_source: string
  recruiter_email_id: number | null
  external_opportunity_id: number | null
  source_url: string | null
  created_at: string
}

type ContactCardBase = {
  id: number
  normalized_phone_number: string
  display_phone_number: string
  company: string
  source_type: 'gmail' | 'nvoids' | null
  source_id: number | null
  source_link_url: string | null
  active_lead_id: number | null
  version_count: number
  is_recruiter: boolean
  is_employer: boolean
  recruiter_relevance_score: number | null
  status: 'Active' | 'Flagged'
  flagged: boolean
  created_at: string
  updated_at: string
}

export type RecruiterNumberCard = ContactCardBase & {
  recruiter_name: string
  designation: string
  recruiter_email: string
  first_detected_email_id: number | null
  linkedin_url: string
  total_opportunity_count: number
  last_email_received_at: string | null
}

export type EmployerNumberCard = ContactCardBase & {
  owner_name: string
  source_email_id: number | null
}

export type OpportunityStatus = 'New' | 'Called' | 'Applied' | 'Follow Up' | 'Closed' | 'Not Interested'

export type RecruiterOpportunityCard = {
  id: number
  recruiter_number_id: number
  source_email_id: number | null
  gmail_message_id: string
  source_type: 'gmail' | 'nvoids'
  source_url: string | null
  external_opportunity_id: number | null
  email_id: number | null
  email_subject: string
  email_sender: string
  gmail_open_url: string
  received_at: string | null
  job_title: string
  end_client: string
  location: string
  work_mode: string
  visa_restrictions: string
  resume_file_name: string
  implementation_partner: string
  prime_vendor: string
  domain: string
  extracted_skills: string
  evidence: string
  recruiter_name: string
  recruiter_email: string
  recruiter_phone_display: string
  recruiter_phone_normalized: string
  recruiter_company: string
  linkedin_url: string
  status: OpportunityStatus
  notes: string
  cold_call_script: string | null
  cold_call_script_updated_at: string | null
  created_at: string
  updated_at: string
}

export type InventoryStatusFilter = 'all' | 'Pending' | 'Active' | 'Flagged'
export type InventoryCategoryFilter = 'all' | 'Recruiter' | 'Employer'
export type InventorySourceFilter = 'all' | 'gmail' | 'nvoids'
export type InventoryAction = 'mark-recruiter' | 'mark-employer' | 'rescore' | 'delete'

export type InventoryRow = {
  key: string
  kind: 'review' | 'contact'
  id: number
  number: string
  owner: string
  company: string
  categories: Array<'Recruiter' | 'Employer'>
  status: 'Pending' | 'Active' | 'Flagged'
  score: number | null
  sourceType: 'gmail' | 'nvoids' | null
  lastCheckedAt: string
  review?: NumberReviewCard
  recruiter?: RecruiterNumberCard
  employer?: EmployerNumberCard
}

export type ReviewEdits = Partial<Pick<
  NumberReviewCard,
  'owner_name' | 'company' | 'display_phone_number' | 'contact_email' | 'designation' | 'linkedin_url'
>>
