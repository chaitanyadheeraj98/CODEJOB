import type { ResumeAssetOption } from '../premium_numbers/types'

/**
 * The skills to show for a resume in a list row.
 *
 * `structured_skills` is the curated field, but it is empty on every resume in
 * the live library while `skills_text` is populated on all of them - so a row
 * that only reads the curated field shows nothing at all. Fall back to the
 * extracted text, which is comma-separated in the same way.
 */
export function displaySkills(resume: Pick<ResumeAssetOption, 'structured_skills' | 'skills_text'>): string[] {
  if (resume.structured_skills?.length) return resume.structured_skills
  return (resume.skills_text ?? '').split(',').map((skill) => skill.trim()).filter(Boolean)
}

const KEEP_LOWER = new Set(['and', 'or', 'of', 'the', 'for', 'in', 'to', 'a', 'an'])

// Mirrors _LABEL_CANONICAL in resume_enrichment_service.py. The server normalises
// on write, so stored labels already look like this; this keeps a legacy row that
// predates that from rendering differently to the row beside it.
const CANONICAL: Record<string, string> = {
  edtech: 'EdTech', healthtech: 'HealthTech', insurtech: 'InsurTech',
  saas: 'SaaS', paas: 'PaaS', iaas: 'IaaS', iot: 'IoT',
  it: 'IT', hr: 'HR', erp: 'ERP', crm: 'CRM', api: 'API',
  ai: 'AI', ml: 'ML', b2b: 'B2B', b2c: 'B2C',
}

function titleCaseSegment(segment: string): string {
  let index = 0
  return segment.trim().replace(/[\p{L}\p{N}]+/gu, (word) => {
    const isFirst = index === 0
    index += 1
    const canonical = CANONICAL[word.toLowerCase()]
    if (canonical) return canonical
    // A word that already carries an inner capital (EdTech, J2EE, IT, SaaS) is
    // left alone - title-casing it would be a downgrade, not a fix.
    if (/[A-Z]/.test(word.slice(1))) return word
    if (!isFirst && KEEP_LOWER.has(word.toLowerCase())) return word.toLowerCase()
    return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
  })
}

/**
 * Present a variant label in consistent title case.
 *
 * Labels are not typed by hand: the resume-evidence extractor stores the model's
 * `domain` string verbatim (resume_enrichment_service._apply_role_and_label), so
 * the same list of domains arrives as "banking, healthcare, telecom, EdTech" on
 * one resume and "Banking, Healthcare, Telecom, EdTech" on the next.
 *
 * This is display only. The stored value is untouched, so the editor keeps
 * showing exactly what is saved and a label typed by hand is never rewritten
 * underneath the person who typed it.
 */
export function formatVariantLabel(label: string | null | undefined): string {
  return (label ?? '')
    .split(',')
    .map((segment) => titleCaseSegment(segment))
    .filter(Boolean)
    .join(', ')
}
