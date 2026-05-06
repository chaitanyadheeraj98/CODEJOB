type SidebarProps = {
  running: boolean
  queueCount: number
  failedCount: number
  runCount: number
  sentCount: number
  activePage: 'run_queue' | 'needs_review' | 'failed_mapping' | 'recent_runs' | 'sent_items'
  onNavigate: (section: 'run_queue' | 'needs_review' | 'failed_mapping' | 'recent_runs' | 'sent_items') => void
}

export default function Sidebar({
  running,
  queueCount,
  failedCount,
  runCount,
  sentCount,
  activePage,
  onNavigate,
}: SidebarProps) {
  const queueLabel = running ? 'Running now' : 'Ready'

  return (
    <aside className="leftRail">
      <div className="brandBlock">
        <p className="brandKicker">Control Center</p>
        <div className="brand">CodeJob MailOps</div>
      </div>
      <button
        className={`composeBtn ${activePage === 'run_queue' ? 'active' : ''}`}
        type="button"
        onClick={() => onNavigate('run_queue')}
      >
        <span>Run Queue</span>
        <small>{queueLabel}</small>
      </button>
      <nav className="navList">
        <button
          className={`navItem ${activePage === 'needs_review' ? 'active' : ''}`}
          type="button"
          onClick={() => onNavigate('needs_review')}
        >
          <span>Needs Review</span> <span>{queueCount}</span>
        </button>
        <button
          className={`navItem ${activePage === 'failed_mapping' ? 'active' : ''}`}
          type="button"
          onClick={() => onNavigate('failed_mapping')}
        >
          <span>Failed Mapping</span> <span>{failedCount}</span>
        </button>
        <button
          className={`navItem ${activePage === 'sent_items' ? 'active' : ''}`}
          type="button"
          onClick={() => onNavigate('sent_items')}
        >
          <span>Sent Items</span> <span>{sentCount}</span>
        </button>
        <button
          className={`navItem ${activePage === 'recent_runs' ? 'active' : ''}`}
          type="button"
          onClick={() => onNavigate('recent_runs')}
        >
          <span>Recent Runs</span> <span>{runCount}</span>
        </button>
      </nav>
    </aside>
  )
}
