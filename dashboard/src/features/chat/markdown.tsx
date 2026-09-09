import type { ReactNode } from 'react'


const LINK = /^\[([^\]]+)\]\(([^)\s]*)\)$/

// Assistant output quotes recruiter email bodies and untrusted web results, so
// an href here is attacker-reachable text. Only http(s) and same-origin
// relative paths become links; everything else - javascript:, data:, and
// protocol-relative //host or /\host - renders as the literal markdown it was.
export function safeHref(raw: string): string | null {
  const value = raw.trim()
  if (!value) return null
  if (/^https?:\/\//i.test(value)) return value
  if (!'/?#'.includes(value[0])) return null
  // `//host` and `/\host` are protocol-relative: they leave the origin, so they
  // are not relative paths at all.
  if (value[0] === '/' && (value[1] === '/' || value[1] === '\\')) return null
  return value
}

// The record IDs this thread can back with evidence, and what to do with one.
// Optional throughout: with no citations the renderer behaves exactly as before,
// which is what keeps every other caller and every existing test unchanged.
export type Citations = {
  ids: Map<string, number>
  onSelect: (candidateId: number) => void
}

function escapeForRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// Matched against the known IDs rather than against an ID *shape*. A UUID
// pattern would go stale the day the format changes and would also light up
// identifiers this thread never fetched; splitting on the actual keys cannot do
// either, and it is what makes an unbacked ID render as plain text.
function linkRecordIds(chunk: string, citations: Citations | undefined, keyPrefix: number) {
  if (!citations?.ids.size) return chunk
  const known = [...citations.ids.keys()]
    .filter((id) => chunk.includes(id))
    // Longest first, so an ID that contains a shorter one is not split by it.
    .sort((left, right) => right.length - left.length)
  if (!known.length) return chunk
  const pattern = new RegExp(`(${known.map(escapeForRegex).join('|')})`, 'g')
  return chunk.split(pattern).map((part, index) => {
    const candidateId = citations.ids.get(part)
    if (candidateId === undefined) return part
    return (
      <button
        key={`${keyPrefix}:${index}`}
        type="button"
        className="chatRecordCitation"
        onClick={() => citations.onSelect(candidateId)}
        title="Open this record"
      >
        {part}
      </button>
    )
  })
}

function renderInline(line: string, citations?: Citations) {
  return line.split(/(\[[^\]]+\]\([^)\s]*\)|`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/).map((chunk, i) => {
    const link = chunk.match(LINK)
    if (link) {
      const href = safeHref(link[2])
      if (!href) return chunk
      const external = /^https?:/i.test(href)
      return (
        <a key={i} href={href} {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
          {link[1]}
        </a>
      )
    }
    // Inline code is left alone: `record-1` inside backticks is being shown as a
    // literal, and turning it into a control would contradict that.
    if (chunk.startsWith('`') && chunk.endsWith('`') && chunk.length > 1) return <code key={i}>{chunk.slice(1, -1)}</code>
    if (chunk.startsWith('**') && chunk.endsWith('**')) return <strong key={i}>{chunk.slice(2, -2)}</strong>
    if (chunk.startsWith('*') && chunk.endsWith('*') && chunk.length > 1) return <em key={i}>{chunk.slice(1, -1)}</em>
    return <span key={i}>{linkRecordIds(chunk, citations, i)}</span>
  })
}

const TABLE_SEPARATOR_ROW = /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$/

function splitTableRow(line: string): string[] {
  return line.replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
}

// ponytail: headings/bold/italic/bullets/numbered lists/tables/inline code/links only, not full markdown. Swap for a real parser if fenced code blocks show up.
export function renderMarkdownLite(text: string, citations?: Citations) {
  const blocks: ReactNode[] = []
  let paragraph: string[] = []
  let list: string[] = []
  let listOrdered = false

  const flushParagraph = () => {
    if (!paragraph.length) return
    blocks.push(
      <p key={blocks.length}>
        {paragraph.map((line, i) => (
          <span key={i}>
            {renderInline(line, citations)}
            {i < paragraph.length - 1 ? <br /> : null}
          </span>
        ))}
      </p>,
    )
    paragraph = []
  }
  const flushList = () => {
    if (!list.length) return
    const ListTag = listOrdered ? 'ol' : 'ul'
    blocks.push(
      <ListTag key={blocks.length}>
        {list.map((line, i) => (
          <li key={i}>{renderInline(line, citations)}</li>
        ))}
      </ListTag>,
    )
    list = []
  }

  const lines = text.split('\n')
  // Headings render relative to the shallowest one in this message, so a body
  // that starts at "###" still emits <h3> instead of jumping straight to <h6>
  // and tripping the heading-order audit. Each bubble sits under a visually
  // hidden <h2>, so h3 is always the correct first step down.
  const shallowestHeading = lines.reduce((shallowest, raw) => {
    const match = raw.trim().match(/^(#{1,6})\s+/)
    return match ? Math.min(shallowest, match[1].length) : shallowest
  }, 6)
  let i = 0
  while (i < lines.length) {
    const line = lines[i].trim()
    if (!line) {
      flushParagraph()
      flushList()
      i += 1
      continue
    }
    const nextLine = (lines[i + 1] ?? '').trim()
    const isTable = line.startsWith('|') && line.endsWith('|') && TABLE_SEPARATOR_ROW.test(nextLine)
    if (isTable) {
      flushParagraph()
      flushList()
      const header = splitTableRow(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitTableRow(lines[i].trim()))
        i += 1
      }
      blocks.push(
        <table key={blocks.length}>
          <thead>
            <tr>{header.map((cell, c) => <th key={c}>{renderInline(cell, citations)}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>{row.map((cell, c) => <td key={c}>{renderInline(cell, citations)}</td>)}</tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.*)/)
    const orderedItem = line.match(/^\d+\.\s+(.*)/)
    const bulletItem = line.match(/^[-*]\s+(.*)/)
    if (heading) {
      flushParagraph()
      flushList()
      const depth = Math.min(heading[1].length - shallowestHeading, 2)
      const HeadingTag = (['h3', 'h4', 'h5'] as const)[depth]
      const headingContent = renderInline(heading[2], citations)
      blocks.push(<HeadingTag key={blocks.length}>{headingContent}</HeadingTag>)
    } else if (orderedItem || bulletItem) {
      flushParagraph()
      const ordered = Boolean(orderedItem)
      if (list.length && listOrdered !== ordered) flushList()
      listOrdered = ordered
      list.push(ordered ? orderedItem![1] : bulletItem![1])
    } else {
      flushList()
      paragraph.push(line)
    }
    i += 1
  }
  flushParagraph()
  flushList()
  return blocks
}
