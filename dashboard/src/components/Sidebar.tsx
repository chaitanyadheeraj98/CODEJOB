type SidebarProps = {
  running: boolean
  queueCount: number
  failedCount: number
  runCount: number
  sentCount: number
  premiumCount: number
  activePage: 'run_queue' | 'needs_review' | 'failed_mapping' | 'recent_runs' | 'sent_items' | 'premium_numbers'
  onNavigate: (section: 'run_queue' | 'needs_review' | 'failed_mapping' | 'recent_runs' | 'sent_items' | 'premium_numbers') => void
}

export default function Sidebar({
  running,
  queueCount,
  failedCount,
  runCount,
  sentCount,
  premiumCount,
  activePage,
  onNavigate,
}: SidebarProps) {
  const queueLabel = running ? 'Running now' : 'Ready'

  const navItems: Array<{
    key: SidebarProps['activePage']
    label: string
    count?: number
  }> = [
    { key: 'run_queue', label: 'Run Queue' },
    { key: 'needs_review', label: 'Needs Review', count: queueCount },
    { key: 'failed_mapping', label: 'Failed Mapping', count: failedCount },
    { key: 'premium_numbers', label: 'Premium Numbers', count: premiumCount },
    { key: 'sent_items', label: 'Sent Items', count: sentCount },
    { key: 'recent_runs', label: 'Recent Runs', count: runCount },
  ]

  return (
    <aside className="leftRail">
      <div className="brandWrap">
        <div className="brandIcon" aria-hidden="true">CJ</div>
        <div className="brandBlock">
          <div className="brand">CodeJob MailOps</div>
          <p className="brandSub">Recruitment Ops</p>
        </div>
      </div>
      <button className="composeBtn" type="button">
        New Campaign
      </button>
      <p className="queueStatus">{queueLabel}</p>
      <nav className="navList">
        {navItems.map((item) => (
          <button
            key={item.key}
            className={`navItem ${activePage === item.key ? 'active' : ''}`}
            type="button"
            onClick={() => onNavigate(item.key)}
          >
            <span>{item.label}</span>
            {item.count != null ? <span className="navCount">{item.count}</span> : null}
          </button>
        ))}
      </nav>
      <nav className="footerNav">
        <button className="navItem" type="button">Settings</button>
        <button className="navItem" type="button">Help Center</button>
      </nav>
    </aside>
  )
}
