import type { UnavailableData } from './renderers'

// The server refused to build an aggregate because the column it would have
// read is not collected. This is deliberately not a chart with a caveat: a
// trend line over an empty column still reads as a trend, whatever the caption
// says, so nothing is drawn at all.
//
// It is also not an error. Nothing failed, and there is nothing for the user to
// retry or fix. The copy therefore describes the system - the field was never
// collected - and never the world, because an empty column is not evidence that
// the thing it would have measured does not exist.

// Same family as the partial mark in Provenance: 16-unit box, 6.25 radius,
// 1.5 stroke. A barred ring, not a cross - this is "cannot be drawn", not
// "something went wrong".
function BarredMark() {
  return (
    <svg className="chatUnavailableMark" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <line x1="4.6" y1="8" x2="11.4" y2="8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

export default function Unavailable({ data }: { data: UnavailableData }) {
  return (
    <section className="chatUnavailable" role="note">
      {/* The heading does not repeat `data.subject`. The server phrases it as
          "the <id> chart", so echoing it produced "No chart for the
          prime_vendor chart" - the word twice, and a raw column id shown to
          someone who never sees column ids. The blocked field's own label says
          which data is missing, and this block replaces the chart that was
          asked for, so the subject was never carrying its weight. */}
      <p className="chatUnavailableHead">
        <BarredMark />
        <span>No chart drawn.</span>
      </p>
      <ul className="chatUnavailableReasons">
        {data.blocked.map((entry) => (
          <li key={entry.field}>
            <strong>{entry.label}</strong>
            {entry.reason ? <> — {entry.reason}</> : null}
          </li>
        ))}
      </ul>
    </section>
  )
}
