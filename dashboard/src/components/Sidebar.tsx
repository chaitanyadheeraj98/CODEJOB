import { useEffect, useState } from 'react'

export type SidebarProps = {
  running: boolean
  queueCount: number
  failedCount: number
  runCount: number
  sentCount: number
  inboxCount?: number
  premiumCount: number
  labelThreadCount?: number
  labelTrackingEnabled?: boolean
  assistantUnseenCount?: number
  resumeTrackingEnabled: boolean
  applicationsEnabled?: boolean
  // Env-gated and off by default. The rows appear only when scheduling is
  // actually on, so no user is shown a page that answers "not enabled here".
  schedulingEnabled?: boolean
  // relationship_labeling is a member of the page union but has no nav row:
  // it is an internal calibration tool, not a feature.
  activePage: 'assistant' | 'run_queue' | 'manual_intake' | 'needs_review' | 'failed_mapping' | 'recent_runs' | 'sent_items' | 'inbox' | 'labels' | 'premium_numbers' | 'resume_tracking' | 'application_tracking' | 'relationship_labeling' | 'scheduled_tasks' | 'scheduled_review' | 'settings'
  onNavigate: (section: SidebarProps['activePage']) => void
  /**
   * Who is signed in, when sign-in is on at all.
   *
   * Absent means the backend has `feature_auth_enabled` off, and the rail must
   * look exactly as it did before sign-in existed - no account block, and no
   * sign-out offered for a session that does not exist.
   */
  account?: { email: string; isAdmin: boolean }
  onSignOut?: () => void
  signingOut?: boolean
}

export default function Sidebar({
  running,
  queueCount,
  failedCount,
  runCount,
  sentCount,
  inboxCount = 0,
  premiumCount,
  labelThreadCount = 0,
  labelTrackingEnabled = false,
  assistantUnseenCount = 0,
  resumeTrackingEnabled,
  applicationsEnabled = false,
  schedulingEnabled = false,
  activePage,
  onNavigate,
  account,
  onSignOut,
  signingOut = false,
}: SidebarProps) {
  // Remembered: someone who works with the rail closed should not reopen it on
  // every reload, and someone who never touches it never sees this state.
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('codejob.rail.collapsed') === '1')
  useEffect(() => {
    localStorage.setItem('codejob.rail.collapsed', collapsed ? '1' : '0')
  }, [collapsed])

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
    // Directly under Run Queue: it is an input to the same queue. No count -
    // there is no backlog here, only a place to paste.
    { key: 'manual_intake', label: 'Manual Intake' },
    { key: 'needs_review', label: 'Needs Review', count: queueCount },
    { key: 'failed_mapping', label: 'Failed Mapping', count: failedCount },
    { key: 'premium_numbers', label: 'Premium Contacts', count: premiumCount },
    ...(applicationsEnabled ? [{ key: 'application_tracking' as const, label: 'Application Tracking' }] : []),
    ...(resumeTrackingEnabled ? [{ key: 'resume_tracking' as const, label: 'Resume Tracking' }] : []),
    { key: 'sent_items', label: 'Sent Items', count: sentCount },
    { key: 'inbox', label: 'Inbox', count: inboxCount },
    // Directly under Inbox because it reads the same mail, filed rather than
    // arriving. Hidden entirely when label tracking is off, so the row never
    // leads to a page that only says "not enabled".
    ...(labelTrackingEnabled ? [{ key: 'labels' as const, label: 'Labels', count: labelThreadCount || undefined }] : []),
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
    <>
      {collapsed ? (
        <button
          type="button"
          className="railReopen"
          onClick={() => setCollapsed(false)}
          aria-label="Show navigation"
          aria-expanded={false}
        >
          <PanelIcon />
        </button>
      ) : null}
      <aside className={`leftRail ${collapsed ? 'collapsed' : ''}`} aria-hidden={collapsed}>
      <div className="brandWrap">
        <div className="brandIcon" aria-hidden="true">CJ</div>
        <div className="brandBlock">
          <div className="brand">CodeJob MailOps</div>
          <p className="brandSub">Recruitment Ops</p>
        </div>
        <button
          type="button"
          className="railToggle"
          onClick={() => setCollapsed(true)}
          aria-label="Hide navigation"
          aria-expanded
          tabIndex={collapsed ? -1 : 0}
        >
          <PanelIcon />
        </button>
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
        {account ? (
          <div className="accountBlock">
            {/* The address, not a display name. Running two test accounts side
                by side, "which account am I looking at" is the question this
                answers, and only the address answers it unambiguously. */}
            <span className="accountEmail" title={account.email}>{account.email}</span>
            {account.isAdmin ? <span className="accountRole">Admin</span> : null}
            <button
              className="signOutBtn"
              type="button"
              onClick={onSignOut}
              disabled={signingOut}
            >
              {signingOut ? 'Signing out...' : 'Sign out'}
            </button>
          </div>
        ) : null}
      </nav>
      </aside>
    </>
  )
}

// Drawn rather than a glyph: a bracket for the rail plus a chevron for the
// direction it moves. One 1.6 stroke, matching the rest of the chrome.
function PanelIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true" focusable="false">
      <rect x="1.6" y="2.4" width="12.8" height="11.2" rx="2.2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M6.2 2.4v11.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}
