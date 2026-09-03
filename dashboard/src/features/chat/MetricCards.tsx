import { useChat } from './chatContext'
import Provenance from './Provenance'
import type { MetricCardsData } from './renderers'

type Props = { data: MetricCardsData; surface: 'page' | 'compact' }

function display(value: number | string): string {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(1)
  return value
}

// No card invents a number: `value` is displayed verbatim from the payload,
// which get_metrics computed server-side. The model supplies a metric name, a
// range and a day count, and nothing else.
export default function MetricCards({ data, surface }: Props) {
  const chat = useChat()

  if (!data.cards.length) {
    return (
      <figure className="chatMetricCards">
        <p className="subtle">{data.title}: nothing to show for this range.</p>
        <Provenance data={data.provenance} />
      </figure>
    )
  }

  return (
    <figure className="chatMetricCards">
      {data.title ? <strong className="chatMetricCardsTitle">{data.title}</strong> : null}
      <div className="chatMetricGrid">
        {data.cards.map((card) => {
          const body = (
            <>
              <span className="chatMetricValue">{display(card.value)}</span>
              <span className="chatMetricLabel">{card.label}{card.unit ? ` ${card.unit}` : ''}</span>
            </>
          )
          // Drill-through is page-only: the widget is a floating panel over the
          // page a click would navigate underneath it.
          return card.drill_to && surface === 'page' ? (
            <button
              key={card.label}
              type="button"
              className="chatMetricCard interactive"
              onClick={() => chat.navigateToQueue(card.drill_to!)}
            >
              {body}
            </button>
          ) : (
            <div key={card.label} className="chatMetricCard">{body}</div>
          )
        })}
      </div>
      <Provenance data={data.provenance} />
    </figure>
  )
}
