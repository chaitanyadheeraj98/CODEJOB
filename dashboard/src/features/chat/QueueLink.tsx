import { useChat } from './chatContext'
import type { QueueLinkData } from './renderers'

type Props = { data: QueueLinkData; surface: 'page' | 'compact' }

const FILTER_LABELS: Record<string, string> = {
  sender: 'From',
  recipient: 'To',
  role: 'Job title',
  location: 'Location',
  interview_type: 'Interview type',
  source: 'Source',
  sendability: 'Status',
  has_resume: 'Resume attached',
  min_ats_score: 'ATS score from',
  max_ats_score: 'ATS score to',
  ats_strength: 'ATS strength',
  contact_status: 'Contact status',
  verification: 'Verification',
  following: 'Following',
  date_filter: 'Date',
  date_from: 'From date',
  date_to: 'To date',
  status: 'Status',
  q: 'Search',
  domain: 'Domain',
  favorite: 'Favourite',
  source_type: 'Source',
  resume_asset_id: 'Resume',
}

const DATE_LABELS: Record<string, string> = {
  today: 'today',
  yesterday: 'yesterday',
  last_7_days: 'the last 7 days',
  custom: 'a custom range',
}

function describe(key: string, value: string): string {
  const label = FILTER_LABELS[key] ?? key.replace(/_/g, ' ')
  if (key === 'date_filter') return `${label}: ${DATE_LABELS[value] ?? value}`
  return `${label}: ${value.split(',').join(', ')}`
}

// Navigation changes no data, so this is a button rather than a proposal card -
// the user performs the action by clicking, and there is nothing to confirm.
export default function QueueLink({ data, surface }: Props) {
  const chat = useChat()
  const filters = Object.entries(data.filters)

  return (
    <div className="chatQueueLink">
      <button
        type="button"
        className="chatQueueLinkButton"
        onClick={() => chat.navigateToQueue({ page: data.page, tab: data.tab, filters: data.filters })}
      >
        Open {data.label}
        {data.title ? <span className="chatQueueLinkTitle">{data.title}</span> : null}
      </button>
      {surface === 'page' && filters.length ? (
        <ul className="chatQueueLinkFilters">
          {filters.map(([key, value]) => <li key={key}>{describe(key, value)}</li>)}
        </ul>
      ) : null}
      {surface === 'page' && data.dropped.length ? (
        <p className="subtle">
          Not applied: {data.dropped.map((item) => item.key).join(', ')}
        </p>
      ) : null}
    </div>
  )
}
