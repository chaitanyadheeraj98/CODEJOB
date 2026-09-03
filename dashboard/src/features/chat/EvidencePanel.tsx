import Provenance from './Provenance'
import type { EvidenceEntry, InferenceProvenanceData } from './renderers'

const SIGNAL_LABELS: Record<string, string> = {
  thread: 'Email thread',
  recruiter: 'Recruiter',
  sender_domain: 'Sender domain',
  skills: 'Skills',
  job_title: 'Job title',
  location: 'Location',
  requirement_text: 'Requirement text',
  end_client: 'End client',
  implementation_partner: 'Implementation partner',
  domain: 'Domain',
}

const MATCH_LABELS: Record<EvidenceEntry['match'], string> = {
  exact: 'exact match',
  alias: 'matched through an alias',
  semantic: 'semantically similar',
  overlap: 'partial overlap',
  absent: 'not available',
}

function label(signal: string): string {
  return SIGNAL_LABELS[signal] ?? signal.replace(/_/g, ' ')
}

function Row({ entry }: { entry: EvidenceEntry }) {
  return (
    <li className="chatEvidenceRow">
      <span className="chatEvidenceSignal">{label(entry.signal)}</span>
      <span className="chatEvidenceValues">
        {entry.left_value || '—'} · {entry.right_value || '—'}
        {entry.normalized_to ? <em className="chatEvidenceNormalized"> → {entry.normalized_to}</em> : null}
      </span>
      <span className="chatEvidenceMatch">{MATCH_LABELS[entry.match]}</span>
      <span className="chatEvidenceContribution">{(entry.weight * entry.sub_score).toFixed(2)}</span>
    </li>
  )
}

// Wraps Provenance rather than replacing it: v2 established one caption
// component shared by every renderer, and a second provenance block would be
// the duplication its handoff explicitly forbids.
export default function EvidencePanel({ data }: { data: InferenceProvenanceData }) {
  const present = data.evidence.filter((entry) => entry.match !== 'absent')
  const absent = data.evidence.filter((entry) => entry.match === 'absent')

  return (
    <div className="chatEvidencePanel">
      <Provenance data={data} />
      <details className="chatEvidenceDetails">
        <summary>Why — {present.length} {present.length === 1 ? 'signal' : 'signals'} compared</summary>
        {present.length ? <ul className="chatEvidenceList">{present.map((entry) => <Row key={entry.signal} entry={entry} />)}</ul> : null}
        {absent.length ? (
          // Absent signals are shown, never silently omitted: two claims resting
          // on very different evidence would otherwise look identical, and the
          // weaker one would borrow the stronger one's credibility.
          <div className="chatEvidenceAbsent">
            <p className="subtle">Not available on these records — not counted against the match:</p>
            <ul className="chatEvidenceList">{absent.map((entry) => <Row key={entry.signal} entry={entry} />)}</ul>
          </div>
        ) : null}
        {!data.semantic_available ? (
          <p className="subtle chatEvidenceFooter">
            Compared on keywords only — no requirement embedding was available for one of these records.
          </p>
        ) : null}
      </details>
    </div>
  )
}
