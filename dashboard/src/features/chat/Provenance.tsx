import type { CoverageEntry, ProvenanceData } from './renderers'

// The caption under every visualization. One component, reused by every
// analysis renderer, so the label is impossible to omit by forgetting it - a
// number whose range and exclusions are invisible is not an inspectable number.
//
// Coverage follows the same reasoning one step further. A chart drawn from a
// column that is 4% populated looks exactly like a chart drawn from a complete
// one, and the caption is where that difference has to become visible. Complete
// columns stay in the quiet strip; an incomplete one is lifted out of it,
// because a warning set in 11px at 70% opacity is not a warning.

const NUMBER = new Intl.NumberFormat()

// Drawn, not borrowed: a ring with a wedge missing, which is the shape of the
// fact it reports. Fixed at roughly two thirds rather than tracking the real
// percentage - at 14px a 98.9% wedge is indistinguishable from a full circle,
// and an icon that reads "complete" beside the word "partial" is worse than no
// icon. The number carries the precision; the mark carries the category.
function PartialMark() {
  return (
    <svg className="chatCoverageMark" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M8 8 V1.75 A6.25 6.25 0 0 1 13.4 11.1 Z" fill="currentColor" />
    </svg>
  )
}

function coverageLine(entry: CoverageEntry): string {
  return `${NUMBER.format(entry.populated)} of ${NUMBER.format(entry.total)} records (${entry.percent}%)`
}

export default function Provenance({ data }: { data: ProvenanceData }) {
  const range = data.date_range.from || data.date_range.to
    ? `${data.date_range.from ?? 'the beginning'} to ${data.date_range.to ?? 'now'}`
    : null
  const filters = Object.entries(data.filters)
  // Tolerates a payload without coverage the way asProvenance already tolerates
  // one without filters or assumptions. A caption that throws takes the whole
  // message down with it, and an older payload is not an error.
  const coverage = data.coverage ?? []
  const partial = coverage.filter((entry) => !entry.complete)
  const complete = coverage.filter((entry) => entry.complete)

  return (
    <>
      {partial.length ? (
        <div className="chatCoverageWarning" role="note">
          <PartialMark />
          <div className="chatCoverageWarningBody">
            <strong>Drawn from partial data.</strong>
            <ul>
              {partial.map((entry) => (
                <li key={entry.field}>
                  {entry.label} is recorded on {coverageLine(entry)}.
                  {' '}
                  <span className="chatCoverageConsequence">
                    The remaining {NUMBER.format(entry.total - entry.populated)} are not counted here,
                    so this shows what is recorded, not what happened.
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
      <figcaption className="chatProvenance">
        <span className="chatProvenanceMetric">{data.metric}</span>
        {range ? <span>{range}</span> : null}
        {filters.length ? <span>{filters.map(([key, value]) => `${key}: ${value}`).join(' · ')}</span> : null}
        {/* Grouped like the coverage figures beside it. Unformatted "8836 rows
            read" next to "8,836 of 8,836 records" is two conventions in one
            line; the adjacency is new, so the mismatch is too. */}
        <span className="chatCoverageComplete">
          {NUMBER.format(data.row_count)} {data.row_count === 1 ? 'row' : 'rows'} read
        </span>
        {complete.map((entry) => (
          // Stated even at 100%: "complete" is a measurement, and a caption that
          // only speaks up when something is wrong teaches the reader that
          // silence means unchecked rather than checked.
          <span key={entry.field} className="chatCoverageComplete">
            {entry.label} {coverageLine(entry)}
          </span>
        ))}
        {data.assumptions.length ? (
          <ul className="chatProvenanceAssumptions">
            {data.assumptions.map((assumption) => <li key={assumption}>{assumption}</li>)}
          </ul>
        ) : null}
      </figcaption>
    </>
  )
}
