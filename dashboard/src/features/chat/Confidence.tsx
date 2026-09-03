import type { ConfidenceLevel } from './renderers'

const LABELS: Record<ConfidenceLevel, string> = {
  confirmed: 'Confirmed',
  likely: 'Likely',
  possible: 'Possible',
}

const CLASSES: Record<ConfidenceLevel, string> = {
  confirmed: 'chatConfidenceConfirmed',
  likely: 'chatConfidenceLikely',
  possible: 'chatConfidencePossible',
}

// The badge is rendered from the payload field, never from the model's prose.
// That is the whole control: a model can describe a Possible link in confident
// language, and the badge beside it will still say Possible.
//
// Only the component is exported - react-refresh/only-export-components rejects
// a second export from a file that exports a component. Types live in
// renderers.ts.
export default function Confidence({ level }: { level: ConfidenceLevel }) {
  const label = LABELS[level]
  // A level this build does not know renders nothing rather than an unlabelled
  // badge: an unrecognised confidence must not read as a confident one.
  if (!label) return null
  return <span className={`chatConfidence ${CLASSES[level]}`}>{label}</span>
}
