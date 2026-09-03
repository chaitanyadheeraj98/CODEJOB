import type { QueueTarget } from '../../queueNavigation'
import type { ChatMessage } from './types'

export type CandidateTableCell = string | number | null

export type CandidateTableRow = {
  candidate_id: number
  record_id: string | null
} & Record<string, CandidateTableCell>

export type CandidateTableData = {
  title: string
  columns: string[]
  rows: CandidateTableRow[]
  dropped: Array<{ candidate_id?: number; column?: string; reason: string }>
  truncated: boolean
}

// One member per visualization. The discriminant is what lets a single mount
// point hold more than one kind of output; without it a second handler parses
// correctly and then draws as a candidate table.
export type QueueLinkData = {
  page: string
  tab: string | null
  label: string
  title: string
  filters: Record<string, string>
  dropped: Array<{ key: string; reason: string }>
}

export type ProvenanceData = {
  metric: string
  source: string
  row_count: number
  date_range: { from: string | null; to: string | null }
  filters: Record<string, string>
  assumptions: string[]
}

// One contributing signal behind an inferred claim. `match: 'absent'` means the
// signal could not be compared on these records - it is not a mismatch, and the
// evidence panel groups it separately so two claims resting on very different
// evidence never display identically.
export type EvidenceEntry = {
  signal: string
  left_value: string
  right_value: string
  normalized_to: string
  match: 'exact' | 'alias' | 'semantic' | 'overlap' | 'absent'
  weight: number
  sub_score: number
  source: string
}

export type ConfidenceLevel = 'confirmed' | 'likely' | 'possible'

// Provenance for a *derived* claim: everything a measured number carries, plus
// how sure, why, and on which of the two score populations it was calibrated.
export type InferenceProvenanceData = ProvenanceData & {
  confidence: ConfidenceLevel
  score: number
  evidence: EvidenceEntry[]
  semantic_available: boolean
}

export type MetricCard = {
  label: string
  value: number | string
  unit: string
  delta: number | null
  drill_to: QueueTarget | null
}

export type MetricCardsData = {
  title: string
  cards: MetricCard[]
  provenance: ProvenanceData
}

export type RankedRow = {
  rank: number
  record_id: number
  label: string
  detail: string
  score: number
  reasons: string[]
  drill_to: QueueTarget | null
}

export type RankedListData = {
  title: string
  measure: string
  rows: RankedRow[]
  dropped: Array<{ key: string; reason: string }>
  provenance: ProvenanceData
}

export type ComparisonColumn = {
  record_id: number
  label: string
  values: Record<string, number | string | null>
}

export type ComparisonData = {
  title: string
  measures: Array<{ key: string; label: string }>
  columns: ComparisonColumn[]
  dropped: Array<{ key: string; reason: string }>
  provenance: ProvenanceData
}

export type ChartPoint = {
  label: string
  value: number
  rate_of_previous: number | null
  drill_to: QueueTarget | null
}

export type ChartData = {
  chart_type: string
  title: string
  series: ChartPoint[]
  max_value: number
  provenance: ProvenanceData
}

export type DisambiguationOption = { id: number; label: string; detail: string }

export type DisambiguationData = {
  kind: string
  query: string
  options: DisambiguationOption[]
  truncated: boolean
}

export type WebResult = { url: string; title: string; snippet: string }

export type WebResultsData = { query: string; results: WebResult[] }

export type RenderedPayload =
  | { kind: 'candidate_table'; data: CandidateTableData }
  | { kind: 'queue_link'; data: QueueLinkData }
  | { kind: 'metric_cards'; data: MetricCardsData }
  | { kind: 'ranked_list'; data: RankedListData }
  | { kind: 'comparison'; data: ComparisonData }
  | { kind: 'chart'; data: ChartData }
  | { kind: 'disambiguation'; data: DisambiguationData }
  | { kind: 'web_results'; data: WebResultsData }

export type RenderHandler = {
  parse: (message: ChatMessage) => RenderedPayload | null
}

function asRows(value: unknown): CandidateTableRow[] | null {
  if (!Array.isArray(value)) return null
  const rows: CandidateTableRow[] = []
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') return null
    const row = entry as Record<string, unknown>
    if (typeof row.candidate_id !== 'number') return null
    rows.push(row as CandidateTableRow)
  }
  return rows
}

/**
 * The provenance gate. A payload without a well-formed provenance block renders
 * NOTHING - not a chart with a caveat, which is still a chart. This is the most
 * important control in the phase: it is what makes a displayed number worth
 * believing, and it is enforced here rather than in the prompt.
 */
export function asProvenance(value: unknown): ProvenanceData | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const block = value as Record<string, unknown>
  if (typeof block.metric !== 'string' || !block.metric) return null
  if (typeof block.row_count !== 'number' || !Number.isFinite(block.row_count)) return null
  const range = block.date_range && typeof block.date_range === 'object' && !Array.isArray(block.date_range)
    ? block.date_range as Record<string, unknown>
    : {}
  return {
    metric: block.metric,
    source: typeof block.source === 'string' ? block.source : '',
    row_count: block.row_count,
    date_range: {
      from: typeof range.from === 'string' ? range.from : null,
      to: typeof range.to === 'string' ? range.to : null,
    },
    filters: asStringMap(block.filters),
    assumptions: Array.isArray(block.assumptions) ? block.assumptions.filter((item): item is string => typeof item === 'string') : [],
  }
}

const CONFIDENCE_LEVELS: readonly string[] = ['confirmed', 'likely', 'possible']
const MATCH_KINDS: readonly string[] = ['exact', 'alias', 'semantic', 'overlap', 'absent']

function asEvidence(value: unknown): EvidenceEntry[] | null {
  if (!Array.isArray(value) || !value.length) return null
  const entries: EvidenceEntry[] = []
  for (const item of value) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return null
    const raw = item as Record<string, unknown>
    if (typeof raw.signal !== 'string' || !raw.signal) return null
    if (typeof raw.match !== 'string' || !MATCH_KINDS.includes(raw.match)) return null
    if (typeof raw.weight !== 'number' || !Number.isFinite(raw.weight)) return null
    if (typeof raw.sub_score !== 'number' || !Number.isFinite(raw.sub_score)) return null
    entries.push({
      signal: raw.signal,
      left_value: typeof raw.left_value === 'string' ? raw.left_value : '',
      right_value: typeof raw.right_value === 'string' ? raw.right_value : '',
      normalized_to: typeof raw.normalized_to === 'string' ? raw.normalized_to : '',
      match: raw.match as EvidenceEntry['match'],
      weight: raw.weight,
      sub_score: raw.sub_score,
      source: typeof raw.source === 'string' ? raw.source : '',
    })
  }
  return entries
}

/**
 * The inference gate, and the reason v3 extends the provenance block rather
 * than sitting a second structure beside it. A claim nobody recorded renders
 * NOTHING unless it arrives with a confidence level, a server-computed score,
 * and at least one piece of evidence. A hedged claim is still a claim.
 */
export function asInferenceProvenance(value: unknown): InferenceProvenanceData | null {
  const base = asProvenance(value)
  if (!base) return null
  const block = value as Record<string, unknown>
  if (typeof block.confidence !== 'string' || !CONFIDENCE_LEVELS.includes(block.confidence)) return null
  if (typeof block.score !== 'number' || !Number.isFinite(block.score)) return null
  if (block.score < 0 || block.score > 1) return null
  const evidence = asEvidence(block.evidence)
  if (!evidence) return null
  return {
    ...base,
    confidence: block.confidence as ConfidenceLevel,
    score: block.score,
    evidence,
    semantic_available: block.semantic_available === true,
  }
}

function asQueueTarget(value: unknown): QueueTarget | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const target = value as Record<string, unknown>
  if (typeof target.page !== 'string' || !target.page) return null
  return {
    page: target.page,
    tab: typeof target.tab === 'string' ? target.tab : null,
    filters: asStringMap(target.filters),
  }
}

function asMetricCards(value: unknown): MetricCard[] | null {
  if (!Array.isArray(value)) return null
  const cards: MetricCard[] = []
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') return null
    const card = entry as Record<string, unknown>
    if (typeof card.label !== 'string' || !card.label) return null
    if (typeof card.value !== 'number' && typeof card.value !== 'string') return null
    cards.push({
      label: card.label,
      value: card.value,
      unit: typeof card.unit === 'string' ? card.unit : '',
      delta: typeof card.delta === 'number' ? card.delta : null,
      drill_to: asQueueTarget(card.drill_to),
    })
  }
  return cards
}

function asDropped(value: unknown): Array<{ key: string; reason: string }> {
  if (!Array.isArray(value)) return []
  return value.filter((entry): entry is { key: string; reason: string } =>
    !!entry && typeof entry === 'object'
    && typeof (entry as Record<string, unknown>).key === 'string'
    && typeof (entry as Record<string, unknown>).reason === 'string')
}

function asRankedRows(value: unknown): RankedRow[] | null {
  if (!Array.isArray(value)) return null
  const rows: RankedRow[] = []
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') return null
    const row = entry as Record<string, unknown>
    if (typeof row.record_id !== 'number' || typeof row.label !== 'string') return null
    if (typeof row.score !== 'number' || !Number.isFinite(row.score)) return null
    rows.push({
      rank: typeof row.rank === 'number' ? row.rank : rows.length + 1,
      record_id: row.record_id,
      label: row.label,
      detail: typeof row.detail === 'string' ? row.detail : '',
      score: row.score,
      reasons: Array.isArray(row.reasons) ? row.reasons.filter((item): item is string => typeof item === 'string') : [],
      drill_to: asQueueTarget(row.drill_to),
    })
  }
  return rows
}

function asComparisonColumns(value: unknown): ComparisonColumn[] | null {
  if (!Array.isArray(value)) return null
  const columns: ComparisonColumn[] = []
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') return null
    const column = entry as Record<string, unknown>
    if (typeof column.record_id !== 'number' || typeof column.label !== 'string') return null
    const raw = column.values && typeof column.values === 'object' && !Array.isArray(column.values)
      ? column.values as Record<string, unknown>
      : {}
    const values: Record<string, number | string | null> = {}
    for (const [key, item] of Object.entries(raw)) {
      values[key] = typeof item === 'number' || typeof item === 'string' ? item : null
    }
    columns.push({ record_id: column.record_id, label: column.label, values })
  }
  return columns
}

function asChartSeries(value: unknown): ChartPoint[] | null {
  if (!Array.isArray(value)) return null
  const points: ChartPoint[] = []
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') return null
    const point = entry as Record<string, unknown>
    if (typeof point.label !== 'string') return null
    if (typeof point.value !== 'number' || !Number.isFinite(point.value)) return null
    points.push({
      label: point.label,
      value: point.value,
      rate_of_previous: typeof point.rate_of_previous === 'number' ? point.rate_of_previous : null,
      drill_to: asQueueTarget(point.drill_to),
    })
  }
  return points
}

function asStringMap(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const out: Record<string, string> = {}
  for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
    if (typeof entry === 'string') out[key] = entry
  }
  return out
}

// Sibling to PROPOSAL_HANDLERS, for tool messages that display something rather
// than prepare a write. Same defensive shape: parse returns null on anything
// unexpected so a malformed payload degrades to "nothing rendered" instead of
// throwing inside the message list.
export const RENDER_HANDLERS: Record<string, RenderHandler> = {
  render_candidate_table: {
    parse: (message) => {
      try {
        const payload = JSON.parse(message.content) as Record<string, unknown>
        if (!payload || payload.action !== 'render_candidate_table') return null
        const rows = asRows(payload.rows)
        if (rows === null) return null
        const columns = Array.isArray(payload.columns) ? payload.columns.filter((c): c is string => typeof c === 'string') : []
        if (!columns.length) return null
        return {
          kind: 'candidate_table',
          data: {
            title: typeof payload.title === 'string' ? payload.title : '',
            columns,
            rows,
            dropped: Array.isArray(payload.dropped) ? payload.dropped as CandidateTableData['dropped'] : [],
            truncated: payload.truncated === true,
          },
        }
      } catch {
        return null
      }
    },
  },
  get_metrics: {
    parse: (message) => {
      try {
        const payload = JSON.parse(message.content) as Record<string, unknown>
        if (!payload || payload.action !== 'render_metric_cards') return null
        const cards = asMetricCards(payload.cards)
        if (cards === null) return null
        const provenance = asProvenance(payload.provenance)
        if (!provenance) return null
        return {
          kind: 'metric_cards',
          data: {
            title: typeof payload.title === 'string' ? payload.title : '',
            cards,
            provenance,
          },
        }
      } catch {
        return null
      }
    },
  },
  rank_opportunities: {
    parse: (message) => {
      try {
        const payload = JSON.parse(message.content) as Record<string, unknown>
        if (!payload || payload.action !== 'render_ranked_list') return null
        const rows = asRankedRows(payload.rows)
        if (rows === null) return null
        const provenance = asProvenance(payload.provenance)
        if (!provenance) return null
        return {
          kind: 'ranked_list',
          data: {
            title: typeof payload.title === 'string' ? payload.title : '',
            measure: typeof payload.measure === 'string' ? payload.measure : '',
            rows,
            dropped: asDropped(payload.dropped),
            provenance,
          },
        }
      } catch {
        return null
      }
    },
  },
  compare_records: {
    parse: (message) => {
      try {
        const payload = JSON.parse(message.content) as Record<string, unknown>
        if (!payload || payload.action !== 'render_comparison') return null
        const columns = asComparisonColumns(payload.columns)
        if (columns === null) return null
        const measures = Array.isArray(payload.measures)
          ? payload.measures.filter((entry): entry is { key: string; label: string } =>
              !!entry && typeof entry === 'object'
              && typeof (entry as Record<string, unknown>).key === 'string'
              && typeof (entry as Record<string, unknown>).label === 'string')
          : []
        if (!measures.length) return null
        const provenance = asProvenance(payload.provenance)
        if (!provenance) return null
        return {
          kind: 'comparison',
          data: {
            title: typeof payload.title === 'string' ? payload.title : '',
            measures,
            columns,
            dropped: asDropped(payload.dropped),
            provenance,
          },
        }
      } catch {
        return null
      }
    },
  },
  get_chart: {
    parse: (message) => {
      try {
        const payload = JSON.parse(message.content) as Record<string, unknown>
        if (!payload || payload.action !== 'render_chart') return null
        if (typeof payload.chart_type !== 'string' || !payload.chart_type) return null
        const series = asChartSeries(payload.series)
        if (series === null) return null
        const provenance = asProvenance(payload.provenance)
        if (!provenance) return null
        return {
          kind: 'chart',
          data: {
            chart_type: payload.chart_type,
            title: typeof payload.title === 'string' ? payload.title : '',
            series,
            // Server-computed, so a truncated series cannot silently rescale
            // itself. Falling back to the series maximum keeps an older
            // payload drawable rather than blank.
            max_value: typeof payload.max_value === 'number' && Number.isFinite(payload.max_value)
              ? payload.max_value
              : series.reduce((highest, point) => Math.max(highest, point.value), 0),
            provenance,
          },
        }
      } catch {
        return null
      }
    },
  },
  resolve_record_reference: {
    parse: (message) => {
      try {
        const payload = JSON.parse(message.content) as Record<string, unknown>
        // A single confident match resolves silently - the model proceeds and
        // there is nothing to draw. Only the multi-match case renders.
        if (!payload || payload.action !== 'render_disambiguation') return null
        if (typeof payload.kind !== 'string' || !payload.kind) return null
        if (!Array.isArray(payload.options)) return null
        const options: DisambiguationOption[] = []
        for (const entry of payload.options) {
          if (!entry || typeof entry !== 'object') return null
          const option = entry as Record<string, unknown>
          if (typeof option.id !== 'number' || typeof option.label !== 'string') return null
          options.push({
            id: option.id,
            label: option.label,
            detail: typeof option.detail === 'string' ? option.detail : '',
          })
        }
        if (options.length < 2) return null
        return {
          kind: 'disambiguation',
          data: {
            kind: payload.kind,
            query: typeof payload.query === 'string' ? payload.query : '',
            options,
            truncated: payload.truncated === true,
          },
        }
      } catch {
        return null
      }
    },
  },
  search_web: {
    parse: (message) => {
      try {
        const payload = JSON.parse(message.content) as Record<string, unknown>
        if (!payload || payload.action !== 'search_web') return null
        if (!Array.isArray(payload.results)) return null
        const results: WebResult[] = []
        for (const entry of payload.results) {
          if (!entry || typeof entry !== 'object') return null
          const row = entry as Record<string, unknown>
          if (typeof row.url !== 'string') return null
          results.push({
            url: row.url,
            title: typeof row.title === 'string' ? row.title : '',
            snippet: typeof row.snippet === 'string' ? row.snippet : '',
          })
        }
        return {
          kind: 'web_results',
          data: { query: typeof payload.query === 'string' ? payload.query : '', results },
        }
      } catch {
        return null
      }
    },
  },
  navigate_to_queue: {
    parse: (message) => {
      try {
        const payload = JSON.parse(message.content) as Record<string, unknown>
        if (!payload || payload.action !== 'navigate_to_queue') return null
        if (typeof payload.page !== 'string' || !payload.page) return null
        return {
          kind: 'queue_link',
          data: {
            page: payload.page,
            tab: typeof payload.tab === 'string' ? payload.tab : null,
            label: typeof payload.label === 'string' ? payload.label : payload.page,
            title: typeof payload.title === 'string' ? payload.title : '',
            filters: asStringMap(payload.filters),
            dropped: Array.isArray(payload.dropped) ? payload.dropped as QueueLinkData['dropped'] : [],
          },
        }
      } catch {
        return null
      }
    },
  },
}

export function renderForMessage(message: ChatMessage): RenderedPayload | null {
  if (message.role !== 'tool' || !message.tool_name) return null
  const handler = RENDER_HANDLERS[message.tool_name]
  if (!handler) return null
  return handler.parse(message)
}
