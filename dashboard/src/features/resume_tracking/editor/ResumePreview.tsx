import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { fetchResumeDraftExport } from '../api'
import { safeHref } from '../../chat/markdown'
import type { ResumeFormatSpec } from '../types'
import usePagination from './usePagination'

export const DEFAULT_SPEC: ResumeFormatSpec = {
  page_size: 'LETTER', layout: 'single', accent_color: '', section_spacing_pt: 0, compact: false,
  sidebar_sections: [], sidebar_width_inches: 2.2,
  font_family: 'Arial', body_font_size: 10, name_font_size: 14, heading_font_size: 10,
  heading_bold: true, heading_uppercase: false,
  margin_left_inches: 0.25, margin_right_inches: 0.5, margin_top_inches: 0.5, margin_bottom_inches: 0.5,
  line_spacing: 1, justify_body: true, bullet_indent_inches: 0.5, bullet_hanging_inches: 0.25,
  rule_before_sections: ['Summary', 'Certifications', 'Skills', 'Experiences', 'Education Details'],
  heading_space_before_pt: 4, heading_space_after_pt: 2,
  skills_divider_inches: 3, skills_category_bold: true, skills_row_gap_pt: 6, environment_gap_pt: 8,
}

function inline(text: string) {
  return text.split(/(\[[^\]]+\]\([^)\s]*\)|\*\*.+?\*\*)/).map((part, index) => {
    const link = part.match(/^\[([^\]]+)\]\(([^)\s]*)\)$/)
    if (link) {
      const href = /^mailto:[^\s<>]+$/i.test(link[2]) ? link[2] : safeHref(link[2])
      return href ? <a key={index} href={href} target="_blank" rel="noopener noreferrer">{link[1]}</a> : part
    }
    return part.startsWith('**') && part.endsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong> : part
  })
}

export function resumeBlocks(markdown: string, spec: ResumeFormatSpec, expectHeader = true, available?: number): ReactNode[] {
  // The skills divider is a ruler position measured on a full-width page. Used
  // as-is inside a 2.4in sidebar it is wider than the column, and every cell
  // wraps to one word per line - a 1,866px column beside a 14,172px one. Scaled
  // to the same proportion instead, which is what `_column_widths` does server
  // side for the .docx.
  const pageWidth = spec.page_size === 'A4' ? 210 / 25.4 : 8.5
  const usableWidth = pageWidth - spec.margin_left_inches - spec.margin_right_inches
  const dividerInches = spec.skills_divider_inches * (available == null ? 1 : available / usableWidth)
  const blocks: ReactNode[] = []
  const spacing = (points: number) => points * (spec.compact ? 0.5 : 1)
  const lines = markdown.replace(/\r\n/g, '\n').split('\n')
  const sectionLevel = Math.min(...lines.flatMap((line) => line.match(/^(#{1,6})\s+/)?.[1].length ?? []).slice(1), 6)
  // Off for a column: the name and contact line are drawn once above both, so
  // the first heading a column sees is a section heading, not the name.
  let nameSeen = !expectHeader
  let contactSeen = !expectHeader
  let role: ReactNode[] | null = null
  const flushRole = () => { if (role) blocks.push(<div className="resume-item" key={`role-${blocks.length}`}>{role}</div>); role = null }
  const add = (node: ReactNode) => { if (role) role.push(node); else blocks.push(node) }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue
    if (/^\|.+\|$/.test(line)) {
      const rows: string[][] = []
      const start = i
      while (i < lines.length && /^\|.+\|$/.test(lines[i].trim())) {
        if (!/^\|[\s:|\-]+\|$/.test(lines[i].trim())) rows.push(lines[i].trim().slice(1, -1).split('|').map((cell) => cell.trim()))
        i++
      }
      i--
      const columns = Math.max(...rows.map((row) => row.length), 1)
      rows.forEach((row, r) => add(<div key={`${start}-${r}`} className="resumeSkillsRow resume-item" role="row"
        style={{ gridTemplateColumns: columns === 2 ? `${dividerInches}in 1fr` : `repeat(${columns}, 1fr)`, paddingBottom: r > 0 && r < rows.length - 1 ? `${spacing(spec.skills_row_gap_pt)}pt` : 0 }}>
        {Array.from({ length: columns }, (_, c) => <span role="cell" key={c} style={{ fontWeight: c === 0 && spec.skills_category_bold ? 'bold' : 'normal' }}>{inline(row[c] ?? '')}</span>)}
      </div>))
      continue
    }
    if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(line)) { add(<hr key={i} />); continue }
    const heading = line.match(/^(#{1,6})\s+(.*)$/)
    if (heading) {
      flushRole()
      const isName = !nameSeen
      nameSeen = true
      if (!isName && heading[1].length > sectionLevel) role = []
      const ruled = !isName && spec.rule_before_sections.some((value) => value.trim().toLowerCase() === heading[2].trim().toLowerCase())
      add(<div key={i} data-heading data-resume-heading={heading[2]} data-line={i}
        className={isName ? 'resumeName' : 'resumeHeading'} style={{
          textAlign: isName ? 'center' : 'left', fontSize: `${isName ? spec.name_font_size : spec.heading_font_size}pt`,
          fontWeight: isName || spec.heading_bold ? 'bold' : 'normal',
          textTransform: !isName && spec.heading_uppercase ? 'uppercase' : undefined,
          color: !isName && spec.accent_color ? spec.accent_color : undefined,
          marginTop: isName ? 0 : `${spacing(spec.heading_space_before_pt + (spec.section_spacing_pt ?? 0))}pt`, marginBottom: isName ? 0 : `${spacing(spec.heading_space_after_pt)}pt`,
          borderTop: ruled ? '1px solid currentColor' : undefined,
        }}>{inline(heading[2])}</div>)
      continue
    }
    const bullet = line.match(/^[-*+]\s+(.*)$/)
    const contact = !bullet && nameSeen && !contactSeen
    if (contact) contactSeen = true
    add(<p key={i} className={`resume-item${contact ? ' resumeContact' : ''}`} style={{
      textAlign: contact ? 'center' : spec.justify_body ? 'justify' : 'left',
      paddingLeft: bullet ? `${spec.bullet_indent_inches}in` : undefined,
      textIndent: bullet ? `-${spec.bullet_hanging_inches}in` : undefined,
      marginBottom: !contact && /^environment:/i.test(line) ? `${spacing(spec.environment_gap_pt)}pt` : 0,
    }}>{bullet ? '• ' : ''}{inline(bullet ? bullet[1] : line)}</p>)
  }
  flushRole()
  return blocks
}

/** Track how much room the preview has, so a fixed-width page can be fitted to it.
 *
 * A resume page is 8.5in wide and does not negotiate: at 96dpi that is 816px.
 * Left alone it sets the min-content width of its grid track and pushes the
 * whole builder into a horizontal scrollbar.
 *
 * Fitting is not free, though - fitted into a third of the builder it lands
 * near 38%, which is a thumbnail rather than a preview. So the fit ratio is
 * returned rather than applied, and the caller decides between fitting and
 * showing the page at full size with a scrollbar.
 */
function useFitScale(naturalWidth: number) {
  const ref = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  useEffect(() => {
    const element = ref.current
    // jsdom has no ResizeObserver, and the unscaled default is what the tests
    // assert against, so an absent observer degrades to 1 rather than throwing.
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => {
      const available = entry.contentRect.width
      if (available > 0) setScale(Math.min(1, available / naturalWidth))
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [naturalWidth])
  return { fitRef: ref, scale }
}

/** Readable at a glance. Below this the page is a shape, not something you read. */
const LEGIBLE_SCALE = 0.62

/** Matches COLUMN_GUTTER_INCHES in resume_render_service.py. */
const COLUMN_GUTTER_INCHES = 0.2

/**
 * Cut the document into header, main column and sidebar.
 *
 * Mirrors `split_for_layout` in resume_render_service.py, and has to keep
 * mirroring it: the two produce the same document, one for the screen and one
 * for Word, and a preview that put Skills in a different column than the
 * download would be worse than no preview.
 */
export function splitForLayout(markdown: string, spec: ResumeFormatSpec) {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n')
  const headings = lines.map((line, index) => ({ match: line.match(/^(#{1,6})\s+(.*)$/), index }))
    .filter((item) => item.match && item.match[2].trim())
  // The first heading is the name; the sections are the shallowest below it.
  const level = headings.length > 1 ? Math.min(...headings.slice(1).map((item) => item.match![1].length)) : 0
  const starts = headings.filter((item) => item.match![1].length === level)
  if (!level || !starts.length) return { header: markdown, main: '', sidebar: '' }

  const wanted = new Set((spec.sidebar_sections ?? []).map((name) => name.trim().toLowerCase()).filter(Boolean))
  const header = lines.slice(0, starts[0].index).join('\n')
  const main: string[] = []
  const sidebar: string[] = []
  starts.forEach((item, position) => {
    const end = position + 1 < starts.length ? starts[position + 1].index : lines.length
    const block = lines.slice(item.index, end)
    ;(wanted.has(item.match![2].trim().toLowerCase()) ? sidebar : main).push(...block)
  })
  return { header, main: main.join('\n'), sidebar: sidebar.join('\n') }
}

type PreviewProps = {
  markdown: string
  spec: ResumeFormatSpec
  /** True while the preview owns a full-width row under the editor. */
  wide?: boolean
  onToggleWide?: () => void
}

export default function ResumePreview({ markdown, spec, wide = false, onToggleWide }: PreviewProps) {
  const width = spec.page_size === 'A4' ? 210 / 25.4 : 8.5
  const twoColumn = spec.layout === 'two-column' && (spec.sidebar_sections ?? []).some((name) => name.trim())
  const blocks = useMemo(() => {
    if (!twoColumn) return resumeBlocks(markdown, spec)
    const { header, main, sidebar } = splitForLayout(markdown, spec)
    const sidebarWidth = spec.sidebar_width_inches ?? 2.2
    const mainWidth = width - spec.margin_left_inches - spec.margin_right_inches - sidebarWidth - COLUMN_GUTTER_INCHES
    // One grid, sliced by the pager exactly like a single stream. Cutting both
    // columns at the same height is also what Word does to a table row that
    // runs past the bottom of a page, so the approximation matches the output.
    return [
      ...resumeBlocks(header, spec),
      <div className="resumeColumns" key="columns"
        style={{ gridTemplateColumns: `${mainWidth}in ${sidebarWidth}in`, gap: `0 ${COLUMN_GUTTER_INCHES}in` }}>
        <div>{resumeBlocks(main, spec, false, mainWidth)}</div>
        <div>{resumeBlocks(sidebar, spec, false, sidebarWidth)}</div>
      </div>,
    ]
  }, [markdown, spec, twoColumn, width])
  const height = spec.page_size === 'A4' ? 297 / 25.4 : 11
  const usableHeight = (height - spec.margin_top_inches - spec.margin_bottom_inches) * 96
  const { measureRef, pages } = usePagination(markdown + JSON.stringify(spec), usableHeight)
  const contentStyle: CSSProperties = { width: `${width - spec.margin_left_inches - spec.margin_right_inches}in`, fontFamily: spec.font_family,
    fontSize: `${spec.body_font_size}pt`, lineHeight: spec.line_spacing }
  const naturalWidth = width * 96
  const { fitRef, scale: fitScale } = useFitScale(naturalWidth)
  const [zoom, setZoom] = useState<'fit' | 'full'>('fit')
  const scale = zoom === 'full' ? 1 : fitScale
  // The stage is transformed, and a transform does not change the space an
  // element reserves. Its height is set back to what the eye sees, so the panel
  // below the preview sits under the last page rather than under a phantom one.
  const stageHeight = pages.length * (height * 96 + 16) * scale
  // Only worth suggesting when the column is the thing making it small, and
  // only while it still is: once the preview is wide the prompt is noise.
  const cramped = zoom === 'fit' && fitScale < LEGIBLE_SCALE && !wide

  return <section className="resumePreview" aria-label="Approximate layout">
    <div className="resumePreviewHead">
      <strong>Approximate layout</strong>
      <div className="resumePreviewTools">
        <span className="subtle">{pages.length} page{pages.length === 1 ? '' : 's'} · {Math.round(scale * 100)}%</span>
        <div className="resumeZoom" role="group" aria-label="Preview zoom">
          <button type="button" aria-pressed={zoom === 'fit'} onClick={() => setZoom('fit')}>Fit</button>
          <button type="button" aria-pressed={zoom === 'full'} onClick={() => setZoom('full')}>100%</button>
        </div>
        {onToggleWide ? (
          <button type="button" className="resumePreviewWiden" aria-pressed={wide} onClick={onToggleWide}>
            {wide ? 'Side by side' : 'Widen'}
          </button>
        ) : null}
      </div>
    </div>
    <p className="subtle">
      Word and LibreOffice decide final page breaks. Justified text may wrap differently.
      {cramped ? <> This column is too narrow to read the page — <strong>Widen</strong> puts it under the editor at full size.</> : null}
    </p>
    <div className={`resumePreviewScroll${zoom === 'full' ? ' zoomed' : ''}`} ref={fitRef}>
      <div className="resumeMeasure resumePreviewContent" ref={measureRef} aria-hidden="true" inert style={contentStyle}>{blocks}</div>
      <div className="resumePreviewStage" style={{ width: `${naturalWidth}px`, height: `${stageHeight}px`, transform: `scale(${scale})` }}>
        {pages.map((page, i) => <div className="resumePage" key={i} style={{ width: `${width}in`, height: `${height}in`,
          padding: `${spec.margin_top_inches}in ${spec.margin_right_inches}in ${spec.margin_bottom_inches}in ${spec.margin_left_inches}in` }}>
          <div style={{ height: `${page.end - page.start}px`, overflow: 'hidden' }}>
            <div className="resumePreviewContent" style={{ ...contentStyle, transform: `translateY(-${page.start}px)` }}>{blocks}</div>
          </div>
          <small className="resumePageNumber">{i + 1} / {pages.length}</small>
        </div>)}
      </div>
    </div>
  </section>
}

export function ExactPreview({ apiBase, draftId, profileId }: { apiBase: string; draftId: number; profileId: number | null }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  const lastRequest = useRef(0)
  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => () => { if (url) URL.revokeObjectURL(url) }, [url])
  const render = async () => {
    if (request.current || Date.now() - lastRequest.current < 1000) return
    lastRequest.current = Date.now()
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError('')
    try {
      const response = await fetchResumeDraftExport(apiBase, draftId, 'pdf', profileId, controller.signal)
      const blob = await response.blob()
      if (!controller.signal.aborted) setUrl(URL.createObjectURL(blob))
    } catch (reason) {
      if (!controller.signal.aborted) setError((reason as Error).message)
    } finally {
      request.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }
  return <div className="resumeExactPreview">
    <button type="button" disabled={busy || !!url} onClick={render}>{busy ? 'Rendering exact preview...' : 'Exact preview'}</button>
    {error ? <p role="alert" className="errorText">{error}</p> : null}
    {url ? <><p>Exact — rendered from the Word file</p><iframe title="Exact resume PDF preview" src={url} />
      <button type="button" onClick={() => setUrl('')}>Close exact preview</button></> : null}
  </div>
}
