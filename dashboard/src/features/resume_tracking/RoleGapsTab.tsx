import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { getRoleGaps, getRoleTarget } from './api'
import { formatVariantLabel } from './resumeDisplay'
import type { RoleGapGroup, RoleGapReport, RoleTargetReport } from './types'

type Props = { apiBase: string }

const FAMILY_LABELS: Record<string, string> = {
  java_backend: 'Java Backend',
  java_fullstack: 'Java Full Stack',
  ai: 'AI / GenAI',
  devops_cloud: 'DevOps & Cloud',
  frontend: 'Frontend',
  data: 'Data Engineering',
  general: 'Unclassified',
}

const WINDOWS = [30, 90, 180, 365]

// The roles worth asking about are the ones the inbox sees rarely, and 90 days of a rare
// role is not a cohort. The target lookup always reaches back at least this far, whatever
// window the aggregate below is using.
const TARGET_MIN_WINDOW = 365

const familyLabel = (family: string) => FAMILY_LABELS[family] ?? family.replaceAll('_', ' ')

/**
 * Turn the two scores into the sentence the user actually needs.
 *
 * A high role fit with a failing mandatory gate means the resume is aimed at the
 * right job and is only missing named tech - a variant worth building. A low role
 * fit means no resume in the library is even the right shape for these postings.
 */
function verdict(group: RoleGapGroup): { tone: 'close' | 'wrong' | 'ok'; text: string } {
  if (!group.flagged_count) return { tone: 'ok', text: 'Your library already covers these postings.' }
  const fit = group.median_role_fit ?? 0
  if (fit >= 0.75) {
    return {
      tone: 'close',
      text: 'The role is a strong match — only the named tools are missing. A focused variant should win these.',
    }
  }
  return {
    tone: 'wrong',
    text: 'No resume in your library is the right shape for these postings — this needs a new resume, not an edit.',
  }
}

/**
 * Coerce whatever the API returned into a shape this component can render.
 *
 * A partial or unexpected payload should degrade to an empty report, not blank the
 * whole page with a render-time TypeError.
 */
function normalise(payload: Partial<RoleGapReport> | null | undefined, fallbackWindow: number): RoleGapReport {
  return {
    window_days: Number(payload?.window_days) || fallbackWindow,
    analysed_jds: Number(payload?.analysed_jds) || 0,
    groups: Array.isArray(payload?.groups) ? payload.groups : [],
  }
}

function normaliseTarget(payload: Partial<RoleTargetReport> | null | undefined, role: string): RoleTargetReport {
  return {
    target_role: payload?.target_role || role,
    window_days: Number(payload?.window_days) || TARGET_MIN_WINDOW,
    cohort_size: Number(payload?.cohort_size) || 0,
    evidence_tier: payload?.evidence_tier || 'none',
    demanded_skills: Array.isArray(payload?.demanded_skills) ? payload.demanded_skills : [],
    variants: Array.isArray(payload?.variants) ? payload.variants : [],
    closest_variant_code: payload?.closest_variant_code || '',
    closest_variant_label: payload?.closest_variant_label || '',
    verdict_tone: payload?.verdict_tone || 'ok',
    verdict: payload?.verdict || '',
    sample_jds: Array.isArray(payload?.sample_jds) ? payload.sample_jds : [],
    narrative: payload?.narrative ?? null,
  }
}

/** The server picks the tone; this only guarantees it maps onto a class that exists. */
const toneClass = (tone: string) => (tone === 'close' || tone === 'wrong' ? tone : 'ok')

/**
 * One role, answered three ways: why the library is not close, what is missing, and
 * which keywords would close it.
 *
 * Everything rendered here came out of the user's own job descriptions with a count
 * behind it. Nothing is generated, so a role the inbox has never seen shows the verdict
 * alone rather than a plausible-looking list of skills nobody actually asked for.
 */
function RoleTargetCard({ report, open, onToggle }: { report: RoleTargetReport; open: boolean; onToggle: () => void }) {
  const tone = toneClass(report.verdict_tone)
  const closest = report.variants[0] ?? null
  const missing = report.demanded_skills.filter((item) => !item.covered_by_closest)
  const covered = report.demanded_skills.filter((item) => item.covered_by_closest)

  return (
    <article className={`roleGapCard roleTargetCard ${tone}`}>
      <header className="roleGapCardHead">
        <div className="roleGapCardTitle">
          <h3>{report.target_role}</h3>
          <p className="subtle roleGapTitles">
            {report.evidence_tier === 'none'
              ? `Nothing like it in the last ${report.window_days} days`
              : `Matched against ${report.cohort_size.toLocaleString()} job descriptions from the last ${report.window_days} days`}
          </p>
        </div>
        {closest ? (
          <div className="roleGapCardStat">
            <span className="roleGapBig">{Math.round(closest.coverage * 100)}%</span>
            <span className="roleGapStatLabel">covered by your closest resume</span>
          </div>
        ) : null}
      </header>

      <p className={`roleGapVerdict ${tone}`}>{report.verdict}</p>
      {report.narrative ? <p className="roleTargetNarrative">{report.narrative}</p> : null}

      {missing.length ? (
        <div className="roleGapBuild">
          <h4>Add these to your resume</h4>
          <div className="roleGapChips">
            {missing.map((item) => (
              <span className="roleGapChip" key={item.skill} title={`Asked for by ${item.jd_count} of these job descriptions`}>
                {item.skill}<span className="roleGapChipCount">{item.jd_count}</span>
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {covered.length ? (
        <div className="roleGapBuild">
          <h4>Already there{report.closest_variant_label ? ` on ${formatVariantLabel(report.closest_variant_label)}` : ''}</h4>
          <div className="roleGapChips">
            {covered.map((item) => (
              <span className="roleGapChip covered" key={item.skill} title={`Asked for by ${item.jd_count} of these job descriptions`}>
                {item.skill}<span className="roleGapChipCount">{item.jd_count}</span>
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <footer className="roleGapFoot">
        <span className="subtle">
          Closest today: <strong>{report.closest_variant_code || '—'}</strong>
          {report.closest_variant_label ? ` · ${formatVariantLabel(report.closest_variant_label)}` : ''}
        </span>
        {report.sample_jds.length ? (
          <button type="button" className="linkButton" onClick={onToggle} aria-expanded={open}>
            {open ? 'Hide the postings' : `Show ${report.sample_jds.length} of the postings`}
          </button>
        ) : null}
      </footer>

      {open ? (
        <ul className="roleGapSamples">
          {report.sample_jds.map((sample) => (
            <li key={sample.email_id}>
              <strong>{sample.role || 'Untitled role'}</strong>
              {/* Which pass admitted this posting: the recruiter used the words, or the
                  skills lined up. Without it the cohort is a black box. */}
              <span className="subtle">
                {sample.match_reason === 'title' ? ' — matched on title' : ' — matched on skills'}
                {sample.skills ? ` · ${sample.skills}` : ''}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  )
}

export default function RoleGapsTab({ apiBase }: Props) {
  const [report, setReport] = useState<RoleGapReport | null>(null)
  const [windowDays, setWindowDays] = useState(90)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [target, setTarget] = useState('')
  const [targetReport, setTargetReport] = useState<RoleTargetReport | null>(null)
  const [targetError, setTargetError] = useState('')
  const [targetLoading, setTargetLoading] = useState(false)
  const [targetSamplesOpen, setTargetSamplesOpen] = useState(false)

  const analyseTarget = (event: FormEvent) => {
    event.preventDefault()
    const role = target.trim()
    if (role.length < 2) return
    setTargetLoading(true)
    setTargetError('')
    setTargetSamplesOpen(false)
    getRoleTarget(apiBase, role, Math.max(windowDays, TARGET_MIN_WINDOW))
      .then((payload) => setTargetReport(normaliseTarget(payload, role)))
      .catch((reason) => {
        setTargetReport(null)
        setTargetError((reason as Error).message)
      })
      .finally(() => setTargetLoading(false))
  }

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    getRoleGaps(apiBase, windowDays)
      .then((payload) => setReport(normalise(payload, windowDays)))
      .catch((reason) => setError((reason as Error).message))
      .finally(() => setLoading(false))
  }, [apiBase, windowDays])

  useEffect(() => { load() }, [load])

  const groups = report?.groups ?? []

  return (
    <section className="roleGapPanel">
      <header className="roleGapIntro">
        <div>
          <h2>What your resumes are missing</h2>
          <p className="subtle">
            Every job description that came through was scored against your resume library. These are the
            roles it keeps failing, and the skills that would fix them — ranked by how specific each skill
            is to that role, not just how often it appears.
          </p>
        </div>
        <label className="roleGapWindow">
          Window
          <select value={windowDays} onChange={(event) => setWindowDays(Number(event.target.value))}>
            {WINDOWS.map((days) => <option key={days} value={days}>Last {days} days</option>)}
          </select>
        </label>
      </header>

      <form className="roleTargetAsk" onSubmit={analyseTarget}>
        <label htmlFor="roleTargetInput">
          Thinking of applying for a role?
          <span className="subtle"> Type it — it does not have to be one of the groups below.</span>
        </label>
        <div className="roleTargetAskRow">
          <input
            id="roleTargetInput"
            type="text"
            value={target}
            placeholder="IT Security Auditor"
            maxLength={120}
            onChange={(event) => setTarget(event.target.value)}
          />
          <button type="submit" className="primaryButton" disabled={targetLoading || target.trim().length < 2}>
            {targetLoading ? 'Checking...' : 'Check my resumes'}
          </button>
        </div>
        <p className="subtle roleTargetHint">
          Answered from the job descriptions already in your inbox, looking back at least a year —
          rare roles need the history.
        </p>
      </form>

      {targetError ? <p className="errorText" role="alert">{targetError}</p> : null}
      {targetReport ? <RoleTargetCard report={targetReport} open={targetSamplesOpen} onToggle={() => setTargetSamplesOpen((value) => !value)} /> : null}

      {error ? <p className="errorText" role="alert">{error}</p> : null}
      {loading ? <p className="subtle">Analysing job descriptions...</p> : null}

      {!loading && report ? (
        <p className="roleGapCount">
          Analysed <strong>{report.analysed_jds.toLocaleString()}</strong> scored job descriptions
          from the last {report.window_days} days.
        </p>
      ) : null}

      {!loading && !groups.length && report ? (
        <p className="subtle">
          Not enough scored job descriptions yet in this window. Widen the window, or let a few more
          sync runs complete.
        </p>
      ) : null}

      <div className="roleGapList">
        {groups.map((group) => {
          const { tone, text } = verdict(group)
          const isOpen = expanded === group.role_family
          const pct = Math.round(group.flagged_share * 100)
          return (
            <article className={`roleGapCard ${tone}`} key={group.role_family}>
              <header className="roleGapCardHead">
                <div className="roleGapCardTitle">
                  <h3>{familyLabel(group.role_family)}</h3>
                  {group.top_titles.length ? (
                    <p className="subtle roleGapTitles">{group.top_titles.slice(0, 3).join(' · ')}</p>
                  ) : null}
                </div>
                <div className="roleGapCardStat">
                  <span className="roleGapBig">{group.flagged_count.toLocaleString()}</span>
                  <span className="roleGapStatLabel">of {group.jd_count.toLocaleString()} need a new resume</span>
                </div>
              </header>

              <div className="roleGapBar" aria-hidden="true">
                <span className="roleGapBarFill" style={{ width: `${pct}%` }} />
              </div>

              <p className={`roleGapVerdict ${tone}`}>{text}</p>

              {group.missing_skills.length ? (
                <div className="roleGapBuild">
                  <h4>Build a resume with</h4>
                  <div className="roleGapChips">
                    {group.missing_skills.map((item) => (
                      <span className="roleGapChip" key={item.skill} title={`Missing from ${item.jd_count} job descriptions`}>
                        {item.skill}<span className="roleGapChipCount">{item.jd_count}</span>
                      </span>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="subtle">No consistent skill pattern — these postings are too varied to generalise.</p>
              )}

              <footer className="roleGapFoot">
                <span className="subtle">
                  Closest today: <strong>{group.closest_variant_code || '—'}</strong>
                  {group.closest_variant_label ? ` · ${formatVariantLabel(group.closest_variant_label)}` : ''}
                </span>
                {group.sample_jds.length ? (
                  <button type="button" className="linkButton" onClick={() => setExpanded(isOpen ? null : group.role_family)} aria-expanded={isOpen}>
                    {isOpen ? 'Hide examples' : `Show ${group.sample_jds.length} examples`}
                  </button>
                ) : null}
              </footer>

              {isOpen ? (
                <ul className="roleGapSamples">
                  {group.sample_jds.map((sample) => (
                    <li key={sample.email_id}>
                      <strong>{sample.role || 'Untitled role'}</strong>
                      {sample.missing_skills.length ? (
                        <span className="subtle"> — missing {sample.missing_skills.join(', ')}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </article>
          )
        })}
      </div>
    </section>
  )
}
