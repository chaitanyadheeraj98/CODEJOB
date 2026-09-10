import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { createRequestSequence } from '../../latestRequest'
import { fetchLabelThreads, promoteLabelThread, type LabelThread } from '../application_tracking/api'
import { listResumeOptions } from '../premium_numbers/api'
import type { ResumeAssetOption } from '../premium_numbers/types'
import {
  fetchLabelOverview,
  fetchThreadDossier,
  markThreadRead,
  type DossierContact,
  type DossierMessage,
  type LabelOverviewItem,
  type ThreadDossier,
} from './api'

type Props = {
  apiBase: string
  refreshToken: number
  onSyncLabels?: () => Promise<unknown>
  onOpenSettings?: () => void
}

const PAGE_SIZE = 25

/**
 * The Labels workspace: label -> thread -> the whole recruiter relationship.
 *
 * Three panes rather than the Inbox's two, because there is one more level of
 * nesting here than the Inbox has. The Inbox answers "what came in"; this
 * answers "what is happening with this submission", and the label is the thing
 * the person chose, so it gets its own rail instead of hiding in a dropdown.
 *
 * The right pane is the reason the page exists. It is not the labeled thread -
 * it is the labeled thread plus every message the watches derived from it
 * caught elsewhere in the mailbox, in one chronology, both directions. Messages
 * that arrived under a different Gmail thread are marked as such rather than
 * silently interleaved, so nobody has to wonder why an unfamiliar subject is on
 * the page.
 */
export default function LabelsPage({ apiBase, refreshToken, onSyncLabels, onOpenSettings }: Props) {
  const [labels, setLabels] = useState<LabelOverviewItem[]>([])
  const [untrackedLabelCount, setUntrackedLabelCount] = useState(0)
  const [activeLabel, setActiveLabel] = useState<string | null>(null)
  const [labelsLoading, setLabelsLoading] = useState(true)
  const [labelsError, setLabelsError] = useState('')

  const [threads, setThreads] = useState<LabelThread[]>([])
  const [threadTotal, setThreadTotal] = useState(0)
  const [threadPage, setThreadPage] = useState(1)
  const [threadsLoading, setThreadsLoading] = useState(false)
  const [threadsError, setThreadsError] = useState('')
  const [query, setQuery] = useState('')

  const [activeThreadId, setActiveThreadId] = useState<string | null>(null)
  const [dossier, setDossier] = useState<ThreadDossier | null>(null)
  const [dossierLoading, setDossierLoading] = useState(false)
  const [dossierError, setDossierError] = useState('')

  const [syncing, setSyncing] = useState(false)
  const [picker, setPicker] = useState(false)
  const [resumes, setResumes] = useState<ResumeAssetOption[]>([])
  const [resumeId, setResumeId] = useState<number | null>(null)
  const [promoting, setPromoting] = useState(false)
  const [revision, setRevision] = useState(0)

  // Selecting a label and selecting a thread both fire fetches that can land out
  // of order; the same guard the Reply Inbox needed.
  const threadRequests = useRef(createRequestSequence()).current
  const dossierRequests = useRef(createRequestSequence()).current

  useEffect(() => {
    let cancelled = false
    setLabelsLoading(true)
    setLabelsError('')
    fetchLabelOverview(apiBase)
      .then((result) => {
        if (cancelled) return
        setLabels(result.items)
        setUntrackedLabelCount(result.untracked_label_count)
        // Only auto-select when nothing is chosen, so a refresh does not yank
        // the person out of the label they are reading.
        setActiveLabel((current) => current ?? result.items.find((item) => item.thread_count > 0)?.name ?? result.items[0]?.name ?? null)
      })
      .catch((reason: Error) => {
        if (!cancelled) setLabelsError(reason.message || 'Could not load your Gmail labels.')
      })
      .finally(() => {
        if (!cancelled) setLabelsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [apiBase, refreshToken, revision])

  useEffect(() => {
    setThreadPage(1)
  }, [activeLabel, query])

  useEffect(() => {
    if (!activeLabel) {
      setThreads([])
      setThreadTotal(0)
      return
    }
    const ticket = threadRequests.next()
    setThreadsLoading(true)
    setThreadsError('')
    const filters: Record<string, string> = { label: activeLabel }
    if (query.trim()) filters.q = query.trim()
    fetchLabelThreads(apiBase, filters, 'newest', threadPage)
      .then((result) => {
        if (!threadRequests.isCurrent(ticket)) return
        setThreads(result.items)
        setThreadTotal(result.total)
        setActiveThreadId((current) =>
          current && result.items.some((item) => item.thread_id === current) ? current : result.items[0]?.thread_id ?? null,
        )
      })
      .catch((reason: Error) => {
        if (threadRequests.isCurrent(ticket)) setThreadsError(reason.message || 'Could not load threads for this label.')
      })
      .finally(() => {
        if (threadRequests.isCurrent(ticket)) setThreadsLoading(false)
      })
  }, [apiBase, activeLabel, query, threadPage, refreshToken, revision, threadRequests])

  useEffect(() => {
    if (!activeThreadId) {
      setDossier(null)
      return
    }
    const ticket = dossierRequests.next()
    setDossierLoading(true)
    setDossierError('')
    fetchThreadDossier(apiBase, activeThreadId)
      .then((result) => {
        if (dossierRequests.isCurrent(ticket)) setDossier(result)
      })
      .catch((reason: Error) => {
        if (dossierRequests.isCurrent(ticket)) setDossierError(reason.message || 'Could not open this conversation.')
      })
      .finally(() => {
        if (dossierRequests.isCurrent(ticket)) setDossierLoading(false)
      })
  }, [apiBase, activeThreadId, refreshToken, revision, dossierRequests])

  useEffect(() => {
    setPicker(false)
    setResumeId(null)
  }, [activeThreadId])

  const sync = useCallback(async () => {
    if (!onSyncLabels) return
    setSyncing(true)
    try {
      await onSyncLabels()
      setRevision((n) => n + 1)
    } finally {
      setSyncing(false)
    }
  }, [onSyncLabels])

  const openPicker = useCallback(async () => {
    setPicker(true)
    try {
      const options = await listResumeOptions(apiBase)
      setResumes(options.filter((resume) => resume.is_enabled))
    } catch {
      setResumes([])
    }
  }, [apiBase])

  const promote = useCallback(async () => {
    if (!activeThreadId || resumeId === null) return
    setPromoting(true)
    setDossierError('')
    try {
      await promoteLabelThread(apiBase, activeThreadId, resumeId)
      setPicker(false)
      setRevision((n) => n + 1)
    } catch (reason) {
      setDossierError((reason as Error).message || 'Could not track this thread.')
    } finally {
      setPromoting(false)
    }
  }, [apiBase, activeThreadId, resumeId])

  const markRead = useCallback(async () => {
    if (!activeThreadId) return
    try {
      setDossier(await markThreadRead(apiBase, activeThreadId))
      setRevision((n) => n + 1)
    } catch (reason) {
      setDossierError((reason as Error).message || 'Could not mark this conversation read.')
    }
  }, [apiBase, activeThreadId])

  const totalTracked = useMemo(() => labels.reduce((sum, label) => sum + label.thread_count, 0), [labels])

  if (!labelsLoading && !labelsError && labels.length === 0) {
    return (
      <section className="labelsSection">
        <EmptyLabels untrackedLabelCount={untrackedLabelCount} onOpenSettings={onOpenSettings} onSync={onSyncLabels ? sync : undefined} syncing={syncing} />
      </section>
    )
  }

  return (
    <section className="labelsSection">
      <header className="labelsToolbar">
        <div className="labelsToolbarText">
          <p className="labelsToolbarCount">{totalTracked} {totalTracked === 1 ? 'thread' : 'threads'} across {labels.length} {labels.length === 1 ? 'label' : 'labels'}</p>
          <p className="subtle">Everything to and from these recruiters, including replies that started a new thread.</p>
        </div>
        <div className="rowBtns">
          <input
            type="search"
            className="labelsSearch"
            value={query}
            placeholder="Search subject or participant"
            aria-label="Search threads in this label"
            onChange={(event) => setQuery(event.target.value)}
          />
          {onSyncLabels ? (
            <button type="button" className="secondary" disabled={syncing} onClick={() => void sync()}>
              {syncing ? 'Syncing…' : 'Sync from Gmail'}
            </button>
          ) : null}
        </div>
      </header>

      {labelsError ? <p className="errorBanner" role="alert">{labelsError}</p> : null}

      <div className="labelsLayout">
        <nav className="labelRail" aria-label="Tracked Gmail labels">
          {labelsLoading && labels.length === 0
            ? [0, 1, 2].map((key) => <span key={key} className="labelRailSkeleton" aria-hidden="true" />)
            : labels.map((label) => (
              <button
                key={label.external_label_id}
                type="button"
                className={`labelRailItem ${activeLabel === label.name ? 'active' : ''}`}
                aria-current={activeLabel === label.name ? 'true' : undefined}
                onClick={() => setActiveLabel(label.name)}
              >
                <span className="labelRailDot" aria-hidden="true" style={label.color_background ? { background: label.color_background } : undefined} />
                <span className="labelRailName">{label.name}</span>
                {label.unread_count > 0 ? <span className="labelRailUnread" title={`${label.unread_count} unread`}>{label.unread_count}</span> : null}
                <span className="labelRailCount">{label.thread_count}</span>
              </button>
            ))}
        </nav>

        <div className="labelThreadList">
          {threadsError ? <p className="errorBanner" role="alert">{threadsError}</p> : null}
          {threadsLoading && threads.length === 0 ? (
            <p className="subtle">Loading threads…</p>
          ) : threads.length === 0 ? (
            <p className="subtle">
              {query.trim() ? `No thread in ${activeLabel} matches “${query.trim()}”.` : `Nothing captured in ${activeLabel} yet. Sync from Gmail after labelling a thread.`}
            </p>
          ) : (
            threads.map((thread) => (
              <button
                key={thread.thread_id}
                type="button"
                className={`labelThreadItem ${activeThreadId === thread.thread_id ? 'active' : ''} ${thread.unread_count > 0 ? 'unread' : ''}`}
                aria-current={activeThreadId === thread.thread_id ? 'true' : undefined}
                onClick={() => setActiveThreadId(thread.thread_id)}
              >
                <span className="labelThreadTop">
                  <span className="labelThreadRecruiter">{thread.recruiter || thread.recruiter_email || 'Unknown sender'}</span>
                  <time className="labelThreadTime" dateTime={thread.last_message_at}>{shortDate(thread.last_message_at)}</time>
                </span>
                <span className="labelThreadSubject">{thread.subject || '(no subject)'}</span>
                <span className="labelThreadMeta">
                  {thread.unread_count > 0 ? <span className="unreadDot" aria-label={`${thread.unread_count} unread`} /> : null}
                  <span>{thread.message_count} {thread.message_count === 1 ? 'message' : 'messages'}</span>
                  {thread.appts_application_id ? <span className="labelThreadTracked">Tracked #{thread.appts_application_id}</span> : null}
                </span>
              </button>
            ))
          )}
          {threadTotal > PAGE_SIZE ? (
            <footer className="labelThreadPager">
              <button type="button" className="secondary" disabled={threadPage === 1 || threadsLoading} onClick={() => setThreadPage((page) => Math.max(1, page - 1))}>Previous</button>
              <span className="subtle">Page {threadPage} of {Math.ceil(threadTotal / PAGE_SIZE)}</span>
              <button type="button" className="secondary" disabled={threadPage * PAGE_SIZE >= threadTotal || threadsLoading} onClick={() => setThreadPage((page) => page + 1)}>Next</button>
            </footer>
          ) : null}
        </div>

        <div className="labelDossier">
          {dossierError ? <p className="errorBanner" role="alert">{dossierError}</p> : null}
          {!activeThreadId ? (
            <p className="subtle">Pick a thread to see the whole conversation.</p>
          ) : dossierLoading && !dossier ? (
            <p className="subtle">Loading conversation…</p>
          ) : dossier ? (
            <Dossier
              dossier={dossier}
              picker={picker}
              resumes={resumes}
              resumeId={resumeId}
              promoting={promoting}
              onOpenPicker={() => void openPicker()}
              onCancelPicker={() => setPicker(false)}
              onResumeChange={setResumeId}
              onPromote={() => void promote()}
              onMarkRead={() => void markRead()}
            />
          ) : null}
        </div>
      </div>
    </section>
  )
}

type DossierProps = {
  dossier: ThreadDossier
  picker: boolean
  resumes: ResumeAssetOption[]
  resumeId: number | null
  promoting: boolean
  onOpenPicker: () => void
  onCancelPicker: () => void
  onResumeChange: (value: number | null) => void
  onPromote: () => void
  onMarkRead: () => void
}

function Dossier({ dossier, picker, resumes, resumeId, promoting, onOpenPicker, onCancelPicker, onResumeChange, onPromote, onMarkRead }: DossierProps) {
  const recruiters = dossier.contacts.filter((contact) => contact.kind === 'recruiter')
  const others = dossier.contacts.filter((contact) => contact.kind !== 'recruiter' && contact.kind !== 'self')

  return (
    <>
      <header className="labelDossierHeader">
        <div className="labelDossierTitle">
          <h3>{dossier.subject || '(no subject)'}</h3>
          <p className="subtle">
            {dossier.messages.length} {dossier.messages.length === 1 ? 'message' : 'messages'}
            {dossier.thread_count > 1 ? ` across ${dossier.thread_count} threads` : ''}
            {dossier.record_id ? ` · Record ${dossier.record_id}` : ''}
          </p>
          <div className="labelChipRow">
            {dossier.labels.map((label) => <span key={label} className="statusBadge">{label}</span>)}
          </div>
        </div>
        <div className="rowBtns">
          {dossier.unread_count > 0 ? <button type="button" className="secondary" onClick={onMarkRead}>Mark {dossier.unread_count} read</button> : null}
          {dossier.gmail_thread_link ? <a className="linkButton" href={dossier.gmail_thread_link} target="_blank" rel="noreferrer">Open in Gmail</a> : null}
          {dossier.appts_application_id ? (
            <span className="labelThreadTracked">Tracked #{dossier.appts_application_id}</span>
          ) : (
            <button type="button" onClick={onOpenPicker} disabled={picker}>Track as application</button>
          )}
        </div>
      </header>

      {picker ? (
        <div className="resumeLockPicker">
          <label>
            Resume version
            <select aria-label="Resume version" value={resumeId ?? ''} onChange={(event) => onResumeChange(event.target.value ? Number(event.target.value) : null)}>
              <option value="">Choose a resume</option>
              {resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.file_name} (v{resume.version})</option>)}
            </select>
          </label>
          <p className="subtle">{resumes.length ? 'Confirming locks this resume version into the application history.' : 'Enable a resume in Settings before tracking a thread.'}</p>
          <div className="rowBtns">
            <button type="button" disabled={promoting || resumeId === null} onClick={onPromote}>Confirm &amp; track</button>
            <button type="button" className="secondary" disabled={promoting} onClick={onCancelPicker}>Cancel</button>
          </div>
        </div>
      ) : null}

      {recruiters.length ? (
        <section className="labelParticipants" aria-label="People tracked in this conversation">
          <p className="labelParticipantsHeading">Tracked</p>
          <div className="labelChipRow">
            {recruiters.map((contact) => <ContactChip key={contact.address} contact={contact} />)}
          </div>
          {others.length ? (
            <>
              <p className="labelParticipantsHeading">Also on these emails, not tracked</p>
              <div className="labelChipRow">
                {others.map((contact) => <ContactChip key={contact.address} contact={contact} />)}
              </div>
            </>
          ) : null}
        </section>
      ) : null}

      <ol className="labelMessageStream">
        {dossier.messages.map((message, index) => (
          <MessageRow key={message.id} message={message} previous={dossier.messages[index - 1]} />
        ))}
      </ol>
    </>
  )
}

function ContactChip({ contact }: { contact: DossierContact }) {
  const title = contact.kind === 'employer'
    ? `${contact.address} — your employer domain, deliberately excluded from tracking`
    : contact.watched
      ? `${contact.address} — mail to and from this ${contact.address.includes('@') ? 'address' : 'domain'} is tracked`
      : contact.address
  return (
    <span className={`labelContactChip ${contact.kind}`} title={title}>
      <span className="labelContactName">{contact.name}</span>
      <span className="labelContactAddress">{contact.address}</span>
      <span className="labelContactCount">{contact.message_count}</span>
    </span>
  )
}

function MessageRow({ message, previous }: { message: DossierMessage; previous?: DossierMessage }) {
  const outbound = message.direction === 'outbound'
  // A separator only where the thread actually changes, so the common case -
  // one thread - carries no extra chrome at all.
  const newThread = previous != null && previous.external_thread_id !== message.external_thread_id
  return (
    <>
      {newThread ? (
        <li className="labelThreadBreak" aria-hidden="false">
          <span>Separate thread · {message.subject || '(no subject)'}</span>
        </li>
      ) : null}
      <li className={`labelMessage ${outbound ? 'outbound' : 'inbound'}`}>
        <div className="labelMessageMeta">
          <span className="labelMessageSender">{outbound ? 'You' : message.sender}</span>
          <time dateTime={message.occurred_at}>{new Date(message.occurred_at).toLocaleString()}</time>
          {message.origin === 'watch' ? <span className="labelMessageOrigin">follow-up</span> : null}
          {!outbound && message.read_at === null ? <span className="unreadDot" aria-label="Unread" /> : null}
        </div>
        <p className="labelMessageBody">{message.body || message.snippet}</p>
        {message.gmail_link ? <a className="labelMessageLink" href={message.gmail_link} target="_blank" rel="noreferrer">Open in Gmail</a> : null}
      </li>
    </>
  )
}

function EmptyLabels({ untrackedLabelCount, onOpenSettings, onSync, syncing }: { untrackedLabelCount: number; onOpenSettings?: () => void; onSync?: () => void; syncing: boolean }) {
  const hasLabelsToTrack = untrackedLabelCount > 0
  return (
    <div className="labelsEmpty">
      <h3>{hasLabelsToTrack ? 'Choose which Gmail labels to follow' : 'No Gmail labels yet'}</h3>
      <p>
        {hasLabelsToTrack
          ? `You have ${untrackedLabelCount} ${untrackedLabelCount === 1 ? 'label' : 'labels'} in Gmail. Pick the ones that mean something — “RTR Requested”, “Submitted” — and every thread you file there shows up here, along with later mail from the same recruiter or company.`
          : 'Label a thread in Gmail — “RTR Requested”, say — then sync. Every thread you file under that label appears here with the full back-and-forth, including replies that start a new thread.'}
      </p>
      <div className="rowBtns">
        {onOpenSettings ? <button type="button" onClick={onOpenSettings}>{hasLabelsToTrack ? 'Choose labels' : 'Open Settings'}</button> : null}
        {onSync ? <button type="button" className="secondary" disabled={syncing} onClick={onSync}>{syncing ? 'Syncing…' : 'Sync from Gmail'}</button> : null}
      </div>
    </div>
  )
}

function shortDate(value: string): string {
  const date = new Date(value)
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  return sameDay
    ? date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
    : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
