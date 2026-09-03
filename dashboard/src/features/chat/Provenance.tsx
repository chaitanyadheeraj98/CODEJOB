import type { ProvenanceData } from './renderers'

// The caption under every visualization. One component, reused by every
// analysis renderer, so the label is impossible to omit by forgetting it - a
// number whose range and exclusions are invisible is not an inspectable number.
export default function Provenance({ data }: { data: ProvenanceData }) {
  const range = data.date_range.from || data.date_range.to
    ? `${data.date_range.from ?? 'the beginning'} to ${data.date_range.to ?? 'now'}`
    : null
  const filters = Object.entries(data.filters)

  return (
    <figcaption className="chatProvenance">
      <span className="chatProvenanceMetric">{data.metric}</span>
      {range ? <span>{range}</span> : null}
      {filters.length ? <span>{filters.map(([key, value]) => `${key}: ${value}`).join(' · ')}</span> : null}
      <span>{data.row_count} {data.row_count === 1 ? 'row' : 'rows'} read</span>
      {data.assumptions.length ? (
        <ul className="chatProvenanceAssumptions">
          {data.assumptions.map((assumption) => <li key={assumption}>{assumption}</li>)}
        </ul>
      ) : null}
    </figcaption>
  )
}
