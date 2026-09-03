import CandidateTable from './CandidateTable'
import CandidateTableCompact from './CandidateTableCompact'
import Chart from './Chart'
import ComparisonTable from './ComparisonTable'
import Disambiguation from './Disambiguation'
import MetricCards from './MetricCards'
import RankedList from './RankedList'
import RelationshipCluster from './RelationshipCluster'
import ScheduledTasks from './ScheduledTasks'
import QueueLink from './QueueLink'
import WebCitations from './WebCitations'
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
    case 'metric_cards':
      return <MetricCards data={payload.data} surface={surface} />
    case 'ranked_list':
      return <RankedList data={payload.data} surface={surface} />
    case 'comparison':
      return <ComparisonTable data={payload.data} surface={surface} />
    case 'chart':
      return <Chart data={payload.data} surface={surface} />
    case 'disambiguation':
      return <Disambiguation data={payload.data} surface={surface} />
    case 'relationship_cluster':
      return <RelationshipCluster data={payload.data} surface={surface} />
    case 'scheduled_tasks':
      return <ScheduledTasks data={payload.data} surface={surface} />
    case 'web_results':
      return <WebCitations data={payload.data} surface={surface} />
    case 'queue_link':
      return <QueueLink data={payload.data} surface={surface} />
    default:
      // Unreachable while the union is exhaustive, but a payload kind this
      // build does not know about must render nothing rather than throw - the
      // same contract every parse() already honours.
      return null
  }
}
