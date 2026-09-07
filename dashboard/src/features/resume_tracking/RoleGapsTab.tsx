import { useCallback, useEffect, useState } from 'react'
import { getRoleGaps } from './api'
import type { RoleGapGroup, RoleGapReport } from './types'

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

export default function RoleGapsTab({ apiBase }: Props) {
  const [report, setReport] = useState<RoleGapReport | null>(null)
  const [windowDays, setWindowDays] = useState(90)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

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
                  {group.closest_variant_label ? ` · ${group.closest_variant_label}` : ''}
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
