import CandidateTable from './CandidateTable'
import CandidateTableCompact from './CandidateTableCompact'
import type { RenderedPayload } from './renderers'

type Props = {
  messageId: number
  payload: RenderedPayload
  surface: 'page' | 'compact'
}

// The single mount point for every render payload. Both surfaces call this with
// the same parsed payload and differ only in `surface`, so a new visualization
// is one case here rather than an edit in ChatWidget and AssistantPage both.
//
// Only the component is exported: react-refresh/only-export-components rejects
// a second export from a file that exports a component, which cost two fixes
// during v1. Types live in renderers.ts.
export default function RenderedMessage({ messageId, payload, surface }: Props) {
  switch (payload.kind) {
    case 'candidate_table':
      return surface === 'page'
        ? <CandidateTable messageId={messageId} data={payload.data} />
        : <CandidateTableCompact data={payload.data} />
    default:
      // Unreachable while the union is exhaustive, but a payload kind this
      // build does not know about must render nothing rather than throw - the
      // same contract every parse() already honours.
      return null
  }
}
