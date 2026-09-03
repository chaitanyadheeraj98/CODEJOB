import { useChat } from './chatContext'
import Provenance from './Provenance'
import type { ChartData, ChartPoint } from './renderers'

type Props = { data: ChartData; surface: 'page' | 'compact' }

const FUNNELS = new Set(['resume_funnel', 'application_pipeline'])

function width(value: number, max: number): string {
  // max_value of 0 means every bar is empty, not a division by zero.
  if (max <= 0) return '0%'
  return `${Math.max(0, Math.min(100, (value / max) * 100))}%`
}

// No charting library. The horizontal-bar pattern the Run Queue already uses
// (a div whose width is a percentage) covers every chart type here, because the
// server buckets the data and the client only draws proportional bars - there
// are no axes, ticks, scales or interpolation to compute.
export default function Chart({ data, surface }: Props) {
  const chat = useChat()
  const isFunnel = FUNNELS.has(data.chart_type)

  if (!data.series.length) {
    return (
      <figure className="chatAnalysisFigure">
        <p className="subtle">{data.title}: no data in this range.</p>
        <Provenance data={data.provenance} />
      </figure>
    )
  }

  // The widget column is too narrow for a bar to carry meaning, so the compact
  // surface renders the same numbers as a labelled list.
  if (surface === 'compact') {
    return (
      <figure className="chatAnalysisFigure">
        {data.title ? <strong>{data.title}</strong> : null}
        <ul className="chatChartList">
          {data.series.map((point) => (
            <li key={point.label}><span>{point.label}</span> <strong>{point.value}</strong></li>
          ))}
        </ul>
        <Provenance data={data.provenance} />
      </figure>
    )
  }

  const row = (point: ChartPoint) => (
    <>
      <span className="chatChartLabel">{point.label}</span>
      <span className="chatChartTrack">
        <span className="chatChartFill" style={{ width: width(point.value, data.max_value) }} />
      </span>
      <span className="chatChartValue">
        {point.value}
        {isFunnel && point.rate_of_previous != null ? (
          <small className="chatChartRate"> ({point.rate_of_previous}%)</small>
        ) : null}
      </span>
    </>
  )

  return (
    <figure className="chatAnalysisFigure">
      {data.title ? <strong>{data.title}</strong> : null}
      {/* The bars are decorative divs, so the value sits beside every one and
          the container names itself - a chart nobody can read with a screen
          reader is not accessible output. */}
      <div className="chatChart" role="group" aria-label={`${data.title || 'Chart'}, ${data.series.length} values`}>
        {data.series.map((point) => (
          point.drill_to ? (
            <button
              key={point.label}
              type="button"
              className="chatChartRow interactive"
              onClick={() => chat.navigateToQueue(point.drill_to!)}
            >
              {row(point)}
            </button>
          ) : (
            <div key={point.label} className="chatChartRow">{row(point)}</div>
          )
        ))}
      </div>
      <Provenance data={data.provenance} />
    </figure>
  )
}
