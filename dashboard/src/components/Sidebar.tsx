export type SidebarProps = {
  running: boolean
  queueCount: number
  failedCount: number
  runCount: number
  sentCount: number
  inboxCount?: number
  premiumCount: number
  assistantUnseenCount?: number
  resumeTrackingEnabled: boolean
  applicationsEnabled?: boolean
  // Env-gated and off by default. The rows appear only when scheduling is
  // actually on, so no user is shown a page that answers "not enabled here".
  schedulingEnabled?: boolean
  // relationship_labeling is a member of the page union but has no nav row:
  // it is an internal calibration tool, not a feature.
  activePage: 'assistant' | 'run_queue' | 'needs_review' | 'failed_mapping' | 'recent_runs' | 'sent_items' | 'inbox' | 'premium_numbers' | 'resume_tracking' | 'application_tracking' | 'relationship_labeling' | 'scheduled_tasks' | 'scheduled_review' | 'settings'
  onNavigate: (section: SidebarProps['activePage']) => void
}

export default function Sidebar({
  running,
  queueCount,
  failedCount,
  runCount,
  sentCount,
  inboxCount = 0,
  premiumCount,
  assistantUnseenCount = 0,
  resumeTrackingEnabled,
  applicationsEnabled = false,
  schedulingEnabled = false,
  activePage,
  onNavigate,
}: SidebarProps) {
  const queueLabel = running ? 'Running now' : 'Ready'

  const navItems: Array<{
    key: SidebarProps['activePage']
    label: string
    count?: number
  }> = [
    // `|| undefined` rather than the raw count: the row below renders a badge for
    // any non-null count, so a literal 0 would show an empty pill on every load.
    { key: 'assistant', label: 'CodeJob Assistant', count: assistantUnseenCount || undefined },
    { key: 'run_queue', label: 'Run Queue' },
    { key: 'needs_review', label: 'Needs Review', count: queueCount },
    { key: 'failed_mapping', label: 'Failed Mapping', count: failedCount },
    { key: 'premium_numbers', label: 'Premium Contacts', count: premiumCount },
    ...(applicationsEnabled ? [{ key: 'application_tracking' as const, label: 'Application Tracking' }] : []),
    ...(resumeTrackingEnabled ? [{ key: 'resume_tracking' as const, label: 'Resume Tracking' }] : []),
    { key: 'sent_items', label: 'Sent Items', count: sentCount },
    { key: 'inbox', label: 'Inbox', count: inboxCount },
    { key: 'recent_runs', label: 'Recent Runs', count: runCount },
    // temp157 §8.1: no scheduled task may exist that the user cannot see.
    ...(schedulingEnabled
      ? [
        { key: 'scheduled_tasks' as const, label: 'Scheduled Tasks' },
        { key: 'scheduled_review' as const, label: 'Scheduled Review' },
      ]
      : []),
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
        <button
          className={`navItem ${activePage === 'settings' ? 'active' : ''}`}
          type="button"
          onClick={() => onNavigate('settings')}
        >
          Settings
        </button>
        <button className="navItem" type="button">Help Center</button>
      </nav>
    </aside>
  )
}
